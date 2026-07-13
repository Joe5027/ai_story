"""生成工作项 Celery 入口。

消息队列只负责唤醒：幂等、租约、恢复和终态都由数据库服务保证。
"""

import logging
import uuid

from celery import current_app, shared_task
from django.db import connection, transaction

from .models import GenerationWorkItem
from .services.scheduler import (
    ArtifactRetentionService,
    WorkItemScheduler,
    queue_for_capability,
)


logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='apps.inference.tasks.dispatch_work_item',
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_work_item(self, work_item_id):
    """在能力队列 worker 中用数据库租约执行一个工作项。"""

    request_id = getattr(self.request, 'id', '') or str(uuid.uuid4())
    return WorkItemScheduler().dispatch(work_item_id, lease_owner=f'celery:{request_id}')


def _enqueue_dispatch(work_item_id):
    item = GenerationWorkItem.objects.only('capability').get(pk=work_item_id)
    result = dispatch_work_item.apply_async(
        args=[str(work_item_id)],
        queue=queue_for_capability(item.capability),
    )
    # 把消息 ID 保存到持久化快照，取消接口才能撤销尚未被 worker 领取的消息。
    with transaction.atomic():
        queryset = GenerationWorkItem.objects
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update()
        locked = queryset.get(pk=work_item_id)
        locked.route_snapshot = {
            **(locked.route_snapshot or {}),
            'celery_task_id': str(result.id),
            'celery_queue': queue_for_capability(locked.capability),
        }
        locked.save(update_fields=['route_snapshot', 'updated_at'])
    return str(result.id)


@shared_task(
    name='apps.inference.tasks.enqueue_work_item',
    queue='orchestration',
)
def enqueue_work_item(work_item_id):
    """编排队列将持久化工作项投递到 llm/image/video 专用队列。"""

    return {
        'work_item_id': str(work_item_id),
        'dispatch_task_id': _enqueue_dispatch(work_item_id),
    }


@shared_task(
    name='apps.inference.tasks.reconcile_work_items',
    queue='maintenance',
)
def reconcile_work_items(limit=100):
    """回收过期租约、核对 Agent job 并补发安全的等待项。"""

    return WorkItemScheduler().reconcile(limit=limit, enqueue=_enqueue_dispatch)


@shared_task(
    name='apps.inference.tasks.cancel_work_item',
    queue='orchestration',
)
def cancel_work_item(work_item_id):
    """固化取消状态，并传播到 Runtime Agent 与尚未执行的 Celery 消息。"""

    result = WorkItemScheduler().cancel(work_item_id)
    item = GenerationWorkItem.objects.only('route_snapshot').get(pk=work_item_id)
    celery_task_id = str((item.route_snapshot or {}).get('celery_task_id') or '')
    celery_revoked = False
    if celery_task_id:
        try:
            # 不使用 terminate=True 强杀进程；DB cancelled 状态和 Agent DELETE
            # 负责终止实际工作，revoke 只拦截仍在 broker 中等待的消息。
            current_app.control.revoke(celery_task_id, terminate=False)
            celery_revoked = True
        except Exception as error:
            logger.warning('Celery 工作项撤销广播失败 work_item=%s error=%s', work_item_id, error)
    return {**result, 'celery_revoked': celery_revoked}


@shared_task(
    name='apps.inference.tasks.cleanup_expired_artifacts',
    queue='maintenance',
)
def cleanup_expired_artifacts(limit=200):
    """按显式开关清理中央存储内过期的 intermediate/failed 文件。"""

    return ArtifactRetentionService.cleanup(limit=limit)
