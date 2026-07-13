"""本地/付费混合推理的管理与审计 API。"""

from decimal import Decimal

import requests
from django.db.models import Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAdminUser, IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response

from apps.models.models import ModelUsageLog
from apps.projects.models import Project

from .models import (
    AIBudgetPolicy,
    BudgetReservation,
    GenerationProfile,
    GenerationRoute,
    GenerationTarget,
    GenerationWorkItem,
    MediaArtifact,
    ProviderPriceRate,
    RuntimeNode,
)
from .serializers import (
    AIBudgetPolicySerializer,
    BudgetReservationSerializer,
    GenerationProfileSerializer,
    GenerationRouteSerializer,
    GenerationTargetSerializer,
    GenerationWorkItemSerializer,
    MediaArtifactSerializer,
    ProviderPriceRateSerializer,
    RuntimeNodeSerializer,
)
from .services.budget import BudgetService, InvalidReservationState
from .services.security import mask_sensitive_data
from .services.work_items import InvalidWorkItemTransition, WorkItemService


def _agent_headers(node, request_id=''):
    headers = {'X-Request-ID': request_id} if request_id else {}
    if node.access_token:
        headers['Authorization'] = f'Bearer {node.access_token}'
    return headers


def _agent_error(response):
    try:
        data = response.json()
    except ValueError:
        data = {}
    error = data.get('error') if isinstance(data, dict) else None
    if isinstance(error, dict):
        return {
            'code': str(error.get('code') or 'RUNTIME_NOT_READY'),
            'message': str(error.get('message') or 'Runtime Agent 请求失败'),
        }
    return {'code': 'RUNTIME_NOT_READY', 'message': 'Runtime Agent 请求失败'}


class RuntimeNodeViewSet(viewsets.ModelViewSet):
    """运行节点 CRUD；Token 只写，健康快照由显式探测更新。"""

    permission_classes = [IsAuthenticated]
    serializer_class = RuntimeNodeSerializer
    queryset = RuntimeNode.objects.all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['node_type', 'health_status', 'is_local', 'is_active']
    search_fields = ['name', 'agent_url', 'agent_version']
    ordering_fields = ['name', 'priority', 'last_seen_at', 'updated_at']
    ordering = ['-priority', 'name']

    def get_permissions(self):
        """节点清单可读；写配置、健康探测和 reload 必须是管理员。"""

        permission_class = (
            IsAuthenticated if self.request.method in SAFE_METHODS else IsAdminUser
        )
        return [permission_class()]

    @action(detail=True, methods=['post'], url_path='refresh-health')
    def refresh_health(self, request, pk=None):
        node = self.get_object()
        base_url = node.agent_url.rstrip('/')
        if not base_url:
            return Response(
                {'error': {'code': 'RUNTIME_NOT_CONFIGURED', 'message': '节点没有 Agent URL。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        headers = _agent_headers(node, request.headers.get('X-Request-ID', ''))
        try:
            ready = requests.get(
                f'{base_url}/v1/health/ready', headers=headers, timeout=(2, 8)
            )
            if not ready.ok:
                error = _agent_error(ready)
                node.health_status = 'unavailable'
                node.save(update_fields=['health_status', 'updated_at'])
                return Response({'error': error}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            capabilities_response = requests.get(
                f'{base_url}/v1/capabilities', headers=headers, timeout=(2, 8)
            )
            capabilities_response.raise_for_status()
            capabilities = capabilities_response.json()
        except requests.RequestException as error:
            node.health_status = 'unavailable'
            node.save(update_fields=['health_status', 'updated_at'])
            return Response(
                {
                    'error': {
                        'code': 'NODE_UNAVAILABLE',
                        'message': mask_sensitive_data(str(error)),
                    }
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        adapter_items = capabilities.get('adapters', [])
        node.capabilities = adapter_items
        node.resource_groups = {
            item.get('name'): item
            for item in capabilities.get('resource_groups', [])
            if isinstance(item, dict) and item.get('name')
        }
        node.hardware_snapshot = capabilities.get('hardware') or {}
        node.agent_version = str(capabilities.get('service_version') or node.agent_version)
        node.health_status = 'healthy'
        node.last_seen_at = timezone.now()
        node.metadata = {
            **(node.metadata or {}),
            'runtime_generation': capabilities.get('runtime_generation'),
            'ready_checks': ready.json().get('checks', {}),
        }
        node.save(
            update_fields=[
                'capabilities', 'resource_groups', 'hardware_snapshot',
                'agent_version', 'health_status',
                'last_seen_at', 'metadata', 'updated_at',
            ]
        )
        return Response(self.get_serializer(node).data)

    @action(detail=True, methods=['post'], url_path='runtime-reload')
    def runtime_reload(self, request, pk=None):
        """只重新加载已固定的配置；该接口不会下载或升级模型。"""

        node = self.get_object()
        try:
            response = requests.post(
                f'{node.agent_url.rstrip("/")}/v1/runtime-reloads',
                headers=_agent_headers(node, request.headers.get('X-Request-ID', '')),
                json={'reason': request.data.get('reason') or 'manual_admin_reload'},
                timeout=(2, 15),
            )
            if not response.ok:
                return Response({'error': _agent_error(response)}, status=response.status_code)
            return Response(response.json(), status=status.HTTP_202_ACCEPTED)
        except requests.RequestException as error:
            return Response(
                {'error': {'code': 'NODE_UNAVAILABLE', 'message': mask_sensitive_data(str(error))}},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class GenerationProfileViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = GenerationProfileSerializer
    queryset = GenerationProfile.objects.filter(is_active=True)
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['key', 'capability', 'is_active']
    ordering = ['capability', 'name']


class GenerationRouteViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = GenerationRouteSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['capability', 'scope', 'project', 'stage_type', 'profile_code', 'is_active']
    search_fields = ['name', 'stage_type']
    ordering_fields = ['priority', 'name', 'updated_at']
    ordering = ['-priority', 'name']

    def get_queryset(self):
        return GenerationRoute.objects.filter(
            Q(scope='global') | Q(project__user=self.request.user)
        ).select_related('project', 'profile').prefetch_related(
            'targets__provider', 'targets__runtime_node', 'targets__profile'
        )

    def create(self, request, *args, **kwargs):
        if request.data.get('scope', 'global') == 'global' and not request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('只有管理员可以创建全局路由。')
        return super().create(request, *args, **kwargs)

    @staticmethod
    def _assert_route_write_allowed(user, route):
        if route.scope == 'global' and not user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('只有管理员可以修改全局路由。')
        if route.scope == 'project' and route.project.user_id != user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('不能修改其他用户的项目路由。')

    def perform_update(self, serializer):
        self._assert_route_write_allowed(self.request.user, serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_route_write_allowed(self.request.user, instance)
        instance.delete()


class GenerationTargetViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = GenerationTargetSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['route', 'role', 'provider', 'runtime_node', 'is_active']
    ordering = ['route', 'position', '-priority']

    def get_queryset(self):
        return GenerationTarget.objects.filter(
            Q(route__scope='global') | Q(route__project__user=self.request.user)
        ).select_related('route', 'provider', 'runtime_node', 'profile')

    def perform_create(self, serializer):
        route_id = self.request.data.get('route')
        route = GenerationRoute.objects.filter(
            Q(scope='global') | Q(project__user=self.request.user), pk=route_id
        ).first()
        if not route:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('无权向该路由添加目标。')
        if route.scope == 'global' and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('只有管理员可以修改全局路由目标。')
        serializer.save(route=route)

    def perform_update(self, serializer):
        route = serializer.instance.route
        if route.scope == 'global' and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('只有管理员可以修改全局路由目标。')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.route.scope == 'global' and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('只有管理员可以删除全局路由目标。')
        instance.delete()


class ProviderPriceRateViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProviderPriceRateSerializer
    queryset = ProviderPriceRate.objects.select_related('provider', 'runtime_node')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        'provider', 'runtime_node', 'capability', 'billing_unit', 'currency',
        'version', 'is_active',
    ]
    search_fields = ['model_pattern', 'version', 'source_note']
    ordering_fields = ['effective_from', 'priority', 'updated_at']
    ordering = ['-priority', '-effective_from']

    def get_permissions(self):
        permission_class = IsAuthenticated if self.request.method in SAFE_METHODS else IsAdminUser
        return [permission_class()]


class AIBudgetPolicyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AIBudgetPolicySerializer
    queryset = AIBudgetPolicy.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['period_type', 'is_active']
    ordering = ['-is_active', 'name']

    def get_permissions(self):
        permission_class = IsAuthenticated if self.request.method in SAFE_METHODS else IsAdminUser
        return [permission_class()]


class BudgetReservationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BudgetReservationSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['policy', 'project', 'work_item', 'status']
    ordering = ['-created_at']

    def get_queryset(self):
        return BudgetReservation.objects.filter(project__user=self.request.user).select_related(
            'policy', 'project', 'work_item'
        )

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """人工核对不明确账单后显式结算或释放，绝不由定时任务猜测。"""

        reservation = self.get_object()
        resolution = request.data.get('resolution')
        try:
            if resolution == 'charged':
                reservation = BudgetService.resolve_manual_review(
                    reservation,
                    charged=True,
                    actual_amount=request.data.get('actual_amount'),
                    note=request.data.get('note', ''),
                )
            elif resolution == 'not_charged':
                reservation = BudgetService.resolve_manual_review(
                    reservation,
                    charged=False,
                    note=request.data.get('note', ''),
                )
            else:
                return Response(
                    {'error': 'resolution 必须为 charged 或 not_charged'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except (InvalidReservationState, ValueError) as error:
            return Response({'error': str(error)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(reservation).data)


class GenerationWorkItemViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = GenerationWorkItemSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = [
        'project', 'capability', 'stage_type', 'status', 'provider', 'runtime_node',
        'storyboard_id',
    ]
    ordering_fields = ['priority', 'scheduled_at', 'created_at', 'completed_at']
    ordering = ['-priority', 'scheduled_at', 'created_at']

    def get_queryset(self):
        return GenerationWorkItem.objects.filter(project__user=self.request.user).select_related(
            'project', 'profile', 'route', 'target', 'provider', 'runtime_node'
        ).prefetch_related('artifacts')

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        item = self.get_object()
        try:
            from .services.scheduler import WorkItemScheduler
            cancellation = WorkItemScheduler().cancel(str(item.id))
            item.refresh_from_db()
        except InvalidWorkItemTransition as error:
            return Response({'error': str(error)}, status=status.HTTP_409_CONFLICT)
        # 同步固化 DB/Agent 取消；异步任务只负责撤销尚在 broker 中等待的消息。
        try:
            from .tasks import cancel_work_item
            cancel_work_item.delay(str(item.id))
        except Exception:
            pass
        return Response(
            {**self.get_serializer(item).data, 'cancellation': cancellation},
            status=status.HTTP_202_ACCEPTED,
        )


class MediaArtifactViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MediaArtifactSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['project', 'work_item', 'kind', 'status', 'lifecycle', 'protected']
    ordering = ['-created_at']

    def get_queryset(self):
        return MediaArtifact.objects.filter(project__user=self.request.user)


class BudgetSummaryViewSet(viewsets.ViewSet):
    """返回预算控制面的单个摘要对象，不伪装成分页列表。"""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        project_id = request.query_params.get('project_id')
        projects = Project.objects.filter(user=request.user)
        if project_id:
            projects = projects.filter(pk=project_id)
            if not projects.exists():
                return Response({'error': '项目不存在'}, status=status.HTTP_404_NOT_FOUND)
        project_ids = list(projects.values_list('id', flat=True))
        reservations = BudgetReservation.objects.filter(project_id__in=project_ids)
        usage_logs = ModelUsageLog.objects.filter(project_id__in=project_ids)
        today = timezone.localdate()
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def amount_sum(values):
            return sum((Decimal(value or 0) for value in values), Decimal('0'))

        active_statuses = ['active', 'ambiguous', 'manual_review']
        return Response({
            'currency': 'CNY',
            'project_count': len(project_ids),
            'settled_total': amount_sum(usage_logs.values_list('settled_cost', flat=True)),
            'estimated_total': amount_sum(usage_logs.values_list('estimated_cost', flat=True)),
            'reserved_total': amount_sum(
                reservations.filter(status__in=active_statuses).values_list('reserved_amount', flat=True)
            ),
            'today_settled': amount_sum(
                usage_logs.filter(created_at__date=today).values_list('settled_cost', flat=True)
            ),
            'month_settled': amount_sum(
                usage_logs.filter(created_at__gte=month_start).values_list('settled_cost', flat=True)
            ),
            'manual_review_count': reservations.filter(
                status__in=['ambiguous', 'manual_review']
            ).count(),
            'policies': AIBudgetPolicySerializer(
                AIBudgetPolicy.objects.order_by('-is_active', 'name'), many=True
            ).data,
        })
