"""确定性生成路由匹配与候选目标排序。"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from django.db.models import Prefetch, Q
from django.utils import timezone

from ..models import GenerationRoute, GenerationTarget


class NoRouteAvailable(LookupError):
    """没有满足条件的推理路由。"""


@dataclass(frozen=True)
class RouteSelection:
    """命中的路由与已排序候选目标。"""

    route: GenerationRoute
    targets: Tuple[GenerationTarget, ...]


def _context_value(context: Dict[str, Any], dotted_key: str):
    value: Any = context
    for part in dotted_key.split('.'):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _matches_rules(rules: Dict[str, Any], context: Dict[str, Any]) -> bool:
    for key, expected in (rules or {}).items():
        actual = _context_value(context, key)
        if isinstance(expected, dict):
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


class RoutingService:
    """按路由优先级、节点可用性和本地偏好选择目标。"""

    LOCAL_NODE_TYPES = {'local_gpu', 'cpu', 'edge'}

    @classmethod
    def select(
        cls,
        capability: str,
        context: Optional[Dict[str, Any]] = None,
        project_settings=None,
        exclude_target_ids: Optional[Iterable[Any]] = None,
    ) -> RouteSelection:
        context = context or {}
        if project_settings is not None and not getattr(project_settings, 'is_active', True):
            project_settings = None
        excluded = {str(item) for item in (exclude_target_ids or [])}
        target_queryset = GenerationTarget.objects.filter(is_active=True).select_related(
            'provider', 'provider__runtime_node', 'runtime_node', 'profile'
        )
        project_id = getattr(project_settings, 'project_id', None)
        route_scope = Q(scope='global')
        if project_id:
            route_scope |= Q(scope='project', project_id=project_id)
        routes = GenerationRoute.objects.filter(
            route_scope, capability=capability, is_active=True
        ).select_related('profile').prefetch_related(
            Prefetch('targets', queryset=target_queryset)
        ).order_by('-priority', 'name')

        default_route_id = getattr(project_settings, 'default_route_id', None)
        capability_routes = getattr(project_settings, 'capability_routes', {}) or {}
        preferred_route = capability_routes.get(capability)
        if default_route_id:
            routes = routes.order_by(
                # Django 3.2 不依赖数据库特定 NULL 排序；默认路由在内存中再前置。
                '-priority', 'name'
            )
        route_list = list(routes)
        if preferred_route or default_route_id:
            preferred_text = str(preferred_route or default_route_id)
            route_list.sort(
                key=lambda route: (
                    str(route.id) != preferred_text and route.name != preferred_text,
                    -route.priority,
                    route.name,
                )
            )

        for route in route_list:
            requested_stage = context.get('stage_type', context.get('stage', ''))
            if route.stage_type and route.stage_type != requested_stage:
                continue
            requested_profile = context.get('profile_code') or getattr(
                project_settings, 'default_profile_code', ''
            )
            if requested_profile and route.profile_code != requested_profile:
                continue
            if not _matches_rules(route.match_rules, context):
                continue
            configured_targets = list(route.targets.all())
            # 自动付费只能是“至少配置过一个本地目标”之后的技术回退。即使管理端
            # 误建了只有 paid_fallback 的路由，运行时也必须 fail closed，不能把
            # API 目标当作事实上的主目标。已配置本地节点暂时离线仍视为技术失败，
            # 后续三重门通过时可以进入付费回退。
            has_configured_local = any(
                target.role != 'paid_fallback' and cls._is_local(target)
                for target in configured_targets
            )
            targets = [
                target for target in configured_targets
                if str(target.id) not in excluded and cls._is_available(target)
            ]
            prefer_local = bool(
                getattr(project_settings, 'prefer_local', route.local_first)
                if project_settings is not None else route.local_first
            )
            cloud_authorized = cls._cloud_authorized(project_settings)
            allow_paid = bool(
                project_settings is not None
                and getattr(project_settings, 'allow_paid_fallback', False)
                and cloud_authorized
                and getattr(project_settings, 'project_budget_cny', 0) > 0
            )
            targets = [
                target for target in targets
                if (cloud_authorized or not cls._requires_cloud_transfer(target))
                and (allow_paid or target.role != 'paid_fallback')
                and (has_configured_local or target.role != 'paid_fallback')
            ]
            targets.sort(
                key=lambda target: (
                    cls._role_rank(target),
                    0 if (prefer_local and cls._is_local(target)) else 1,
                    target.position,
                    -target.priority,
                    -float(target.weight),
                    target.name,
                )
            )
            if targets:
                return RouteSelection(route, tuple(targets))
        raise NoRouteAvailable(f'未找到能力 {capability} 的可用生成路由。')

    @classmethod
    def _is_local(cls, target: GenerationTarget) -> bool:
        if target.runtime_node:
            return bool(
                target.runtime_node.is_local
                or target.runtime_node.node_type in cls.LOCAL_NODE_TYPES
            )
        if target.provider:
            if getattr(target.provider, 'deployment_mode', '') in {'local', 'mock'}:
                return True
            provider_node = getattr(target.provider, 'runtime_node', None)
            return bool(provider_node and provider_node.is_local)
        return False

    @classmethod
    def _requires_cloud_transfer(cls, target: GenerationTarget) -> bool:
        if target.role == 'paid_fallback':
            return True
        if target.runtime_node:
            return not target.runtime_node.is_local
        if target.provider:
            return getattr(target.provider, 'deployment_mode', 'api') == 'api'
        return True

    @staticmethod
    def _cloud_authorized(project_settings) -> bool:
        return bool(
            project_settings is not None
            and getattr(project_settings, 'allow_cloud_data_transfer', False)
            and getattr(project_settings, 'cloud_authorized_by_id', None)
            and getattr(project_settings, 'cloud_authorized_at', None)
        )

    @staticmethod
    def _role_rank(target: GenerationTarget) -> int:
        return {
            'local_primary': 0,
            'local_secondary': 1,
            'paid_fallback': 2,
        }.get(target.role, 3)

    @staticmethod
    def _is_available(target: GenerationTarget) -> bool:
        if target.cooldown_until and target.cooldown_until > timezone.now():
            return False
        if target.provider and not target.provider.is_active:
            return False
        if target.provider and getattr(target.provider, 'health_status', '') == 'unavailable':
            return False
        if target.runtime_node:
            if not target.runtime_node.is_active:
                return False
            if target.runtime_node.health_status == 'unavailable':
                return False
        return True

    resolve = select


def select_generation_targets(*args, **kwargs):
    """函数式路由入口。"""

    return RoutingService.select(*args, **kwargs)
