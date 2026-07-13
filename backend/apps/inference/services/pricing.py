"""提供商单价匹配和生成成本估算。"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fnmatch import fnmatchcase
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from django.db.models import Q
from django.utils import timezone

from ..models import ProviderPriceRate


MONEY_QUANTUM = Decimal('0.000001')


class PricingConfigurationError(ValueError):
    """价格表配置不完整或相互冲突。"""


@dataclass(frozen=True)
class CostLine:
    """一个计费单位的成本明细。"""

    billing_unit: str
    quantity: Decimal
    unit_size: Decimal
    unit_price: Decimal
    amount: Decimal
    currency: str
    exchange_rate_to_cny: Decimal
    amount_cny: Decimal
    rate_id: Any


@dataclass(frozen=True)
class CostEstimate:
    """成本估算结果；金额均使用 Decimal，避免浮点累计误差。"""

    amount: Decimal
    currency: str
    amount_cny: Decimal
    lines: Tuple[CostLine, ...] = ()
    missing_usage: Tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """至少命中一条价格且所有计费单位均有用量。"""

        return bool(self.lines) and not self.missing_usage


def _decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if value is None or value == '':
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _usage_value(usage: Mapping[str, Any], *keys: str) -> Optional[Decimal]:
    for key in keys:
        if key in usage:
            return _decimal(usage.get(key))
    return None


def _value_or_default(value: Optional[Decimal], default: Decimal) -> Decimal:
    return default if value is None else value


def _context_value(context: Mapping[str, Any], dotted_key: str):
    value: Any = context
    for part in dotted_key.split('.'):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _matches_conditions(
    conditions: Mapping[str, Any],
    context: Mapping[str, Any],
    usage: Mapping[str, Any],
) -> bool:
    merged = {**usage, **context}
    for key, expected in (conditions or {}).items():
        actual = _context_value(merged, key)
        if isinstance(expected, Mapping):
            if 'in' in expected and actual not in expected['in']:
                return False
            if 'not_in' in expected and actual in expected['not_in']:
                return False
            if 'min' in expected and (actual is None or actual < expected['min']):
                return False
            if 'max' in expected and (actual is None or actual > expected['max']):
                return False
        elif isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _quantity_for_unit(unit: str, usage: Mapping[str, Any]) -> Optional[Decimal]:
    input_tokens = _usage_value(usage, 'input_tokens', 'prompt_tokens')
    output_tokens = _usage_value(usage, 'output_tokens', 'completion_tokens')
    if unit == 'input_token':
        return input_tokens
    if unit == 'output_token':
        return output_tokens
    if unit == 'token':
        total = _usage_value(usage, 'total_tokens', 'tokens')
        if total is not None:
            return total
        if input_tokens is not None or output_tokens is not None:
            return (input_tokens or Decimal('0')) + (output_tokens or Decimal('0'))
        return None
    if unit == 'image':
        return _usage_value(usage, 'image_count', 'images', 'sample_count')
    if unit == 'image_megapixel':
        direct = _usage_value(usage, 'image_megapixels', 'megapixels')
        if direct is not None:
            return direct
        width = _usage_value(usage, 'width')
        height = _usage_value(usage, 'height')
        count = _value_or_default(
            _usage_value(usage, 'image_count', 'images', 'sample_count'), Decimal('1')
        )
        if width is not None and height is not None:
            return width * height * count / Decimal('1000000')
        return None
    if unit == 'video_second':
        direct = _usage_value(usage, 'video_seconds', 'total_duration_seconds')
        if direct is not None:
            return direct
        duration = _usage_value(usage, 'duration_seconds')
        count = _value_or_default(
            _usage_value(usage, 'video_count', 'videos', 'sample_count'), Decimal('1')
        )
        return duration * count if duration is not None else None
    if unit == 'video_task':
        return _value_or_default(
            _usage_value(usage, 'video_tasks', 'video_task_count', 'video_count'), Decimal('1')
        )
    if unit == 'request':
        return _value_or_default(
            _usage_value(usage, 'request_count', 'requests'), Decimal('1')
        )
    if unit == 'gpu_second':
        return _usage_value(usage, 'gpu_seconds')
    return None


class PricingService:
    """根据所有者、能力、模型模式和生效时间选择最具体价格。"""

    @classmethod
    def match_rates(
        cls,
        capability: str,
        model_name: str,
        provider=None,
        runtime_node=None,
        at=None,
        rates: Optional[Iterable[ProviderPriceRate]] = None,
        context: Optional[Mapping[str, Any]] = None,
        usage: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, ProviderPriceRate]:
        at = at or timezone.now()
        context = context or {}
        usage = usage or {}
        if rates is None:
            owner_query = Q(pk__in=[])
            if provider is not None:
                owner_query |= Q(provider=provider, runtime_node__isnull=True)
            if runtime_node is not None:
                owner_query |= Q(runtime_node=runtime_node, provider__isnull=True)
            if provider is not None and runtime_node is not None:
                owner_query |= Q(provider=provider, runtime_node=runtime_node)
            rates = ProviderPriceRate.objects.filter(
                owner_query,
                capability=capability,
                is_active=True,
            ).filter(
                Q(effective_from__isnull=True) | Q(effective_from__lte=at),
                Q(effective_to__isnull=True) | Q(effective_to__gt=at),
            )

        candidates = []
        for rate in rates:
            if rate.capability != capability or not rate.is_active:
                continue
            if rate.effective_from and rate.effective_from > at:
                continue
            if rate.effective_to and rate.effective_to <= at:
                continue
            if rate.provider_id and (provider is None or rate.provider_id != provider.pk):
                continue
            if rate.runtime_node_id and (
                runtime_node is None or rate.runtime_node_id != runtime_node.pk
            ):
                continue
            pattern = rate.model_pattern or '*'
            if not fnmatchcase((model_name or '').lower(), pattern.lower()):
                continue
            if not _matches_conditions(rate.conditions, context, usage):
                continue
            literal_length = len(pattern.replace('*', '').replace('?', ''))
            owner_specificity = int(bool(rate.provider_id)) + int(bool(rate.runtime_node_id))
            candidates.append((rate, owner_specificity, literal_length))

        selected: Dict[str, ProviderPriceRate] = {}
        for rate, owner_specificity, literal_length in sorted(
            candidates,
            key=lambda item: (
                item[0].priority,
                item[1],
                item[2],
                item[0].effective_from or timezone.datetime.min.replace(tzinfo=timezone.utc),
                str(item[0].pk),
            ),
            reverse=True,
        ):
            selected.setdefault(rate.billing_unit, rate)
        return selected

    @classmethod
    def estimate(
        cls,
        capability: str,
        model_name: str,
        usage: Optional[Mapping[str, Any]] = None,
        provider=None,
        runtime_node=None,
        at=None,
        rates: Optional[Iterable[ProviderPriceRate]] = None,
        default_currency: str = 'CNY',
        context: Optional[Mapping[str, Any]] = None,
    ) -> CostEstimate:
        usage = usage or {}
        selected = cls.match_rates(
            capability=capability,
            model_name=model_name,
            provider=provider,
            runtime_node=runtime_node,
            at=at,
            rates=rates,
            context=context,
            usage=usage,
        )
        # 总 Token 与输入/输出 Token 是互斥口径，优先采用拆分价格。
        if 'token' in selected and ('input_token' in selected or 'output_token' in selected):
            selected.pop('token')

        lines = []
        missing = []
        totals_by_currency: Dict[str, Decimal] = {}
        total_cny = Decimal('0')
        for unit, rate in sorted(selected.items()):
            quantity = _quantity_for_unit(unit, usage)
            if quantity is None:
                missing.append(unit)
                continue
            if quantity < 0:
                raise ValueError(f'{unit} 用量不能小于 0。')
            if rate.unit_size <= 0 or rate.unit_price < 0 or rate.exchange_rate_to_cny <= 0:
                raise PricingConfigurationError(f'价格 {rate.pk} 的单价或汇率配置无效。')
            if quantity == 0:
                amount = Decimal('0')
            else:
                amount = quantity / rate.unit_size * rate.unit_price
                amount = max(amount, rate.minimum_charge)
            amount = amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            currency = rate.currency.upper()
            amount_cny = (amount * rate.exchange_rate_to_cny).quantize(
                MONEY_QUANTUM, rounding=ROUND_HALF_UP
            )
            totals_by_currency[currency] = totals_by_currency.get(currency, Decimal('0')) + amount
            total_cny += amount_cny
            lines.append(
                CostLine(
                    billing_unit=unit,
                    quantity=quantity,
                    unit_size=rate.unit_size,
                    unit_price=rate.unit_price,
                    amount=amount,
                    currency=currency,
                    exchange_rate_to_cny=rate.exchange_rate_to_cny,
                    amount_cny=amount_cny,
                    rate_id=rate.pk,
                )
            )
        if len(totals_by_currency) == 1:
            currency, amount = next(iter(totals_by_currency.items()))
        elif totals_by_currency:
            currency, amount = 'CNY', total_cny
        else:
            currency, amount = default_currency.upper(), Decimal('0')
        return CostEstimate(
            amount=amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
            currency=currency,
            amount_cny=total_cny.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
            lines=tuple(lines),
            missing_usage=tuple(missing),
        )


def estimate_generation_cost(*args, **kwargs):
    """函数式成本估算入口。"""

    return PricingService.estimate(*args, **kwargs)
