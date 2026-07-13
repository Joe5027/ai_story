from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.models.models import ModelProvider, ModelUsageLog
from apps.projects.models import Project


User = get_user_model()


class ModelUsageLogIsolationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('ledger-owner', password='secret123')
        self.other = User.objects.create_user('ledger-other', password='secret123')
        self.staff = User.objects.create_user(
            'ledger-staff', password='secret123', is_staff=True
        )
        self.owner_project = Project.objects.create(
            user=self.owner, name='owner-project', original_topic='owner'
        )
        self.other_project = Project.objects.create(
            user=self.other, name='other-project', original_topic='other'
        )
        self.provider = ModelProvider.objects.create(
            name='ledger-provider',
            provider_type='llm',
            deployment_mode='api',
            api_url='https://api.example.com/v1/chat/completions',
            api_key='secret-key',
            model_name='ledger-model',
            executor_class='core.ai_client.openai_client.OpenAIClient',
        )
        self.own_log = ModelUsageLog.objects.create(
            model_provider=self.provider,
            project_id=self.owner_project.id,
            status='failed',
            error_code='OWN_FAILURE',
            tokens_used=11,
        )
        self.other_log = ModelUsageLog.objects.create(
            model_provider=self.provider,
            project_id=self.other_project.id,
            status='failed',
            error_code='OTHER_FAILURE',
            tokens_used=22,
        )
        self.orphan_log = ModelUsageLog.objects.create(
            model_provider=self.provider,
            project_id=None,
            status='failed',
            error_code='ORPHAN_FAILURE',
            tokens_used=33,
        )

    def _authenticate(self, user):
        self.client.force_authenticate(user)

    def test_regular_user_all_log_actions_and_csv_are_project_scoped(self):
        self._authenticate(self.owner)

        listing = self.client.get('/api/v1/models/usage-logs/')
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item['id'] for item in listing.data['results']},
            {str(self.own_log.id)},
        )

        own_project = self.client.get(
            '/api/v1/models/usage-logs/by_project/',
            {'project_id': str(self.owner_project.id)},
        )
        other_project = self.client.get(
            '/api/v1/models/usage-logs/by_project/',
            {'project_id': str(self.other_project.id)},
        )
        failed = self.client.get('/api/v1/models/usage-logs/failed_logs/')
        provider_logs = self.client.get(
            f'/api/v1/models/providers/{self.provider.id}/usage_logs/'
        )
        self.assertEqual(own_project.data['count'], 1)
        self.assertEqual(other_project.data['count'], 0)
        self.assertEqual(failed.data['count'], 1)
        self.assertEqual(provider_logs.data['count'], 1)

        self.assertEqual(
            self.client.get(
                f'/api/v1/models/usage-logs/{self.other_log.id}/'
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.get(
                f'/api/v1/models/usage-logs/{self.orphan_log.id}/'
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        exported = self.client.get('/api/v1/models/usage-logs/export_csv/')
        csv_body = exported.content.decode('utf-8-sig')
        self.assertIn(str(self.owner_project.id), csv_body)
        self.assertNotIn(str(self.other_project.id), csv_body)
        self.assertNotIn('ORPHAN_FAILURE', csv_body)

        statistics = self.client.get(
            f'/api/v1/models/providers/{self.provider.id}/statistics/'
        )
        provider_list = self.client.get('/api/v1/models/providers/')
        provider_row = next(
            item for item in provider_list.data['results']
            if item['id'] == str(self.provider.id)
        )
        self.assertEqual(statistics.data['total_count'], 1)
        self.assertEqual(provider_row['total_usage_count'], 1)

    def test_staff_can_audit_global_and_projectless_logs(self):
        self._authenticate(self.staff)

        listing = self.client.get('/api/v1/models/usage-logs/')
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item['id'] for item in listing.data['results']},
            {str(self.own_log.id), str(self.other_log.id), str(self.orphan_log.id)},
        )
        exported = self.client.get('/api/v1/models/usage-logs/export_csv/')
        csv_body = exported.content.decode('utf-8-sig')
        self.assertIn(str(self.owner_project.id), csv_body)
        self.assertIn(str(self.other_project.id), csv_body)
        self.assertIn('ORPHAN_FAILURE', csv_body)


class ProviderAdminPermissionAndURLTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('provider-user', password='secret123')
        self.staff = User.objects.create_user(
            'provider-admin', password='secret123', is_staff=True
        )
        self.provider = ModelProvider.objects.create(
            name='permission-provider',
            provider_type='llm',
            deployment_mode='api',
            api_url='https://api.example.com/v1/chat/completions',
            api_key='secret-key',
            model_name='permission-model',
            executor_class='core.ai_client.openai_client.OpenAIClient',
            is_active=True,
        )

    @staticmethod
    def _provider_payload(api_url):
        return {
            'name': f'provider-{api_url}',
            'provider_type': 'llm',
            'deployment_mode': 'api',
            'api_url': api_url,
            'api_key': 'secret-key',
            'model_name': 'secure-model',
            'executor_class': 'core.ai_client.openai_client.OpenAIClient',
            'max_tokens': 512,
        }

    @patch('apps.models.services.ModelProviderService.check_provider_health')
    def test_regular_user_can_read_but_cannot_write_or_test_provider(self, health):
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.get('/api/v1/models/providers/').status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.post(
                '/api/v1/models/providers/',
                self._provider_payload('https://new.example.com/v1'),
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.patch(
                f'/api/v1/models/providers/{self.provider.id}/',
                {'name': 'forbidden-update'},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(
                f'/api/v1/models/providers/{self.provider.id}/'
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                f'/api/v1/models/providers/{self.provider.id}/toggle_status/'
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        response = self.client.post(
            f'/api/v1/models/providers/{self.provider.id}/test_connection/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        health.assert_not_called()

    def test_api_provider_rejects_non_loopback_http_and_url_userinfo(self):
        self.client.force_authenticate(self.staff)
        insecure = self.client.post(
            '/api/v1/models/providers/',
            self._provider_payload('http://api.example.com/v1'),
            format='json',
        )
        userinfo = self.client.post(
            '/api/v1/models/providers/',
            self._provider_payload('https://user:pass@api.example.com/v1'),
            format='json',
        )
        loopback = self.client.post(
            '/api/v1/models/providers/',
            self._provider_payload('http://127.0.0.1:18000/v1'),
            format='json',
        )
        self.assertEqual(insecure.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(userinfo.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(loopback.status_code, status.HTTP_201_CREATED)
