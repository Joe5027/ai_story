"""云端出站、手工价格和预算的强制三重门。"""

from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import AIBudgetPolicy, BudgetReservation, ProjectAISettings
from .pricing import PricingService
from .security import mask_sensitive_data


class PaidCallDenied(RuntimeError):
    code = 'BUDGET_DENIED'

    def __init__(self, message, code=None):
        super().__init__(message)
        if code:
            self.code = code


def _locked(queryset):
    return queryset.select_for_update() if connection.features.has_select_for_update else queryset


class PaidCallGate:
    """在任何外部生成 POST 前完成授权、价格与预算预留。

    检查顺序固定为云端数据授权 → 手工价目表 → 项目预算 → 全局日/月预算。
    这样既不会为了估价泄露业务数据，也能保证默认 0 预算的全新安装绝无自动
    付费路径。主观质量不满意不调用此类；只能由用户显式“API 重生成”进入。
    """

    REPLAY_DENIALS = {
        'active': (
            'PAID_REQUEST_IN_PROGRESS',
            '相同幂等键已有活动预留，可能正在提交，禁止再次调用付费 Provider',
        ),
        'settled': (
            'PAID_REQUEST_ALREADY_SETTLED',
            '相同幂等键的付费调用已经结算，禁止重复提交',
        ),
        'ambiguous': (
            'PAID_RESULT_AMBIGUOUS',
            '相同幂等键的付费结果尚不明确，必须先人工复核',
        ),
        'manual_review': (
            'PAID_RESULT_AMBIGUOUS',
            '相同幂等键的付费结果正在人工复核，禁止重复提交',
        ),
        'released': (
            'PAID_RESERVATION_CLOSED',
            '相同幂等键的预算预留已经释放，不能复用为新的付费调用',
        ),
        'expired': (
            'PAID_RESERVATION_CLOSED',
            '相同幂等键的预算预留已经过期，不能复用为新的付费调用',
        ),
    }

    @classmethod
    def reserve(
        cls,
        project,
        provider,
        capability: str,
        usage,
        idempotency_key: str,
        work_item=None,
        automatic_fallback: bool = True,
        confirmed_max_cost_cny=None,
    ):
        if getattr(provider, 'deployment_mode', 'api') != 'api':
            return None

        now = timezone.now()
        with transaction.atomic():
            settings_query = ProjectAISettings.objects.filter(project=project, is_active=True)
            # PostgreSQL 不允许在 nullable 外连接一侧执行 FOR UPDATE。这里先只锁
            # ProjectAISettings 自身，再按 budget_policy_id 单独锁预算策略行。
            project_settings = _locked(settings_query).first()
            if not project_settings or not (
                project_settings.allow_cloud_data_transfer
                and project_settings.cloud_authorized_by_id
                and project_settings.cloud_authorized_at
            ):
                raise PaidCallDenied('项目未显式授权提示词和媒体出站', 'CLOUD_NOT_AUTHORIZED')
            if automatic_fallback and not project_settings.allow_paid_fallback:
                raise PaidCallDenied('项目未启用自动付费回退', 'CLOUD_NOT_AUTHORIZED')

            # 日/月额度是全局边界，而不只是“当前 policy”边界。按主键固定顺序
            # 锁住全部活动策略，避免两个项目绑定不同 policy 时并发穿透全局上限。
            active_policies = list(_locked(
                AIBudgetPolicy.objects.filter(is_active=True).order_by('pk')
            ))
            if project_settings.budget_policy_id is None:
                policy = min(active_policies, key=lambda item: item.created_at, default=None)
            else:
                policy = next((
                    item for item in active_policies
                    if item.pk == project_settings.budget_policy_id
                ), None)
            if policy is None:
                raise PaidCallDenied('未配置全局预算策略')

            # 预算行已锁定，同一策略下的并发预留会串行到这里。旧预留是调用账本
            # 事实，不是一张可以再次消费的“许可证”；任何状态都必须在 invoke 前
            # 明确拒绝，尤其 active 也可能代表首个 worker 已经提交但尚未结算。
            existing = _locked(BudgetReservation.objects.filter(
                policy=policy,
                idempotency_key=idempotency_key,
            )).first()
            if existing:
                cls._deny_reservation_replay(
                    existing,
                    project=project,
                    provider=provider,
                    capability=capability,
                    work_item=work_item,
                )

            estimate = PricingService.estimate(
                capability=capability,
                model_name=provider.model_name,
                usage=usage,
                provider=provider,
            )
            if not estimate.lines or estimate.missing_usage:
                raise PaidCallDenied('缺少匹配当前用量的有效价目表', 'PRICE_MISSING')

            project_limit = Decimal(project_settings.project_budget_cny)
            daily_limit = Decimal(policy.daily_limit_cny)
            monthly_limit = Decimal(policy.monthly_limit_cny)
            if project_limit <= 0 or daily_limit <= 0 or monthly_limit <= 0:
                raise PaidCallDenied('项目或全局预算仍为默认 0')

            reservations = BudgetReservation.objects.exclude(status__in=['released', 'expired'])
            project_used = cls._reservation_total(reservations.filter(project=project))
            daily_used = cls._reservation_total(reservations.filter(created_at__date=timezone.localdate()))
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            monthly_used = cls._reservation_total(reservations.filter(created_at__gte=month_start))
            amount = estimate.amount_cny
            if confirmed_max_cost_cny is not None:
                try:
                    confirmed_max = Decimal(str(confirmed_max_cost_cny))
                except Exception as error:
                    raise PaidCallDenied(
                        '本次确认的最大费用格式无效', 'BUDGET_DENIED'
                    ) from error
                if confirmed_max < 0 or amount > confirmed_max:
                    raise PaidCallDenied(
                        '当前价目表估算已超过用户本次确认的最大费用',
                        'BUDGET_DENIED',
                    )
            if project_used + amount > project_limit:
                raise PaidCallDenied('预计费用将超过项目预算')
            if daily_used + amount > daily_limit:
                raise PaidCallDenied('预计费用将超过全局每日预算')
            if monthly_used + amount > monthly_limit:
                raise PaidCallDenied('预计费用将超过全局月度预算')

            reservation = BudgetReservation.objects.create(
                policy=policy,
                project=project,
                work_item=work_item,
                idempotency_key=idempotency_key,
                estimated_amount=amount,
                reserved_amount=amount,
                currency='CNY',
                details=mask_sensitive_data({
                    'capability': capability,
                    'provider_id': str(provider.id),
                    'price_rate_ids': [str(line.rate_id) for line in estimate.lines],
                    'usage_estimate': usage,
                    'automatic_fallback': automatic_fallback,
                }),
            )
            policy.reserved_amount += amount
            policy.save(update_fields=['reserved_amount', 'updated_at'])
            return reservation

    @classmethod
    def _deny_reservation_replay(
        cls,
        reservation,
        *,
        project,
        provider,
        capability,
        work_item,
    ):
        """校验幂等键身份并拒绝把旧预算记录再次用于外部提交。"""

        details = reservation.details or {}
        expected_work_item_id = getattr(work_item, 'pk', None)
        identity_conflict = (
            reservation.project_id != project.pk
            or (
                (reservation.work_item_id is not None or expected_work_item_id is not None)
                and reservation.work_item_id != expected_work_item_id
            )
            or (
                details.get('provider_id')
                and str(details.get('provider_id')) != str(provider.pk)
            )
            or (
                details.get('capability')
                and str(details.get('capability')) != str(capability)
            )
        )
        if identity_conflict:
            raise PaidCallDenied(
                '相同预算幂等键对应了不同的项目、Provider、能力或金额',
                'PAID_IDEMPOTENCY_CONFLICT',
            )
        code, message = cls.REPLAY_DENIALS.get(
            reservation.status,
            ('PAID_RESERVATION_CLOSED', '相同幂等键已有不可复用的预算预留'),
        )
        raise PaidCallDenied(message, code)

    @staticmethod
    def _reservation_total(queryset):
        active = queryset.filter(status__in=['active', 'ambiguous', 'manual_review']).aggregate(
            total=Sum('reserved_amount')
        )['total'] or Decimal('0')
        settled = queryset.filter(status='settled').aggregate(total=Sum('settled_amount'))['total'] or Decimal('0')
        return active + settled

    @classmethod
    def settle(cls, reservation, actual_amount=None, details=None):
        """真实 usage 缺失时按最坏预留额结算，绝不低估付费成本。"""
        if reservation is None:
            return None
        with transaction.atomic():
            # 全部预算事务统一先锁 policy、再锁 reservation，避免“结算”和
            # “相同幂等键重放检查”互相反向等待造成 PostgreSQL 死锁。
            policy = _locked(AIBudgetPolicy.objects.filter(pk=reservation.policy_id)).get()
            locked = _locked(BudgetReservation.objects.filter(pk=reservation.pk)).get()
            if locked.status == 'settled':
                return locked
            if locked.status != 'active':
                raise PaidCallDenied(f'预算预留状态 {locked.status} 不能结算')
            amount = Decimal(actual_amount) if actual_amount is not None else locked.reserved_amount
            policy.reserved_amount = max(Decimal('0'), policy.reserved_amount - locked.reserved_amount)
            policy.spent_amount += amount
            policy.save(update_fields=['reserved_amount', 'spent_amount', 'updated_at'])
            locked.status = 'settled'
            locked.settled_amount = amount
            locked.settled_at = timezone.now()
            if details:
                locked.details = {**(locked.details or {}), **mask_sensitive_data(details)}
            locked.save(update_fields=['status', 'settled_amount', 'settled_at', 'details', 'updated_at'])
            return locked

    @classmethod
    def mark_ambiguous(cls, reservation, reason):
        """上游是否计费不明确时永久保留预留，等待人工核对后再释放。"""
        if reservation is None:
            return None
        with transaction.atomic():
            locked = _locked(BudgetReservation.objects.filter(pk=reservation.pk)).get()
            if locked.status != 'active':
                return locked
            locked.status = 'manual_review'
            locked.expires_at = None
            locked.details = {
                **(locked.details or {}),
                'manual_review_reason': mask_sensitive_data(str(reason)),
            }
            locked.save(update_fields=['status', 'expires_at', 'details', 'updated_at'])
            return locked

    @classmethod
    def release(cls, reservation, reason='safe_pre_submit_failure'):
        if reservation is None:
            return None
        with transaction.atomic():
            policy = _locked(AIBudgetPolicy.objects.filter(pk=reservation.policy_id)).get()
            locked = _locked(BudgetReservation.objects.filter(pk=reservation.pk)).get()
            if locked.status in {'released', 'expired'}:
                return locked
            if locked.status != 'active':
                raise PaidCallDenied(f'预算预留状态 {locked.status} 不能释放')
            policy.reserved_amount = max(Decimal('0'), policy.reserved_amount - locked.reserved_amount)
            policy.save(update_fields=['reserved_amount', 'updated_at'])
            locked.status = 'released'
            locked.released_at = timezone.now()
            locked.details = {
                **(locked.details or {}),
                'release_reason': mask_sensitive_data(reason),
            }
            locked.save(update_fields=['status', 'released_at', 'details', 'updated_at'])
            return locked
