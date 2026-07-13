from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.ai_client.image2video_client import VideoGeneratorClient
from core.ai_client.siliconflow_video_client import SiliconFlowVideoClient
from core.ai_client.volcengine_image2video_client import VolcengineImage2VideoClient


class VideoGeneratorClientTestCase(SimpleTestCase):
    @patch('core.ai_client.image2video_client.VideoGeneratorClient._localize_video_data', side_effect=lambda data, timeout: data)
    @patch('core.ai_client.image2video_client.requests.post')
    def test_chat_completions_endpoint_extracts_video_url(self, mock_post, mock_localize):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            'choices': [
                {
                    'message': {
                        'content': '<video src="https://example.com/generated.mp4" controls="controls"></video>'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        client = VideoGeneratorClient(
            api_url='https://freeapi.example.com/v1/chat/completions',
            api_token='secret',
            model='grok-imagine-1.0-video',
        )

        result = client._generate_video(
            prompt='狗狗动起来',
            image_base64='ZmFrZV9pbWFnZV9iYXNlNjQ=',
            camera_movement_description='镜头缓慢推进，主体保持居中',
            model='grok-imagine-1.0-video',
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'], [{'url': 'https://example.com/generated.mp4'}])
        mock_post.assert_called_once()
        self.assertEqual(
            mock_post.call_args.args[0],
            'https://freeapi.example.com/v1/chat/completions',
        )
        self.assertEqual(
            mock_post.call_args.kwargs['json']['messages'][0]['content'][1]['image_url']['url'],
            'data:image/jpeg;base64,ZmFrZV9pbWFnZV9iYXNlNjQ=',
        )
        self.assertIn(
            '运镜描述：镜头缓慢推进，主体保持居中',
            mock_post.call_args.kwargs['json']['messages'][0]['content'][0]['text'],
        )

    @patch('core.ai_client.image2video_client.VideoGeneratorClient._read_image_url_as_base64', return_value='ZmFrZV9pbWFnZV9iYXNlNjQ=')
    @patch('core.ai_client.image2video_client.requests.post')
    def test_video_generations_endpoint_keeps_original_url(self, mock_post, mock_read_image):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            'data': {
                'task_id': 'task-123'
            }
        }
        mock_post.return_value = mock_response

        client = VideoGeneratorClient(
            api_url='https://video.example.com/v1/video/generations',
            api_token='secret',
            model='video-model',
        )

        task_result = client.create_video_task(
            prompt='狗狗动起来',
            image_uri='https://example.com/source.png',
            image_base64='ZmFrZV9pbWFnZV9iYXNlNjQ=',
            camera_movement_description='镜头轻微右移',
            model='video-model',
        )

        self.assertEqual(task_result, 'task-123')
        mock_post.assert_called_once()
        self.assertEqual(
            mock_post.call_args.args[0],
            'https://video.example.com/v1/video/generations',
        )
        self.assertEqual(
            mock_post.call_args.kwargs['json']['imageBase64'],
            'ZmFrZV9pbWFnZV9iYXNlNjQ=',
        )
        self.assertEqual(
            mock_post.call_args.kwargs['json']['cameraMovementDescription'],
            '镜头轻微右移',
        )


    @patch('core.ai_client.image2video_client.VideoGeneratorClient._localize_video_data', side_effect=lambda data, timeout: data)
    @patch('core.ai_client.image2video_client.Path.read_bytes', return_value=b'local-image-bytes')
    @patch('core.ai_client.image2video_client.requests.post')
    def test_chat_completions_endpoint_reads_storage_image_as_base64(self, mock_post, mock_read_bytes, mock_localize):
        post_response = Mock()
        post_response.raise_for_status.return_value = None
        post_response.json.return_value = {
            'choices': [
                {
                    'message': {
                        'content': '<video src="https://example.com/generated.mp4" controls="controls"></video>'
                    }
                }
            ]
        }
        mock_post.return_value = post_response

        client = VideoGeneratorClient(
            api_url='https://freeapi.example.com/v1/chat/completions',
            api_token='secret',
            model='grok-imagine-1.0-video',
        )

        result = client._generate_video(
            prompt='狗狗动起来',
            image_uri='/api/v1/content/storage/image/2026-03-16/image_da512287fb1d4e50912886a1cdd0324e.jpg',
            model='grok-imagine-1.0-video',
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'], [{'url': 'https://example.com/generated.mp4'}])
        mock_read_bytes.assert_called_once()
        self.assertEqual(
            mock_post.call_args.kwargs['json']['messages'][0]['content'][1]['image_url']['url'],
            'data:image/jpeg;base64,bG9jYWwtaW1hZ2UtYnl0ZXM=',
        )


class VolcengineImage2VideoClientTestCase(SimpleTestCase):
    @patch('core.ai_client.volcengine_image2video_client.VideoGeneratorClient._localize_video_data', side_effect=lambda data, timeout: data)
    @patch('core.ai_client.volcengine_image2video_client.requests.get')
    @patch('core.ai_client.volcengine_image2video_client.requests.post')
    def test_generate_video_uses_volc_task_api(self, mock_post, mock_get, mock_localize):
        create_response = Mock()
        create_response.raise_for_status.return_value = None
        create_response.json.return_value = {'id': 'task-123'}
        mock_post.return_value = create_response

        get_response = Mock()
        get_response.raise_for_status.return_value = None
        get_response.json.return_value = {
            'status': 'succeeded',
            'content': {'video_url': 'https://example.com/generated.mp4'},
            'usage': {'total_tokens': 12},
        }
        mock_get.return_value = get_response

        client = VolcengineImage2VideoClient(
            api_url='https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',
            api_token='secret',
            model='doubao-seedance-1-5-pro-251215',
        )

        result = client._generate_video(
            prompt='小猫对着镜头打哈欠',
            image_base64='ZmFrZV9pbWFnZQ==',
            aspect_ratio='16:9',
            duration_seconds=5,
            resolution='720p',
            seed=11,
            generate_audio=True,
            poll_interval=0,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'], [{'url': 'https://example.com/generated.mp4'}])
        self.assertEqual(
            mock_post.call_args.args[0],
            'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',
        )
        self.assertEqual(mock_post.call_args.kwargs['json']['model'], 'doubao-seedance-1-5-pro-251215')
        self.assertEqual(mock_post.call_args.kwargs['json']['ratio'], '16:9')
        self.assertEqual(mock_post.call_args.kwargs['json']['duration'], 5)
        self.assertEqual(mock_post.call_args.kwargs['json']['resolution'], '720p')
        self.assertTrue(mock_post.call_args.kwargs['json']['generate_audio'])
        self.assertEqual(
            mock_post.call_args.kwargs['json']['content'][1]['image_url']['url'],
            'data:image/jpeg;base64,ZmFrZV9pbWFnZQ==',
        )
        self.assertEqual(
            mock_get.call_args.args[0],
            'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/task-123',
        )

    @patch('core.ai_client.volcengine_image2video_client.time.sleep', return_value=None)
    def test_wait_volc_task_breaks_after_max_poll_attempts(self, mock_sleep):
        client = VolcengineImage2VideoClient(
            api_url='https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',
            api_token='secret',
            model='doubao-seedance-1-5-pro-251215',
        )

        with patch.object(client, '_get_volc_task', return_value={'status': 'processing'}) as mock_get_task:
            with self.assertRaises(TimeoutError) as exc:
                client._wait_volc_task(
                    task_id='task-123',
                    poll_interval=0,
                    max_wait_time=600,
                    timeout=30,
                    max_poll_attempts=3,
                )

        self.assertIn('轮询次数超过 3 次', str(exc.exception))
        self.assertEqual(mock_get_task.call_count, 3)

    @patch('core.ai_client.volcengine_image2video_client.time.sleep', return_value=None)
    def test_wait_volc_task_breaks_after_consecutive_errors(self, mock_sleep):
        client = VolcengineImage2VideoClient(
            api_url='https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',
            api_token='secret',
            model='doubao-seedance-1-5-pro-251215',
        )

        with patch.object(client, '_get_volc_task', side_effect=RuntimeError('network error')) as mock_get_task:
            with self.assertRaises(RuntimeError) as exc:
                client._wait_volc_task(
                    task_id='task-123',
                    poll_interval=0,
                    max_wait_time=600,
                    timeout=30,
                    max_consecutive_errors=2,
                )

        self.assertIn('连续查询异常达到 2 次', str(exc.exception))
        self.assertEqual(mock_get_task.call_count, 2)


class SiliconFlowVideoClientTestCase(SimpleTestCase):
    @patch('core.ai_client.siliconflow_video_client.VideoGeneratorClient._localize_video_data', side_effect=lambda data, timeout: data)
    @patch('core.ai_client.siliconflow_video_client.requests.post')
    def test_generate_video_uses_submit_and_status_api(self, mock_post, mock_localize):
        submit_response = Mock()
        submit_response.raise_for_status.return_value = None
        submit_response.json.return_value = {'requestId': 'request-123'}

        queued_response = Mock()
        queued_response.raise_for_status.return_value = None
        queued_response.json.return_value = {'status': 'InQueue', 'reason': ''}

        success_response = Mock()
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = {
            'status': 'Succeed',
            'reason': '',
            'results': {
                'videos': [{'url': 'https://example.com/generated.mp4'}],
                'timings': {'inference': 123},
                'seed': 11,
            },
        }
        mock_post.side_effect = [submit_response, queued_response, success_response]

        client = SiliconFlowVideoClient(
            api_url='https://api.siliconflow.cn/v1/video/submit',
            api_token='secret',
            model='Wan-AI/Wan2.2-I2V-A14B',
        )

        result = client._generate_video(
            prompt='小猫缓慢转头看向镜头',
            model='Wan-AI/Wan2.2-I2V-A14B',
            image_base64='ZmFrZV9pbWFnZQ==',
            image_mime_type='image/jpeg',
            aspect_ratio='16:9',
            seed=11,
            poll_interval=0,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'], [{'url': 'https://example.com/generated.mp4'}])
        self.assertEqual(result['metadata']['task_id'], 'request-123')
        self.assertEqual(mock_post.call_args_list[0].args[0], 'https://api.siliconflow.cn/v1/video/submit')
        submit_payload = mock_post.call_args_list[0].kwargs['json']
        self.assertEqual(submit_payload['model'], 'Wan-AI/Wan2.2-I2V-A14B')
        self.assertEqual(submit_payload['image_size'], '1280x720')
        self.assertEqual(submit_payload['image'], 'data:image/jpeg;base64,ZmFrZV9pbWFnZQ==')
        self.assertEqual(submit_payload['seed'], 11)
        self.assertEqual(mock_post.call_args_list[1].args[0], 'https://api.siliconflow.cn/v1/video/status')
        self.assertEqual(mock_post.call_args_list[1].kwargs['json'], {'requestId': 'request-123'})

    @patch('core.ai_client.siliconflow_video_client.time.sleep', return_value=None)
    def test_wait_breaks_after_max_poll_attempts(self, mock_sleep):
        client = SiliconFlowVideoClient(
            api_url='https://api.siliconflow.cn/v1/video/submit',
            api_token='secret',
            model='Wan-AI/Wan2.2-T2V-A14B',
        )

        with patch.object(client, 'get_task_status', return_value={'status': 'InProgress'}):
            with self.assertRaises(TimeoutError) as exc:
                client.wait_for_completion(
                    task_id='request-123',
                    poll_interval=0,
                    max_wait_time=600,
                    max_poll_attempts=2,
                )

        self.assertIn('轮询次数超过 2 次', str(exc.exception))
