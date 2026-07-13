#!/usr/bin/env python
"""Run a real SiliconFlow video generation smoke through the repo client."""

import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / 'backend'
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django  # noqa: E402

django.setup()

from core.ai_client.siliconflow_video_client import SiliconFlowVideoClient  # noqa: E402


def parse_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, '').strip()
    if not raw_value:
        return default
    return int(raw_value)


def main() -> int:
    api_key = os.getenv('SILICONFLOW_API_KEY', '').strip()
    if not api_key:
        print(json.dumps({
            'ok': False,
            'error': 'SILICONFLOW_API_KEY is required',
        }, ensure_ascii=False))
        return 2

    model = os.getenv('SILICONFLOW_VIDEO_MODEL', 'Wan-AI/Wan2.2-T2V-A14B').strip()
    prompt = os.getenv(
        'SILICONFLOW_VIDEO_PROMPT',
        'A calm cinematic shot of a small paper boat floating on a clear pond, gentle ripples, natural daylight.',
    ).strip()
    api_url = os.getenv('SILICONFLOW_VIDEO_API_URL', 'https://api.siliconflow.cn/v1/video/submit').strip()
    image_size = os.getenv('SILICONFLOW_VIDEO_IMAGE_SIZE', '1280x720').strip()
    negative_prompt = os.getenv('SILICONFLOW_VIDEO_NEGATIVE_PROMPT', '').strip()
    image_base64 = os.getenv('SILICONFLOW_VIDEO_IMAGE_BASE64', '').strip()
    image_uri = os.getenv('SILICONFLOW_VIDEO_IMAGE_URI', '').strip()
    image_mime_type = os.getenv('SILICONFLOW_VIDEO_IMAGE_MIME_TYPE', 'image/jpeg').strip()
    poll_interval = parse_int_env('SILICONFLOW_VIDEO_POLL_INTERVAL', 10)
    max_wait_time = parse_int_env('SILICONFLOW_VIDEO_MAX_WAIT', 900)
    timeout = parse_int_env('SILICONFLOW_VIDEO_HTTP_TIMEOUT', 120)

    client = SiliconFlowVideoClient(
        api_url=api_url,
        api_token=api_key,
        model=model,
        timeout=timeout,
    )

    result = client._generate_video(
        prompt=prompt,
        model=model,
        image_size=image_size,
        negative_prompt=negative_prompt or None,
        image_base64=image_base64 or None,
        image_uri=image_uri or None,
        image_mime_type=image_mime_type,
        poll_interval=poll_interval,
        max_wait_time=max_wait_time,
        timeout=timeout,
    )

    videos = result.get('data') or []
    metadata = result.get('metadata') or {}
    safe_videos = []
    for video in videos:
        item = dict(video)
        if item.get('original_url') and item.get('original_url') != item.get('url'):
            item['original_url_present'] = True
            item.pop('original_url', None)
        safe_videos.append(item)

    print(json.dumps({
        'ok': True,
        'model': model,
        'request_url': metadata.get('request_url'),
        'status_url': metadata.get('status_url'),
        'task_id': metadata.get('task_id'),
        'latency_ms': metadata.get('latency_ms'),
        'timings': metadata.get('timings', {}),
        'seed': metadata.get('seed'),
        'videos': safe_videos,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
