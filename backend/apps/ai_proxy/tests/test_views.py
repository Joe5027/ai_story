from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.models.models import ModelProvider


class AIProxyViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='secret123')
        self.client.force_authenticate(self.user)

    def test_models_endpoint_groups_active_providers(self):
        ModelProvider.objects.create(
            name='Chat Provider',
            provider_type='llm',
            api_url='https://example.com/v1/chat/completions',
            api_key='key',
            model_name='gpt-test',
            executor_class='core.ai_client.openai_client.OpenAIClient',
        )
        ModelProvider.objects.create(
            name='Image Provider',
            provider_type='text2image',
            api_url='https://example.com/v1/images/generations',
            api_key='key',
            model_name='image-test',
            executor_class='core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
        )
        ModelProvider.objects.create(
            name='Video Provider',
            provider_type='image2video',
            api_url='https://example.com/v1/videos/generations',
            api_key='key',
            model_name='video-test',
            executor_class='core.ai_client.image2video_client.VideoGeneratorClient',
        )

        response = self.client.get(reverse('ai-models'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # 安装迁移会保留可用 Mock Provider，列表断言只关注本测试创建的条目。
        self.assertIn('gpt-test', [item['model_name'] for item in response.data['chat']])
        self.assertIn('image-test', [item['model_name'] for item in response.data['image']])
        self.assertIn('video-test', [item['model_name'] for item in response.data['video']])

    @patch('apps.ai_proxy.views.create_ai_client')
    def test_images_generations_uses_text2image_provider(self, mock_create_client):
        provider = ModelProvider.objects.create(
            name='Image Provider',
            provider_type='text2image',
            api_url='https://example.com/v1/images/generations',
            api_key='key',
            model_name='image-test',
            executor_class='core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
            deployment_mode='mock',
        )
        mock_create_client.return_value = object()

        with patch('apps.ai_proxy.views.ImageGenerationService.generate') as mock_generate:
            mock_generate.return_value = Mock(
                success=True,
                text='ok',
                data=[{'url': '/api/v1/content/storage/image/a.png'}],
                metadata={'latency_ms': 12},
            )
            response = self.client.post(
                reverse('ai-images-generations'),
                {
                    'model': 'image-test',
                    'prompt': '一只猫',
                    'size': '1024x1024',
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['model'], 'image-test')
        self.assertEqual(response.data['provider']['id'], str(provider.id))
        self.assertEqual(response.data['provider_type'], 'text2image')
        self.assertEqual(response.data['data'][0]['url'], '/api/v1/content/storage/image/a.png')
        mock_generate.assert_called_once()

    @patch('apps.ai_proxy.views.create_ai_client')
    def test_images_generations_uses_image_edit_provider_when_mode_is_inpaint(self, mock_create_client):
        provider = ModelProvider.objects.create(
            name='Edit Provider',
            provider_type='image_edit',
            api_url='https://example.com/v1/images/edits',
            api_key='key',
            model_name='edit-test',
            executor_class='core.ai_client.executors.openai_images_edit_executor.OpenAIImagesEditExecutor',
            deployment_mode='mock',
        )
        mock_create_client.return_value = object()

        with patch('apps.ai_proxy.views.ImageGenerationService.edit') as mock_edit:
            mock_edit.return_value = Mock(
                success=True,
                text='ok',
                data=[{'url': '/api/v1/content/storage/image/edited.png'}],
                metadata={'latency_ms': 20},
            )
            response = self.client.post(
                reverse('ai-images-generations'),
                {
                    'model': 'edit-test',
                    'prompt': '修复这张图',
                    'mode': 'inpaint',
                    'image': '/api/v1/content/storage/image/source.png',
                    'mask': '/api/v1/content/storage/image/mask.png',
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['model'], 'edit-test')
        self.assertEqual(response.data['provider']['id'], str(provider.id))
        self.assertEqual(response.data['provider_type'], 'image_edit')
        self.assertEqual(response.data['data'][0]['url'], '/api/v1/content/storage/image/edited.png')
        mock_edit.assert_called_once()

    @patch('apps.ai_proxy.views.create_ai_client')
    def test_videos_generations_uses_image2video_provider(self, mock_create_client):
        provider = ModelProvider.objects.create(
            name='Video Provider',
            provider_type='image2video',
            api_url='https://example.com/v1/videos/generations',
            api_key='key',
            model_name='video-test',
            executor_class='core.ai_client.image2video_client.VideoGeneratorClient',
            deployment_mode='mock',
        )

        fake_client = Mock()
        fake_client._generate_video.return_value = {
            'success': True,
            'data': [{'url': '/api/v1/content/storage/video/a.mp4'}],
            'metadata': {'task_id': 'task-1'},
        }
        mock_create_client.return_value = fake_client

        response = self.client.post(
            reverse('ai-videos-generations'),
            {
                'model': 'video-test',
                'prompt': '让角色走起来',
                'images': [
                    '/api/v1/content/storage/image/a.png',
                    '/api/v1/content/storage/image/b.png',
                ],
                'duration': 8,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['model'], 'video-test')
        self.assertEqual(response.data['provider']['id'], str(provider.id))
        self.assertEqual(response.data['data'][0]['url'], '/api/v1/content/storage/video/a.mp4')
        fake_client._generate_video.assert_called_once()
        self.assertEqual(
            fake_client._generate_video.call_args.kwargs['image_uris'],
            ['/api/v1/content/storage/image/a.png', '/api/v1/content/storage/image/b.png'],
        )

    @patch('apps.ai_proxy.views.ImageGenerationService.generate')
    def test_api_image_without_project_is_blocked_before_external_execution(self, mock_generate):
        ModelProvider.objects.create(
            name='Paid Image Provider',
            provider_type='text2image',
            api_url='https://example.com/v1/images/generations',
            api_key='secret',
            model_name='paid-image-test',
            executor_class='core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
            deployment_mode='api',
        )

        response = self.client.post(
            reverse('ai-images-generations'),
            {'model': 'paid-image-test', 'prompt': '不会出站'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'PROJECT_REQUIRED')
        mock_generate.assert_not_called()
