"""SiliconFlow video generation client."""

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from core.ai_client.image2video_client import VideoGeneratorClient


class SiliconFlowVideoClient(VideoGeneratorClient):
    """Client for SiliconFlow /v1/video/submit and /v1/video/status."""

    TERMINAL_SUCCESS = 'Succeed'
    TERMINAL_FAILED = 'Failed'
    PENDING_STATUSES = {'InQueue', 'InProgress'}

    def _build_create_video_url(self) -> str:
        normalized = self.base_url.rstrip('/')
        if normalized.endswith('/video/submit'):
            return normalized
        if normalized.endswith('/video/status'):
            return f"{normalized[:-len('/video/status')]}/video/submit"
        if normalized.endswith('/v1'):
            return f'{normalized}/video/submit'
        return normalized

    def _build_status_url(self) -> str:
        normalized = self.base_url.rstrip('/')
        if normalized.endswith('/video/status'):
            return normalized
        if normalized.endswith('/video/submit'):
            return f"{normalized[:-len('/video/submit')]}/video/status"
        if normalized.endswith('/v1'):
            return f'{normalized}/video/status'
        parsed = urlparse(normalized)
        path = parsed.path.rstrip('/')
        if path.endswith('/video/submit'):
            path = f"{path[:-len('/video/submit')]}/video/status"
            return parsed._replace(path=path, params='', query='', fragment='').geturl()
        return f'{normalized}/status'

    def _image_size_from_aspect_ratio(self, aspect_ratio: Optional[str]) -> str:
        mapping = {
            '16:9': '1280x720',
            '9:16': '720x1280',
            '1:1': '960x960',
            '1280x720': '1280x720',
            '720x1280': '720x1280',
            '960x960': '960x960',
        }
        return mapping.get(str(aspect_ratio or '').strip(), '1280x720')

    def _build_image_value(
        self,
        image_uri: Optional[str],
        image_base64: Optional[str],
        image_mime_type: str,
        timeout: int,
    ) -> str:
        if image_base64:
            if image_base64.startswith('data:'):
                return image_base64
            return f'data:{image_mime_type};base64,{image_base64}'

        image_url = image_uri.get('url') if isinstance(image_uri, dict) else image_uri
        if not image_url:
            return ''
        if image_url.startswith('data:') or image_url.startswith('http://') or image_url.startswith('https://'):
            return image_url

        resolved_base64 = self._resolve_image_base64(image_url, None, timeout)
        if resolved_base64:
            return f'data:{image_mime_type};base64,{resolved_base64}'
        return ''

    def create_video_task(
        self,
        prompt: str,
        model: str = 'Wan-AI/Wan2.2-T2V-A14B',
        image_size: Optional[str] = None,
        aspect_ratio: str = '16:9',
        negative_prompt: Optional[str] = None,
        image_uri: Optional[str] = None,
        image_base64: Optional[str] = None,
        image_mime_type: str = 'image/jpeg',
        seed: Optional[int] = None,
        **kwargs,
    ) -> str:
        """Submit a SiliconFlow video task and return requestId."""
        timeout = kwargs.get('timeout', self.timeout)
        final_prompt = self._build_prompt_text(
            prompt=prompt,
            negative_prompt=negative_prompt,
            camera_movement_description=kwargs.get('camera_movement_description'),
        )
        payload: Dict[str, Any] = {
            'model': model,
            'prompt': final_prompt,
            'image_size': image_size or self._image_size_from_aspect_ratio(aspect_ratio),
        }

        image_value = self._build_image_value(
            image_uri=image_uri,
            image_base64=image_base64,
            image_mime_type=image_mime_type,
            timeout=timeout,
        )
        if image_value:
            payload['image'] = image_value
        if negative_prompt:
            payload['negative_prompt'] = negative_prompt
        if seed is not None:
            payload['seed'] = seed

        try:
            response = requests.post(
                self._build_create_video_url(),
                json=payload,
                headers=self.headers,
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as exc:
            raise Exception(f'创建硅基流动视频任务失败: {str(exc)}') from exc

        request_id = result.get('requestId') or result.get('request_id')
        if not request_id:
            raise Exception(f'响应格式错误: 缺少requestId字段: {result}')
        return request_id

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Retrieve a SiliconFlow video task status."""
        try:
            response = requests.post(
                self._build_status_url(),
                json={'requestId': task_id},
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            raise Exception(f'查询硅基流动视频任务失败: {str(exc)}') from exc

    def wait_for_completion(
        self,
        task_id: str,
        poll_interval: int = 10,
        max_wait_time: int = 900,
        callback: Optional[callable] = None,
        max_poll_attempts: Optional[int] = None,
        max_consecutive_errors: int = 3,
    ) -> Dict[str, Any]:
        """Poll until SiliconFlow returns Succeed or Failed."""
        start_time = time.time()
        attempts = 0
        consecutive_errors = 0

        while True:
            if time.time() - start_time > max_wait_time:
                raise TimeoutError(f'硅基流动视频任务超时: 等待时间超过 {max_wait_time} 秒')
            if max_poll_attempts is not None and attempts >= max_poll_attempts:
                raise TimeoutError(f'硅基流动视频任务轮询次数超过 {max_poll_attempts} 次')

            attempts += 1
            try:
                task_info = self.get_task_status(task_id)
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise
                time.sleep(poll_interval)
                continue

            if callback:
                callback(task_info)

            status = task_info.get('status')
            if status == self.TERMINAL_SUCCESS:
                return task_info
            if status == self.TERMINAL_FAILED:
                reason = task_info.get('reason') or '未知错误'
                raise Exception(f'硅基流动视频任务失败: {reason}')
            if status not in self.PENDING_STATUSES:
                reason = task_info.get('reason') or '未知状态'
                raise Exception(f'硅基流动视频任务状态异常: {status} - {reason}')

            time.sleep(poll_interval)

    def _extract_video_data(self, result: Dict[str, Any]) -> List[dict]:
        videos = (result.get('results') or {}).get('videos') or []
        video_data = []
        for item in videos:
            if isinstance(item, dict):
                url = item.get('url')
                if url:
                    video_data.append(item)
            elif item:
                video_data.append({'url': item})
        return video_data

    def _generate_video(
        self,
        prompt: str,
        poll_interval: int = 10,
        max_wait_time: int = 900,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate a video synchronously through SiliconFlow task polling."""
        start_time = time.time()
        timeout = kwargs.get('timeout', self.timeout)
        task_id = self.create_video_task(prompt=prompt, **kwargs)
        result = self.wait_for_completion(
            task_id,
            poll_interval=poll_interval,
            max_wait_time=max_wait_time,
            max_poll_attempts=kwargs.get('max_poll_attempts'),
            max_consecutive_errors=kwargs.get('max_consecutive_errors', 3),
        )
        video_data = self._extract_video_data(result)
        if not video_data:
            raise Exception(f'硅基流动视频任务成功但未返回视频URL: {result}')

        localized_video_data = self._localize_video_data(video_data, timeout)
        return {
            'success': True,
            'data': localized_video_data,
            'metadata': {
                'latency_ms': int((time.time() - start_time) * 1000),
                'model': kwargs.get('model') or self.model,
                'request_url': self._build_create_video_url(),
                'status_url': self._build_status_url(),
                'task_id': task_id,
                'seed': (result.get('results') or {}).get('seed'),
                'timings': (result.get('results') or {}).get('timings', {}),
            },
        }
