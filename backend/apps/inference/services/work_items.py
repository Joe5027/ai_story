"""生成工作项幂等创建、租约、心跳和受控状态机。"""

import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from django.db import connection, transaction
from django.utils import timezone

from ..models import GenerationWorkItem
from .security import mask_sensitive_data


class InvalidWorkItemTransition(RuntimeError):
    """工作项状态、版本或租约不允许当前操作。"""


def _locked_work_item(pk):
    queryset = GenerationWorkItem.objects
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()
    return queryset.get(pk=pk)


class WorkItemService:
    """维护 waiting→leased→running/retry_wait→终态的稳定执行状态。"""

    TRANSITIONS = {
        'waiting': {'leased', 'failed', 'cancelled'},
        'leased': {'waiting', 'running', 'retry_wait', 'failed', 'cancelled'},
        'running': {'retry_wait', 'succeeded', 'failed', 'cancelled'},
        'retry_wait': {'waiting', 'failed', 'cancelled'},
        'succeeded': set(),
        'failed': set(),
        'cancelled': set(),
    }
    BLOCKED_UPDATE_FIELDS = {
        'id', 'pk', 'project', 'project_id', 'status', 'idempotency_key',
        'attempt_count', 'version', 'created_at', 'updated_at',
    }

    @staticmethod
    def requires_paid_manual_review(work_item, reservation_statuses=None) -> bool:
        """判断失败项是否必须先完成付费结果人工核对。

        ``PAID_RESULT_AMBIGUOUS`` 本身不能证明已经完成复核；只有关联预留全部
        进入 settled/released，才能允许用户创建一条全新幂等键的人工重试。
        active 也可能处于“已提交、未记账”窗口，因此同样必须阻断。
        """

        if reservation_statuses is None:
            reservation_statuses = work_item.budget_reservations.values_list(
                'status', flat=True
            )
        statuses = {str(status) for status in reservation_statuses}
        manual_error = (
            work_item.error_code == 'PAID_RESULT_AMBIGUOUS'
            or work_item.error_class == 'manual_review'
        )
        if statuses.intersection({'active', 'ambiguous', 'manual_review'}):
            return True
        if manual_error:
            return not statuses or not statuses.issubset({'settled', 'released'})
        return False

    @classmethod
    def create(
        cls,
        project,
        capability: str,
        idempotency_key=None,
        **defaults,
    ) -> GenerationWorkItem:
        """项目范围幂等创建；同键不同能力或请求内容视为冲突。"""

        key = str(idempotency_key or uuid.uuid4())
        if len(key) > 128:
            raise ValueError('工作项幂等键不能超过 128 个字符。')
        safe_defaults = dict(defaults)
        safe_request = mask_sensitive_data(safe_defaults.get('request_parameters', {}))
        safe_defaults['request_parameters'] = safe_request
        item, created = GenerationWorkItem.objects.get_or_create(
            project=project,
            idempotency_key=key,
            defaults={'capability': capability, **safe_defaults},
        )
        if not created and (
            item.capability != capability or item.request_parameters != safe_request
        ):
            raise InvalidWorkItemTransition('相同工作项幂等键对应了不同请求。')
        return item

    @classmethod
    def transition(
        cls,
        work_item: GenerationWorkItem,
        target_status: str,
        expected_status: Optional[str] = None,
        expected_version: Optional[int] = None,
        error: Optional[Dict[str, Any]] = None,
        lease_owner: str = '',
        lease_seconds: int = 60,
        allow_early_retry: bool = False,
        now=None,
        **updates,
    ) -> GenerationWorkItem:
        """锁定后迁移状态，并用可选版本号拒绝旧执行者写入。"""

        now = now or timezone.now()
        with transaction.atomic():
            locked = _locked_work_item(work_item.pk)
            if expected_status is not None and locked.status != expected_status:
                raise InvalidWorkItemTransition(
                    f'期望状态 {expected_status}，实际状态为 {locked.status}。'
                )
            if expected_version is not None and locked.version != expected_version:
                raise InvalidWorkItemTransition(
                    f'期望版本 {expected_version}，实际版本为 {locked.version}。'
                )
            if locked.status == target_status:
                return locked
            if target_status not in cls.TRANSITIONS.get(locked.status, set()):
                raise InvalidWorkItemTransition(
                    f'不允许从 {locked.status} 迁移到 {target_status}。'
                )
            lease_controlled = locked.status in {'leased', 'running'} and target_status not in {
                'cancelled'
            }
            if lease_controlled and (
                not lease_owner or locked.lease_owner != lease_owner
            ):
                raise InvalidWorkItemTransition('只有当前租约持有者可以推进工作项。')
            if locked.status == 'retry_wait' and target_status == 'waiting':
                if locked.next_retry_at and locked.next_retry_at > now and not allow_early_retry:
                    raise InvalidWorkItemTransition('工作项尚未到下次重试时间。')
                if locked.attempt_count >= locked.max_attempts:
                    raise InvalidWorkItemTransition('工作项已达到最大尝试次数。')

            for field, value in updates.items():
                if field in cls.BLOCKED_UPDATE_FIELDS or not hasattr(locked, field):
                    raise ValueError(f'不允许更新工作项字段：{field}')
                setattr(locked, field, mask_sensitive_data(value, field))

            locked.status = target_status
            if target_status == 'leased':
                if not lease_owner:
                    raise InvalidWorkItemTransition('认领工作项必须提供 lease_owner。')
                if lease_seconds < 1:
                    raise ValueError('租约秒数必须大于 0。')
                locked.lease_owner = lease_owner
                locked.claimed_at = now
                locked.heartbeat_at = now
                locked.lease_expires_at = now + timedelta(seconds=lease_seconds)
            elif target_status == 'running':
                if locked.attempt_count >= locked.max_attempts:
                    raise InvalidWorkItemTransition('工作项已达到最大尝试次数。')
                if locked.lease_expires_at and locked.lease_expires_at <= now:
                    raise InvalidWorkItemTransition('工作项租约不存在或已过期。')
                locked.attempt_count += 1
                locked.started_at = now
            elif target_status == 'retry_wait':
                locked.next_retry_at = locked.next_retry_at or now
                cls._clear_lease(locked)
            elif target_status == 'waiting':
                locked.next_retry_at = None
                locked.completed_at = None
                locked.error_class = ''
                locked.error_code = ''
                locked.error_message = ''
                cls._clear_lease(locked)
            elif target_status in {'succeeded', 'failed', 'cancelled'}:
                locked.completed_at = now
                cls._clear_lease(locked)

            if target_status in {'failed', 'retry_wait'} and error:
                safe_error = mask_sensitive_data(error)
                locked.error_class = str(safe_error.get('category', safe_error.get('class', '')))
                locked.error_code = str(safe_error.get('code', ''))
                locked.error_message = str(safe_error.get('message', ''))
            locked.version += 1
            locked.save()
            return locked

    @staticmethod
    def _clear_lease(work_item: GenerationWorkItem) -> None:
        work_item.lease_owner = ''
        work_item.lease_expires_at = None
        work_item.heartbeat_at = None

    @classmethod
    def claim(
        cls,
        work_item,
        lease_owner: str,
        lease_seconds: int = 60,
        **kwargs,
    ):
        return cls.transition(
            work_item,
            'leased',
            expected_status='waiting',
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            **kwargs,
        )

    @classmethod
    def heartbeat(
        cls,
        work_item,
        lease_owner: str,
        extend_seconds: int = 60,
        expected_version: Optional[int] = None,
        now=None,
    ):
        """仅当前租约持有者可续租；过期租约不能复活。"""

        now = now or timezone.now()
        with transaction.atomic():
            locked = _locked_work_item(work_item.pk)
            if locked.status not in {'leased', 'running'}:
                raise InvalidWorkItemTransition('只有 leased/running 工作项可以续租。')
            if expected_version is not None and locked.version != expected_version:
                raise InvalidWorkItemTransition('工作项版本已变化。')
            if locked.lease_owner != lease_owner:
                raise InvalidWorkItemTransition('租约持有者不匹配。')
            if locked.lease_expires_at and locked.lease_expires_at <= now:
                raise InvalidWorkItemTransition('工作项租约已过期。')
            if extend_seconds < 1:
                raise ValueError('续租秒数必须大于 0。')
            locked.heartbeat_at = now
            locked.lease_expires_at = now + timedelta(seconds=extend_seconds)
            locked.version += 1
            locked.save(update_fields=['heartbeat_at', 'lease_expires_at', 'version', 'updated_at'])
            return locked

    @classmethod
    def reclaim_expired_lease(
        cls,
        work_item,
        lease_owner: str,
        lease_seconds: int = 60,
        expected_version: Optional[int] = None,
        now=None,
    ):
        """仅重新认领尚未运行且租约已过期的工作项。"""

        if not lease_owner:
            raise ValueError('重新认领必须提供 lease_owner。')
        if lease_seconds < 1:
            raise ValueError('租约秒数必须大于 0。')
        now = now or timezone.now()
        with transaction.atomic():
            locked = _locked_work_item(work_item.pk)
            if locked.status != 'leased':
                raise InvalidWorkItemTransition('只有 leased 工作项可以安全重新认领。')
            if expected_version is not None and locked.version != expected_version:
                raise InvalidWorkItemTransition('工作项版本已变化。')
            if not locked.lease_expires_at or locked.lease_expires_at > now:
                raise InvalidWorkItemTransition('现有租约尚未过期。')
            locked.lease_owner = lease_owner
            locked.claimed_at = now
            locked.heartbeat_at = now
            locked.lease_expires_at = now + timedelta(seconds=lease_seconds)
            locked.version += 1
            locked.save(
                update_fields=[
                    'lease_owner', 'claimed_at', 'heartbeat_at', 'lease_expires_at',
                    'version', 'updated_at',
                ]
            )
            return locked

    @classmethod
    def start(cls, work_item, lease_owner: str, **kwargs):
        return cls.transition(
            work_item,
            'running',
            expected_status='leased',
            lease_owner=lease_owner,
            **kwargs,
        )

    @classmethod
    def schedule_retry(
        cls,
        work_item,
        delay_seconds: int,
        lease_owner: str,
        error=None,
        now=None,
        **kwargs,
    ):
        now = now or timezone.now()
        if delay_seconds < 0:
            raise ValueError('重试延迟不能小于 0。')
        return cls.transition(
            work_item,
            'retry_wait',
            error=error,
            lease_owner=lease_owner,
            now=now,
            next_retry_at=now + timedelta(seconds=delay_seconds),
            **kwargs,
        )

    @classmethod
    def retry(cls, work_item, force: bool = False, **kwargs):
        return cls.transition(
            work_item,
            'waiting',
            expected_status='retry_wait',
            allow_early_retry=force,
            **kwargs,
        )

    @classmethod
    def succeed(cls, work_item, lease_owner: str, **kwargs):
        return cls.transition(
            work_item,
            'succeeded',
            expected_status='running',
            lease_owner=lease_owner,
            **kwargs,
        )

    @classmethod
    def fail(cls, work_item, error=None, lease_owner: str = '', **kwargs):
        return cls.transition(
            work_item,
            'failed',
            error=error,
            lease_owner=lease_owner,
            **kwargs,
        )

    @classmethod
    def cancel(cls, work_item, **kwargs):
        return cls.transition(work_item, 'cancelled', **kwargs)

    @classmethod
    def release_lease(cls, work_item, lease_owner: str, **kwargs):
        return cls.transition(
            work_item,
            'waiting',
            expected_status='leased',
            lease_owner=lease_owner,
            **kwargs,
        )

    @classmethod
    def reserve(cls, work_item, **kwargs):
        """兼容旧调用：新状态机在 waiting 阶段由预算服务独立预留。"""

        return cls.transition(work_item, 'waiting', **kwargs)

    queue = reserve
