"""并发安全的预算预留、释放、过期和最终结算。"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from django.db import connection, transaction
from django.db.models import F, Sum
from django.utils import timezone

from ..models import AIBudgetPolicy, BudgetReservation, GenerationWorkItem
from .security import mask_sensitive_data


MONEY_QUANTUM = Decimal('0.000001')


class BudgetError(RuntimeError):
    """预算操作失败。"""


class BudgetExceeded(BudgetError):
    """硬预算不足。"""


class BudgetCurrencyMismatch(BudgetError):
    """预算与计价币种不一致。"""


class InvalidReservationState(BudgetError):
    """预算预留状态不允许当前操作。"""


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('金额必须是有效数字。')
    if amount < 0:
        raise ValueError('金额不能小于 0。')
    return amount


def _locked_get(model, pk):
    queryset = model.objects
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()
    return queryset.get(pk=pk)


class BudgetService:
    """维护预算摘要和预留明细；PostgreSQL 使用行锁，SQLite 使用写事务。"""

    @classmethod
    def _expire_locked(cls, policy: AIBudgetPolicy, now) -> Decimal:
        expired = BudgetReservation.objects.filter(
            policy=policy,
            status='active',
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        released = expired.aggregate(total=Sum('reserved_amount'))['total'] or Decimal('0')
        if released:
            expired.update(status='expired', released_at=now, updated_at=now)
            policy.reserved_amount = max(Decimal('0'), policy.reserved_amount - released)
        return released

    @classmethod
    def reserve(
        cls,
        policy: AIBudgetPolicy,
        project,
        amount: Any,
        idempotency_key: str,
        work_item=None,
        currency: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
        now=None,
    ) -> BudgetReservation:
        """幂等预留预算；相同策略和幂等键不会重复占额。"""

        amount = _money(amount)
        if not idempotency_key:
            raise ValueError('预算预留必须提供幂等键。')
        now = now or timezone.now()
        resolved_currency = (currency or policy.currency).upper()
        with transaction.atomic():
            # 所有涉及策略汇总和预留明细的事务都按 policy → reservation 顺序
            # 加锁，避免与付费调用门的重放检查形成反向锁等待。
            locked_policy = _locked_get(AIBudgetPolicy, policy.pk)
            existing_query = BudgetReservation.objects
            if connection.features.has_select_for_update:
                existing_query = existing_query.select_for_update()
            existing = existing_query.filter(
                policy_id=policy.pk,
                idempotency_key=str(idempotency_key),
            ).first()
            if existing is not None:
                if (
                    existing.project_id != project.pk
                    or existing.estimated_amount != amount
                    or existing.currency.upper() != resolved_currency
                ):
                    raise BudgetError('相同预算幂等键对应了不同请求。')
                if existing.status != 'active':
                    # 终态/人工复核记录只能用于账本查询，不能再被上层误当成
                    # 一次新的活动预算预留。真正的付费提交还会经过 PaidCallGate，
                    # 该门会进一步拒绝 active 重放。
                    raise InvalidReservationState(
                        f'相同预算幂等键已有 {existing.status} 预留，不能重新激活。'
                    )
                return existing

            if not locked_policy.is_active:
                raise BudgetError('预算策略未启用。')
            if locked_policy.period_ends_at and locked_policy.period_ends_at <= now:
                raise BudgetError('预算周期已结束。')
            if resolved_currency != locked_policy.currency.upper():
                raise BudgetCurrencyMismatch('计价币种与预算策略币种不一致。')

            cls._expire_locked(locked_policy, now)
            projected = locked_policy.spent_amount + locked_policy.reserved_amount + amount
            if (
                locked_policy.hard_limit is not None
                and projected > locked_policy.hard_limit
                and not locked_policy.allow_overage
            ):
                raise BudgetExceeded(
                    f'预算不足：需要 {amount} {resolved_currency}，'
                    f'可用 {max(Decimal("0"), locked_policy.remaining_amount)} {resolved_currency}。'
                )

            locked_policy.reserved_amount += amount
            locked_policy.save(update_fields=['reserved_amount', 'updated_at'])
            ttl = locked_policy.reservation_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
            expires_at = now + timedelta(seconds=ttl) if ttl > 0 else None
            return BudgetReservation.objects.create(
                policy=locked_policy,
                project=project,
                work_item=work_item,
                idempotency_key=str(idempotency_key),
                estimated_amount=amount,
                reserved_amount=amount,
                currency=resolved_currency,
                expires_at=expires_at,
                details=mask_sensitive_data(details or {}),
            )

    @classmethod
    def settle(
        cls,
        reservation: BudgetReservation,
        actual_amount: Any,
        usage: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
        now=None,
    ) -> BudgetReservation:
        """将预留转为实际支出；重复结算返回已结算记录。"""

        actual_amount = _money(actual_amount)
        now = now or timezone.now()
        with transaction.atomic():
            policy = _locked_get(AIBudgetPolicy, reservation.policy_id)
            locked = _locked_get(BudgetReservation, reservation.pk)
            if locked.status == 'settled':
                return locked
            if locked.status != 'active':
                raise InvalidReservationState(f'状态 {locked.status} 的预留不能结算。')
            new_reserved = max(Decimal('0'), policy.reserved_amount - locked.reserved_amount)
            projected = policy.spent_amount + new_reserved + actual_amount
            if (
                policy.hard_limit is not None
                and projected > policy.hard_limit
                and not policy.allow_overage
            ):
                raise BudgetExceeded('实际费用超过剩余额度，结算已拒绝。')

            policy.reserved_amount = new_reserved
            policy.spent_amount += actual_amount
            policy.save(update_fields=['reserved_amount', 'spent_amount', 'updated_at'])

            merged_details = dict(locked.details or {})
            if details:
                merged_details.update(mask_sensitive_data(details))
            if usage is not None:
                merged_details['usage'] = mask_sensitive_data(usage)
            locked.status = 'settled'
            locked.settled_amount = actual_amount
            locked.settled_at = now
            locked.details = merged_details
            locked.save(
                update_fields=['status', 'settled_amount', 'settled_at', 'details', 'updated_at']
            )
            if locked.work_item_id:
                updates = {
                    'actual_cost': F('actual_cost') + actual_amount,
                    'currency': locked.currency,
                    'updated_at': now,
                }
                if usage is not None:
                    updates['usage'] = mask_sensitive_data(usage)
                GenerationWorkItem.objects.filter(pk=locked.work_item_id).update(**updates)
            return locked

    @classmethod
    def release(cls, reservation: BudgetReservation, now=None) -> BudgetReservation:
        """释放未使用的活动预留；重复释放保持幂等。"""

        now = now or timezone.now()
        with transaction.atomic():
            policy = _locked_get(AIBudgetPolicy, reservation.policy_id)
            locked = _locked_get(BudgetReservation, reservation.pk)
            if locked.status in {'released', 'expired'}:
                return locked
            if locked.status != 'active':
                raise InvalidReservationState(f'状态 {locked.status} 的预留不能释放。')
            policy.reserved_amount = max(
                Decimal('0'), policy.reserved_amount - locked.reserved_amount
            )
            policy.save(update_fields=['reserved_amount', 'updated_at'])
            locked.status = 'released'
            locked.released_at = now
            locked.save(update_fields=['status', 'released_at', 'updated_at'])
            return locked

    @classmethod
    def expire_active(cls, policy: AIBudgetPolicy, now=None) -> int:
        """释放指定策略中过期的活动预留，返回过期记录数。"""

        now = now or timezone.now()
        with transaction.atomic():
            locked_policy = _locked_get(AIBudgetPolicy, policy.pk)
            queryset = BudgetReservation.objects.filter(
                policy=locked_policy,
                status='active',
                expires_at__isnull=False,
                expires_at__lte=now,
            )
            count = queryset.count()
            cls._expire_locked(locked_policy, now)
            locked_policy.save(update_fields=['reserved_amount', 'updated_at'])
            return count

    @classmethod
    def mark_ambiguous(
        cls,
        reservation: BudgetReservation,
        reason: str,
    ) -> BudgetReservation:
        """上游是否受理或计费不明确时冻结预留，禁止自动释放。"""

        with transaction.atomic():
            locked = _locked_get(BudgetReservation, reservation.pk)
            if locked.status == 'ambiguous':
                return locked
            if locked.status != 'active':
                raise InvalidReservationState(f'状态 {locked.status} 不能标记为结果不明确。')
            details = dict(locked.details or {})
            details['ambiguous_reason'] = mask_sensitive_data(reason)
            locked.status = 'ambiguous'
            locked.details = details
            locked.save(update_fields=['status', 'details', 'updated_at'])
            return locked

    @classmethod
    def mark_manual_review(
        cls,
        reservation: BudgetReservation,
        reason: str = '',
    ) -> BudgetReservation:
        """把活动或不明确预留转入人工复核，金额继续冻结。"""

        with transaction.atomic():
            locked = _locked_get(BudgetReservation, reservation.pk)
            if locked.status == 'manual_review':
                return locked
            if locked.status not in {'active', 'ambiguous'}:
                raise InvalidReservationState(f'状态 {locked.status} 不能进入人工复核。')
            details = dict(locked.details or {})
            if reason:
                details['manual_review_reason'] = mask_sensitive_data(reason)
            locked.status = 'manual_review'
            locked.details = details
            locked.save(update_fields=['status', 'details', 'updated_at'])
            return locked

    @classmethod
    def resolve_manual_review(
        cls,
        reservation: BudgetReservation,
        *,
        charged: bool,
        actual_amount=None,
        note: str = '',
        now=None,
    ) -> BudgetReservation:
        """人工核对后原子结算或释放冻结金额。

        ``ambiguous/manual_review`` 代表上游可能已经受理请求，定时任务绝不能
        自动释放或重提。只有管理员提交明确结论后，才在同一事务中同时调整
        策略汇总和预留明细，避免两个账本发生短暂不一致。
        """

        now = now or timezone.now()
        with transaction.atomic():
            policy = _locked_get(AIBudgetPolicy, reservation.policy_id)
            locked = _locked_get(BudgetReservation, reservation.pk)
            if locked.status not in {'ambiguous', 'manual_review'}:
                raise InvalidReservationState(
                    f'状态 {locked.status} 不是待人工复核预留。'
                )
            details = dict(locked.details or {})
            details['manual_resolution'] = 'charged' if charged else 'not_charged'
            if note:
                details['manual_resolution_note'] = mask_sensitive_data(note)

            policy.reserved_amount = max(
                Decimal('0'), policy.reserved_amount - locked.reserved_amount
            )
            if charged:
                amount = _money(
                    locked.reserved_amount if actual_amount is None else actual_amount
                )
                policy.spent_amount += amount
                locked.status = 'settled'
                locked.settled_amount = amount
                locked.settled_at = now
                locked.save(
                    update_fields=[
                        'status', 'settled_amount', 'settled_at', 'details', 'updated_at'
                    ]
                )
            else:
                locked.status = 'released'
                locked.released_at = now
                locked.save(
                    update_fields=['status', 'released_at', 'details', 'updated_at']
                )
            locked.details = details
            # 上面的 update_fields 需要在 details 赋值后持久化，因此统一补一次保存。
            locked.save(update_fields=['details', 'updated_at'])
            policy.save(update_fields=['reserved_amount', 'spent_amount', 'updated_at'])
            return locked


def reserve_budget(*args, **kwargs):
    """函数式预算预留入口。"""

    return BudgetService.reserve(*args, **kwargs)


def settle_budget(*args, **kwargs):
    """函数式预算结算入口。"""

    return BudgetService.settle(*args, **kwargs)
