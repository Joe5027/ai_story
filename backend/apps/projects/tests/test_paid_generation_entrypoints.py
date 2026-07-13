from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.content.models import Storyboard
from apps.models.models import ModelProvider
from apps.projects.models import Project, ProjectStage
from apps.projects.tasks import run_full_pipeline_task
from apps.prompts.models import PromptTemplate, PromptTemplateSet


class PaidGenerationEntrypointTestCase(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='paid-entry-user', password='test'
        )
        self.client.force_authenticate(self.user)
        self.project = Project.objects.create(
            name='付费入口测试', original_topic='测试', user=self.user
        )
        self.template_set = PromptTemplateSet.objects.create(
            name='付费入口模板', created_by=self.user, is_active=True
        )
        self.project.prompt_template_set = self.template_set
        self.project.save(update_fields=['prompt_template_set'])

        self.llm_provider = ModelProvider.objects.create(
            name='节点对话付费 API',
            provider_type='llm',
            deployment_mode='api',
            executor_class='core.ai_client.openai_client.OpenAIClient',
            api_url='https://paid.example.invalid/v1',
            api_key='test-only',
            model_name='paid-chat',
        )
        self.image_provider = ModelProvider.objects.create(
            name='资产预览付费 API',
            provider_type='text2image',
            deployment_mode='api',
            executor_class='core.ai_client.text2image_client.Text2ImageClient',
            api_url='https://paid.example.invalid/v1/images',
            api_key='test-only',
            model_name='paid-image',
        )
        PromptTemplate.objects.create(
            template_set=self.template_set,
            stage_type='storyboard',
            model_provider=self.llm_provider,
            template_content='节点对话系统提示词',
            is_active=True,
        )
        PromptTemplate.objects.create(
            template_set=self.template_set,
            stage_type='image_generation',
            model_provider=self.image_provider,
            template_content='{{ visual_prompt }}',
            is_active=True,
        )
        self.storyboard = Storyboard.objects.create(
            project=self.project,
            sequence_number=1,
            scene_description='中景',
            narration_text='旁白',
            image_prompt='画面',
        )
        ProjectStage.objects.create(
            project=self.project,
            stage_type='asset_extraction',
            output_data={
                'items': [{
                    'temp_id': 'asset-temp-1',
                    'key': 'hero',
                    'label': '主角',
                }]
            },
        )

    @patch('apps.projects.views.create_ai_client')
    def test_paid_node_chat_requires_per_call_confirmation(self, create_client):
        response = self.client.post(
            f'/api/v1/projects/projects/{self.project.id}/node-chat-init/',
            {
                'node_type': 'storyboard',
                'node_id': str(self.storyboard.id),
                'user_message': '调整画面',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'PAID_CONFIRMATION_REQUIRED')
        create_client.assert_not_called()

    @patch('apps.projects.views.create_ai_client')
    def test_confirmed_asset_preview_without_cloud_authorization_has_zero_invoke(
        self, create_client
    ):
        response = self.client.post(
            f'/api/v1/projects/projects/{self.project.id}/asset_extraction_generate_image/',
            {
                'temp_id': 'asset-temp-1',
                'prompt': '生成主角预览图',
                'confirm_paid': True,
                'confirmed_max_cost_cny': '1.000000',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('项目未显式授权', response.data['error'])
        create_client.assert_not_called()

    @patch('apps.projects.views.ProjectViewSet._start_pipeline_directly')
    def test_full_pipeline_with_direct_api_provider_is_blocked_before_enqueue(self, start):
        response = self.client.post(
            f'/api/v1/projects/projects/{self.project.id}/run_pipeline/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data['error']['code'], 'PAID_PIPELINE_CONFIRMATION_REQUIRED'
        )
        start.assert_not_called()

    @patch('apps.projects.tasks.complete_episode_task_by_celery_id')
    @patch('apps.projects.tasks._unregister_project_task')
    @patch('apps.projects.tasks.RedisStreamPublisher')
    @patch('apps.projects.tasks.execute_llm_stage')
    def test_worker_rechecks_direct_api_provider_after_queue_wait(
        self, execute_llm, publisher, _unregister, _complete
    ):
        publisher.return_value = MagicMock()
        result = run_full_pipeline_task.run(str(self.project.pk), self.user.pk)

        self.assertFalse(result['success'])
        self.assertIn('PAID_PIPELINE_CONFIRMATION_REQUIRED', result['error'])
        execute_llm.assert_not_called()
