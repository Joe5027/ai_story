from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest.mock import Mock, patch

from apps.models.models import ModelProvider
from apps.projects.models import Project
from core.ai_client.factory import create_ai_client
from core.ai_client.outbound_guard import PaidProviderGuardDenied
from core.ai_client.runtime_agent_client import RuntimeAgentTransport

from ..services.security import REDACTED, mask_sensitive_data
from ..services.work_items import WorkItemService


class SensitiveDataMaskingTestCase(TestCase):
    def test_token_usage_fields_remain_numeric_while_credentials_are_masked(self):
        masked = mask_sensitive_data({
            'max_tokens': 4096,
            'input_tokens': 120,
            'output_tokens': 80,
            'access_token': 'runtime-secret',
            'provider_api_key': 'paid-secret',
        })

        self.assertEqual(masked['max_tokens'], 4096)
        self.assertEqual(masked['input_tokens'], 120)
        self.assertEqual(masked['output_tokens'], 80)
        self.assertEqual(masked['access_token'], REDACTED)
        self.assertEqual(masked['provider_api_key'], REDACTED)

    def test_work_item_keeps_token_limits_but_never_persists_credentials(self):
        user = get_user_model().objects.create_user(username='mask-user', password='test')
        project = Project.objects.create(
            name='脱敏测试', original_topic='测试', user=user
        )

        item = WorkItemService.create(
            project,
            'llm',
            idempotency_key='mask-work-item',
            request_parameters={
                'prompt': '测试',
                'max_tokens': 2048,
                'usage_estimate': {'input_tokens': 10, 'output_tokens': 20},
                'authorization': 'Bearer secret-value',
            },
        )

        self.assertEqual(item.request_parameters['max_tokens'], 2048)
        self.assertEqual(item.request_parameters['usage_estimate']['input_tokens'], 10)
        self.assertEqual(item.request_parameters['authorization'], REDACTED)


class PaidProviderOutboundGuardTestCase(TestCase):
    def test_direct_paid_client_creation_is_blocked_before_network(self):
        provider = ModelProvider.objects.create(
            name='未受控付费 Provider',
            provider_type='llm',
            deployment_mode='api',
            executor_class='core.ai_client.openai_client.OpenAIClient',
            api_url='https://example.invalid/v1',
            api_key='test-only-secret',
            model_name='paid-model',
        )

        with self.assertRaises(PaidProviderGuardDenied):
            create_ai_client(provider)

    def test_mock_client_remains_available_without_paid_context(self):
        provider = ModelProvider.objects.create(
            name='安全 Mock Provider',
            provider_type='llm',
            deployment_mode='mock',
            executor_class='core.ai_client.mock_llm_client.MockLLMClient',
            model_name='mock-model',
        )

        self.assertIsNotNone(create_ai_client(provider))


class RuntimeAgentSubmissionSafetyTestCase(TestCase):
    @patch('core.ai_client.runtime_agent_client.requests.delete')
    @patch('core.ai_client.runtime_agent_client.requests.post')
    def test_persistence_callback_failure_requests_agent_cancellation(self, post, delete):
        accepted = Mock(status_code=202)
        accepted.json.return_value = {'job_id': 'orphan-candidate', 'status': 'queued'}
        post.return_value = accepted
        delete.return_value = Mock(status_code=202)
        transport = RuntimeAgentTransport(
            'http://127.0.0.1:9100', 'runtime-token', 'mock/default', timeout=5
        )
        transport.on_job_submitted = Mock(side_effect=RuntimeError('数据库写入失败'))

        with self.assertRaisesRegex(RuntimeError, '数据库写入失败'):
            transport.run_job(
                'llm',
                '测试',
                project_id='project-1',
                stage_type='rewrite',
                work_item_id='work-1',
            )

        delete.assert_called_once()
        self.assertTrue(delete.call_args.args[0].endswith('/v1/jobs/orphan-candidate'))
