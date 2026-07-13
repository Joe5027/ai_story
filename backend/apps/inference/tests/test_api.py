from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.models.models import ModelProvider
from apps.projects.models import Project

from ..models import (
    AIBudgetPolicy,
    BudgetReservation,
    GenerationWorkItem,
    ProjectAISettings,
    ProviderPriceRate,
    RuntimeNode,
)
from ..services.budget import BudgetService


class InferenceControlPlaneAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='secret123')
        self.other = User.objects.create_user(username='other', password='secret123')
        self.client.force_authenticate(self.user)
        self.project = Project.objects.create(
            user=self.user,
            name='本地推理项目',
            original_topic='仅本地处理',
        )

    def test_runtime_node_token_is_write_only(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        response = self.client.post('/api/v1/models/runtime-nodes/', {
            'name': 'remote-test-node',
            'node_type': 'rented_gpu',
            'agent_url': 'https://runtime.example.com',
            'access_token': 'node-secret-token',
            'is_local': False,
            'slot_count': 1,
            'concurrency_limit': 1,
            'is_active': True,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('access_token', response.data)
        self.assertTrue(response.data['has_access_token'])
        self.assertEqual(response.data['access_token_masked'], '***oken')
        detail = self.client.get(
            f"/api/v1/models/runtime-nodes/{response.data['id']}/"
        )
        self.assertNotIn('node-secret-token', str(detail.data))

    def test_project_cloud_authorization_requires_explicit_confirmation(self):
        url = f'/api/v1/projects/projects/{self.project.id}/ai-settings/'
        rejected = self.client.patch(
            url, {'allow_cloud_data_transfer': True}, format='json'
        )
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            rejected.data['error']['code'], 'CLOUD_AUTH_CONFIRMATION_REQUIRED'
        )

        accepted = self.client.patch(url, {
            'allow_cloud_data_transfer': True,
            'confirm_cloud_data_transfer': True,
        }, format='json')
        self.assertEqual(accepted.status_code, status.HTTP_200_OK)
        self.assertTrue(accepted.data['allow_cloud_data_transfer'])
        self.assertEqual(accepted.data['cloud_authorized_by_name'], self.user.username)
        self.assertIsNotNone(accepted.data['cloud_authorized_at'])

        revoked = self.client.patch(
            url, {'allow_cloud_data_transfer': False}, format='json'
        )
        self.assertEqual(revoked.status_code, status.HTTP_200_OK)
        self.assertFalse(revoked.data['allow_paid_fallback'])
        self.assertIsNone(revoked.data['cloud_authorized_at'])

    def test_estimate_reports_zero_budget_gates_and_video_segments(self):
        response = self.client.post(
            f'/api/v1/projects/projects/{self.project.id}/generation-estimates/',
            {
                'capability': 'image2video',
                'profile': 'balanced',
                'task_count': 2,
                'duration_seconds': 10,
                'native_max_seconds': 5,
                'fps': 24,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['effective_work_item_count'], 4)
        self.assertIn('project_budget', response.data['missing_configuration'])
        self.assertIn('global_daily_budget', response.data['missing_configuration'])
        self.assertFalse(response.data['automatic_fallback_allowed'])
        self.assertTrue(response.data['segment_plan'])
        self.assertLessEqual(
            max(float(item['requested_duration_seconds']) for item in response.data['segment_plan']),
            5,
        )

    def test_explicit_paid_provider_estimate_does_not_require_fallback_route(self):
        provider = ModelProvider.objects.create(
            name='显式付费文本模型',
            provider_type='llm',
            deployment_mode='api',
            api_url='https://example.com/v1',
            api_key='paid-secret',
            model_name='paid-llm',
            executor_class='core.ai_client.openai_client.OpenAIClient',
        )
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='llm',
            model_pattern='paid-llm',
            billing_unit='request',
            unit_price='0.50000000',
        )
        policy = AIBudgetPolicy.objects.create(
            name='显式调用预算',
            hard_limit=100,
            daily_limit_cny=100,
            monthly_limit_cny=100,
        )
        settings_obj, _ = ProjectAISettings.objects.get_or_create(project=self.project)
        settings_obj.budget_policy = policy
        settings_obj.allow_cloud_data_transfer = True
        settings_obj.cloud_authorized_by = self.user
        settings_obj.cloud_authorized_at = timezone.now()
        settings_obj.project_budget_cny = 100
        settings_obj.save()

        response = self.client.post(
            f'/api/v1/projects/projects/{self.project.id}/generation-estimates/',
            {
                'capability': 'llm',
                'provider_id': str(provider.pk),
                'task_count': 2,
                'usage_per_item': {},
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['estimate_scope'], 'explicit_provider')
        self.assertNotIn('generation_route', response.data['missing_configuration'])
        self.assertNotIn('paid_fallback_disabled', response.data['missing_configuration'])
        self.assertEqual(response.data['worst_api_cost_cny'], 1)
        self.assertTrue(response.data['manual_paid_execution_allowed'])

    def test_project_work_items_do_not_leak_between_users(self):
        own = GenerationWorkItem.objects.create(
            project=self.project,
            capability='llm',
            stage_type='storyboard',
            request_parameters={'prompt': '私密提示词'},
        )
        other_project = Project.objects.create(
            user=self.other, name='其他项目', original_topic='private'
        )
        GenerationWorkItem.objects.create(
            project=other_project,
            capability='llm',
            stage_type='storyboard',
            request_parameters={'prompt': '其他人的内容'},
        )

        response = self.client.get(
            f'/api/v1/projects/projects/{self.project.id}/generation-work-items/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(own.id))
        self.assertNotIn('私密提示词', str(response.data))
        self.assertNotIn('其他人的内容', str(response.data))

    @patch('apps.ai_proxy.views.ImageGenerationService.generate')
    def test_paid_api_with_project_but_no_cloud_authorization_makes_no_call(self, generate):
        provider = ModelProvider.objects.create(
            name='Paid gated image',
            provider_type='text2image',
            deployment_mode='api',
            api_url='https://example.com/v1/images',
            api_key='paid-secret',
            model_name='paid-test',
            executor_class='core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
        )
        response = self.client.post('/api/v1/ai/images/generations', {
            'model': provider.model_name,
            'project_id': str(self.project.id),
            'confirm_paid_generation': True,
            'confirmed_max_cost_cny': '1.000000',
            'prompt': '不得出站',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['error']['code'], 'CLOUD_NOT_AUTHORIZED')
        generate.assert_not_called()

    def test_budget_summary_defaults_to_zero(self):
        response = self.client.get(
            f'/api/v1/models/budget-summary/?project_id={self.project.id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['settled_total'], 0)
        self.assertEqual(response.data['reserved_total'], 0)
        self.assertEqual(response.data['policies'][0]['daily_limit_cny'], '0.000000')

    def test_runtime_node_list_is_paginated(self):
        RuntimeNode.objects.create(
            name='pagination-node',
            node_type='local_gpu',
            agent_url='http://127.0.0.1:9100',
        )
        response = self.client.get('/api/v1/models/runtime-nodes/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)

    def test_global_budget_and_route_configuration_requires_admin(self):
        policy = AIBudgetPolicy.objects.create(name='管理员预算', hard_limit=10)

        budget_response = self.client.patch(
            f'/api/v1/models/budget-policies/{policy.id}/',
            {'daily_limit_cny': '10.000000'},
            format='json',
        )
        route_response = self.client.post(
            '/api/v1/models/generation-routes/',
            {
                'name': '普通用户不能创建的全局路由',
                'capability': 'llm',
                'scope': 'global',
                'profile_code': 'balanced',
            },
            format='json',
        )

        self.assertEqual(budget_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(route_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reservation_and_work_item_errors_are_redacted_on_read(self):
        policy = AIBudgetPolicy.objects.create(name='脱敏预算', hard_limit=10)
        item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='llm',
            stage_type='rewrite',
            status='failed',
            error_message='Bearer runtime-secret-token',
            projection_error='api_key=sk-projection-secret',
        )
        reservation = BudgetReservation.objects.create(
            policy=policy,
            project=self.project,
            work_item=item,
            idempotency_key='redacted-reservation',
            status='manual_review',
            estimated_amount=1,
            reserved_amount=1,
            details={'manual_review_reason': 'token=sk-reservation-secret'},
        )

        work_item_response = self.client.get(
            f'/api/v1/models/generation-work-items/{item.id}/'
        )
        reservation_response = self.client.get(
            f'/api/v1/models/budget-reservations/{reservation.id}/'
        )
        rendered = f'{work_item_response.data} {reservation_response.data}'

        self.assertEqual(work_item_response.status_code, status.HTTP_200_OK)
        self.assertEqual(reservation_response.status_code, status.HTTP_200_OK)
        self.assertNotIn('runtime-secret-token', rendered)
        self.assertNotIn('projection-secret', rendered)
        self.assertNotIn('reservation-secret', rendered)
        self.assertIn('[REDACTED]', rendered)

    def test_retry_failed_items_requires_paid_manual_review_resolution(self):
        item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='llm',
            stage_type='rewrite',
            status='failed',
            error_class='manual_review',
            error_code='PAID_RESULT_AMBIGUOUS',
            idempotency_key='paid-manual-review',
        )
        policy = AIBudgetPolicy.objects.create(name='待人工复核预算', hard_limit=10)
        reservation = BudgetReservation.objects.create(
            policy=policy,
            project=self.project,
            work_item=item,
            idempotency_key='paid-manual-review-reservation',
            status='manual_review',
            estimated_amount=1,
            reserved_amount=1,
        )
        url = f'/api/v1/projects/projects/{self.project.id}/retry-failed-items/'

        blocked = self.client.post(
            url, {'work_item_ids': [str(item.id)]}, format='json'
        )

        self.assertEqual(blocked.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(blocked.data['error']['code'], 'PAID_MANUAL_REVIEW_REQUIRED')
        self.assertEqual(GenerationWorkItem.objects.count(), 1)

        BudgetService.resolve_manual_review(
            reservation,
            charged=False,
            note='已确认厂商没有计费',
        )
        allowed = self.client.post(
            url, {'work_item_ids': [str(item.id)]}, format='json'
        )

        self.assertEqual(allowed.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(allowed.data['retried_count'], 1)
        self.assertEqual(GenerationWorkItem.objects.count(), 2)

    def test_manual_api_regeneration_is_confirmed_cost_capped_and_idempotent(self):
        provider = ModelProvider.objects.create(
            name='人工重生成 API',
            provider_type='text2image',
            deployment_mode='api',
            api_url='https://example.com/v1',
            api_key='paid-secret',
            model_name='paid-image',
            executor_class='core.ai_client.openai_client.OpenAIClient',
        )
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='text2image',
            model_pattern='paid-image',
            billing_unit='image',
            unit_price='0.50000000',
        )
        policy = AIBudgetPolicy.objects.create(
            name='人工重生成预算', hard_limit=100,
            daily_limit_cny=100, monthly_limit_cny=100,
        )
        settings_obj, _ = ProjectAISettings.objects.get_or_create(project=self.project)
        settings_obj.budget_policy = policy
        settings_obj.allow_cloud_data_transfer = True
        settings_obj.cloud_authorized_by = self.user
        settings_obj.cloud_authorized_at = timezone.now()
        settings_obj.project_budget_cny = 100
        settings_obj.save()
        source = GenerationWorkItem.objects.create(
            project=self.project,
            capability='text2image',
            stage_type='image_generation',
            status='succeeded',
            idempotency_key='local-image-source',
            request_parameters={
                'prompt': '本地结果主观不满意',
                'usage_estimate': {'image_count': 1},
            },
        )
        url = f'/api/v1/projects/projects/{self.project.id}/regenerate-work-item-with-api/'
        payload = {
            'work_item_id': str(source.pk),
            'provider_id': str(provider.pk),
            'confirm_paid_generation': True,
            'confirmed_max_cost_cny': '0.500000',
        }

        first = self.client.post(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='manual-image-v1'
        )
        replay = self.client.post(
            url, payload, format='json', HTTP_IDEMPOTENCY_KEY='manual-image-v1'
        )

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertTrue(replay.data['idempotent_replay'])
        regenerated = GenerationWorkItem.objects.exclude(pk=source.pk).get()
        self.assertEqual(regenerated.provider, provider)
        self.assertTrue(regenerated.request_parameters['manual_api'])
        self.assertEqual(regenerated.max_attempts, 1)

    @override_settings(AI_ROUTER_V2_ENABLED=True)
    @patch('apps.projects.views.is_stage_template_enabled', return_value=True)
    @patch('apps.projects.views.StageWorkItemPlanner.plan_stage')
    def test_execute_stage_manual_api_is_server_side_cost_capped(
        self, plan_stage, _stage_enabled
    ):
        provider = ModelProvider.objects.create(
            name='阶段人工 API',
            provider_type='llm',
            deployment_mode='api',
            api_url='https://example.com/v1',
            api_key='paid-secret',
            model_name='paid-stage-llm',
            executor_class='core.ai_client.openai_client.OpenAIClient',
        )
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='llm',
            model_pattern='paid-stage-llm',
            billing_unit='request',
            unit_price='0.50000000',
        )
        policy = AIBudgetPolicy.objects.create(
            name='阶段人工预算', hard_limit=100,
            daily_limit_cny=100, monthly_limit_cny=100,
        )
        settings_obj, _ = ProjectAISettings.objects.get_or_create(project=self.project)
        settings_obj.budget_policy = policy
        settings_obj.allow_cloud_data_transfer = True
        settings_obj.cloud_authorized_by = self.user
        settings_obj.cloud_authorized_at = timezone.now()
        settings_obj.project_budget_cny = 100
        settings_obj.save()
        item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='llm',
            stage_type='rewrite',
            provider=provider,
            idempotency_key='manual-stage-item',
            request_parameters={
                'prompt': '付费改写',
                'usage_estimate': {'request_count': 1},
            },
        )
        plan_stage.return_value = SimpleNamespace(
            stage_execution_id=item.stage_execution_id,
            work_items=(item,),
            created_count=1,
        )

        response = self.client.post(
            f'/api/v1/projects/projects/{self.project.id}/execute_stage/',
            {
                'stage_name': 'rewrite',
                'input_data': {
                    'manual_api': True,
                    'explicit_provider_id': str(provider.pk),
                    'confirm_paid_generation': True,
                    'confirmed_max_cost_cny': '0.500000',
                },
                'manual_api': True,
                'explicit_provider_id': str(provider.pk),
                'confirm_paid_generation': True,
                'confirmed_max_cost_cny': '0.500000',
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='manual-stage-v1',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        item.refresh_from_db()
        self.assertTrue(item.request_parameters['manual_api'])
        self.assertEqual(item.request_parameters['confirmed_max_cost_cny'], '0.500000')
        self.assertEqual(item.max_attempts, 1)
        self.assertEqual(response.data['maximum_estimated_cost_cny'], 0.5)
