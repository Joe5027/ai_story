"""工作项状态的 Redis/SSE 最佳努力事件出口。"""

import json
import logging
import time

import redis
from django.conf import settings

from .security import mask_sensitive_data


logger = logging.getLogger(__name__)


def publish_work_item_event(work_item, event_type: str, **metadata) -> bool:
    """发布不含完整提示词和凭据的工作项事件，Redis 故障不改变数据库事实。"""

    if not getattr(settings, 'AI_WORK_ITEM_EVENTS_ENABLED', True):
        return False
    channel = (
        f'ai_story:project:{work_item.project_id}:stage:'
        f'{work_item.stage_type or work_item.capability}'
    )
    message = mask_sensitive_data({
        'type': 'generation_work_item',
        'event': event_type,
        'project_id': str(work_item.project_id),
        'stage': work_item.stage_type,
        'work_item_id': str(work_item.pk),
        'capability': work_item.capability,
        'status': work_item.status,
        'attempt_count': work_item.attempt_count,
        'storyboard_id': str(work_item.storyboard_id or ''),
        'tile_index': work_item.tile_index,
        'segment_index': work_item.segment_index,
        'metadata': metadata,
        'timestamp': time.time(),
    })
    try:
        client = redis.from_url(
            getattr(settings, 'REDIS_PUBSUB_URL', 'redis://localhost:6379/2'),
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        client.publish(channel, json.dumps(message, ensure_ascii=False))
        return True
    except Exception as error:
        logger.debug('工作项事件发布失败，不影响数据库状态: %s', error)
        return False
