from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.content.processors.text2image_stage import Text2ImageStageProcessor
from apps.models.models import ModelProvider, ModelUsageLog
from apps.projects.models import Project
from core.ai_client.base import AIResponse


class LegacyProcessorHybridGuardTestCase(TestCase):
    """证明旧 Processor 不再能在三重门外创建付费客户端。"""

    def setUp(self):
        user = get_user_model().objects.create_user(
            username='legacy-hybrid-user', password='test'
        )
        self.project = Project.objects.create(
            name='旧处理器安全测试', original_topic='测试', user=user
        )
        self.processor = Text2ImageStageProcessor()
        self.storyboard = {
            'scene_number': 1,
            'narration': '旁白',
            'visual_prompt': '一张安全测试图片',
            'shot_type': '中景',
        }
        self.prompt_payload = {
            'prompt': '一张安全测试图片',
            'image': [],
        }
        self.client_params = {
            'ratio': '1:1',
            'resolution': '2k',
            'width': 1024,
            'height': 1024,
            'steps': 20,
            'negative_prompt': '',
            'sample_count': 1,
        }

    def provider(self, deployment_mode):
        return ModelProvider.objects.create(
            name=f'{deployment_mode}-text2image',
            provider_type='text2image',
            deployment_mode=deployment_mode,
            executor_class='core.ai_client.mock_text2image_client.MockText2ImageClient',
            model_name='guard-test-model',
            api_url='https://paid.example.invalid/v1/images',
            api_key='test-only-secret',
        )

    @patch.object(Text2ImageStageProcessor, '_resolve_generation_client_params')
    @patch.object(Text2ImageStageProcessor, '_build_generation_prompt_payload')
    @patch('apps.content.processors.text2image_stage.ImageGenerationService.generate')
    @patch('apps.content.processors.text2image_stage.create_ai_client')
    def test_unauthorized_paid_processor_never_invokes_provider(
        self,
        create_client,
        generate,
        build_prompt,
        resolve_params,
    ):
        build_prompt.return_value = self.prompt_payload
        resolve_params.return_value = self.client_params

        result = self.processor._generate_single_image(
            project=self.project,
            storyboard=self.storyboard,
            provider=self.provider('api'),
        )

        self.assertIsNone(result)
        create_client.assert_not_called()
        generate.assert_not_called()
        self.assertFalse(ModelUsageLog.objects.exists())

    @patch.object(Text2ImageStageProcessor, '_resolve_generation_client_params')
    @patch.object(Text2ImageStageProcessor, '_build_generation_prompt_payload')
    @patch('apps.content.processors.text2image_stage.ImageGenerationService.generate')
    @patch('apps.content.processors.text2image_stage.create_ai_client')
    def test_mock_processor_still_generates_through_hybrid(
        self,
        create_client,
        generate,
        build_prompt,
        resolve_params,
    ):
        build_prompt.return_value = self.prompt_payload
        resolve_params.return_value = self.client_params
        create_client.return_value = Mock()
        generate.return_value = AIResponse(
            success=True,
            data=[{'url': '/api/v1/content/storage/image/mock.png'}],
            metadata={'usage': {'image_count': 1}},
        )

        result = self.processor._generate_single_image(
            project=self.project,
            storyboard=self.storyboard,
            provider=self.provider('mock'),
        )

        self.assertEqual(result[0]['url'], '/api/v1/content/storage/image/mock.png')
        create_client.assert_called_once()
        generate.assert_called_once()
        usage_log = ModelUsageLog.objects.get()
        self.assertEqual(usage_log.deployment_mode, 'mock')
        self.assertEqual(usage_log.status, 'success')
