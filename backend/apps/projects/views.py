"""
项目管理视图集
职责: 处理HTTP请求和业务逻辑编排
遵循单一职责原则(SRP)
"""


import hashlib
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from celery.result import AsyncResult
from django.core.cache import cache
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import Q
from django.db import transaction
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from jinja2 import Template, TemplateError
import requests
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import AccessToken

from apps.models.models import ModelProvider
from apps.inference.models import (
    AIBudgetPolicy,
    BudgetReservation,
    GenerationWorkItem,
    ProjectAISettings,
)
from apps.inference.serializers import (
    GenerationWorkItemSerializer,
    ProjectAISettingsSerializer,
)
from apps.inference.services.pricing import PricingService
from apps.inference.services.hybrid import HybridInferenceService
from apps.inference.services.routing import NoRouteAvailable, RoutingService
from apps.inference.services.segments import plan_video_segments
from apps.inference.services.stage_planner import StagePlanningError, StageWorkItemPlanner
from apps.inference.services.work_items import (
    InvalidWorkItemTransition,
    WorkItemService,
)
from apps.prompts.models import PromptTemplate, PromptTemplateSet
from apps.prompts.models import GlobalVariable
from apps.prompts.serializers import GlobalVariableListSerializer
from core.ai_client.factory import create_ai_client
from core.ai_client.base import AIResponse
from core.utils.file_storage import image_storage
from .models import Project, ProjectAssetBinding, ProjectModelConfig, ProjectStage, Series
from .paid_safety import pipeline_direct_api_providers
from .queue_service import cancel_running_queue_task, enqueue_episode_task, force_release_queue_task
from .serializers import (
    ProjectBatchCreateSerializer,
    ProjectAssetBindingSerializer,
    ProjectAssetBindingUpdateSerializer,
    ProjectCreateSerializer,
    ProjectDetailSerializer,
    ProjectListSerializer,
    ProjectModelConfigSerializer,
    ProjectStageSerializer,
    ProjectTemplateSerializer,
    ProjectUpdateSerializer,
    SeriesCreateSerializer,
    SeriesDetailSerializer,
    SeriesListSerializer,
    SeriesUpdateSerializer,
    StageExecuteSerializer,
    StageRetrySerializer,
)
from .utils import get_project_stage_order, get_stage_template_states, is_stage_template_enabled

logger = logging.getLogger(__name__)


class SeriesViewSet(viewsets.ModelViewSet):
    """作品管理 ViewSet"""

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "updated_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Series.objects.filter(user=self.request.user).prefetch_related('episodes__stages', 'episodes__queue_tasks')

    def get_serializer_class(self):
        if self.action == 'list':
            return SeriesListSerializer
        if self.action == 'retrieve':
            return SeriesDetailSerializer
        if self.action == 'create':
            return SeriesCreateSerializer
        if self.action in ['update', 'partial_update']:
            return SeriesUpdateSerializer
        return SeriesDetailSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        instance.refresh_from_db()
        detail_serializer = SeriesDetailSerializer(instance, context=self.get_serializer_context())
        return Response(detail_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)


class ProjectViewSet(viewsets.ModelViewSet):
    """
    项目管理ViewSet

    提供项目的CRUD操作和工作流控制
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "prompt_template_set", "series"]
    search_fields = ["name", "description", "original_topic"]
    ordering_fields = ["created_at", "updated_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """只返回当前用户的项目"""
        queryset = (
            Project.objects.filter(user=self.request.user)
            .select_related("user", "prompt_template_set", "series")
            .prefetch_related("stages", "asset_bindings__asset", "queue_tasks")
        )

        series_id = self.request.query_params.get('series') or self.request.query_params.get('series_id')
        if series_id:
            queryset = queryset.filter(series_id=series_id)

        return queryset

    @action(detail=True, methods=['get', 'put', 'patch'], url_path='ai-settings')
    def ai_settings(self, request, pk=None):
        """读取或更新项目质量、出站授权和预算。

        云授权是项目级显式决定。第一次从禁止切换为允许时必须附带确认位；
        撤销授权会同步关闭自动付费回退，防止旧配置继续向外发送数据。
        """

        project = self.get_object()
        ai_settings, _ = ProjectAISettings.objects.get_or_create(project=project)
        if request.method.lower() == 'get':
            return Response(ProjectAISettingsSerializer(ai_settings).data)

        partial = request.method.lower() == 'patch'
        serializer = ProjectAISettingsSerializer(
            ai_settings,
            data=request.data,
            partial=partial,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        requested_cloud = serializer.validated_data.get(
            'allow_cloud_data_transfer', ai_settings.allow_cloud_data_transfer
        )
        if requested_cloud and not ai_settings.allow_cloud_data_transfer:
            if request.data.get('confirm_cloud_data_transfer') is not True:
                return Response(
                    {
                        'error': {
                            'code': 'CLOUD_AUTH_CONFIRMATION_REQUIRED',
                            'message': '首次授权云端出站必须明确确认。',
                        }
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            saved = serializer.save()
            if requested_cloud:
                if not saved.cloud_authorized_by_id:
                    saved.cloud_authorized_by = request.user
                    saved.cloud_authorized_at = timezone.now()
            else:
                saved.allow_paid_fallback = False
                saved.cloud_authorized_by = None
                saved.cloud_authorized_at = None
            saved.save(
                update_fields=[
                    'allow_paid_fallback', 'cloud_authorized_by',
                    'cloud_authorized_at', 'updated_at',
                ]
            )
        return Response(ProjectAISettingsSerializer(saved).data)

    @action(detail=True, methods=['post'], url_path='generation-estimates')
    def generation_estimates(self, request, pk=None):
        """执行前返回工作项规模、最坏付费成本与缺失门槛。"""

        project = self.get_object()
        capability = str(request.data.get('capability') or '').strip()
        if capability not in {'llm', 'text2image', 'image_edit', 'image2video', 'motion_render'}:
            return Response(
                {'error': 'capability 不受支持'}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            task_count = int(request.data.get('task_count', 1))
        except (TypeError, ValueError):
            return Response({'error': 'task_count 必须是整数'}, status=status.HTTP_400_BAD_REQUEST)
        if task_count < 1 or task_count > 10000:
            return Response(
                {'error': 'task_count 必须在 1 到 10000 之间'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ai_settings, _ = ProjectAISettings.objects.get_or_create(project=project)
        profile_code = str(
            request.data.get('profile') or ai_settings.default_profile_code or 'balanced'
        )
        stage_type = str(request.data.get('stage_type') or '')
        provider_id = str(request.data.get('provider_id') or '').strip()
        usage_per_item = request.data.get('usage_per_item') or {}
        if not isinstance(usage_per_item, dict):
            return Response(
                {'error': 'usage_per_item 必须是对象'}, status=status.HTTP_400_BAD_REQUEST
            )

        segment_plan = []
        effective_task_count = task_count
        if capability == 'image2video':
            try:
                duration = Decimal(str(request.data.get('duration_seconds', 10)))
                native_max = Decimal(str(request.data.get('native_max_seconds', 5)))
            except (InvalidOperation, TypeError, ValueError):
                return Response(
                    {'error': '视频时长参数必须是数字'}, status=status.HTTP_400_BAD_REQUEST
                )
            try:
                segment_plan = plan_video_segments(
                    target_duration_seconds=duration,
                    max_native_duration=native_max,
                    fps=int(request.data.get('fps', 24)),
                    overlap_frames=int(request.data.get('overlap_frames', 8)),
                )
            except (ValueError, TypeError) as error:
                return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
            effective_task_count *= len(segment_plan)
            usage_per_item = {
                'video_seconds': float(native_max),
                'video_tasks': 1,
                **usage_per_item,
            }

        missing = []
        paid_targets = []
        local_targets = []
        explicit_provider = None
        if provider_id:
            explicit_provider = ModelProvider.objects.filter(
                pk=provider_id,
                provider_type=capability,
                is_active=True,
            ).first()
            if explicit_provider is None:
                return Response(
                    {'error': '显式 Provider 不存在、未启用或能力不匹配'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if explicit_provider.deployment_mode == 'api':
                paid_targets.append(explicit_provider)
            else:
                local_targets.append(explicit_provider)
        else:
            try:
                selection = RoutingService.select(
                    capability,
                    context={
                        'project_id': str(project.id),
                        'stage_type': stage_type,
                        'profile_code': profile_code,
                    },
                    project_settings=ai_settings,
                )
                route = selection.route
            except NoRouteAvailable:
                route = None
                missing.append('generation_route')

            if route is not None:
                for target in route.targets.filter(is_active=True).select_related('provider'):
                    provider = target.provider
                    if provider and provider.deployment_mode == 'api':
                        paid_targets.append(target)
                    else:
                        local_targets.append(target)

        # 自动回退最多走两个付费目标；最坏成本按顺序累加，不能只报最低价。
        worst_per_item = Decimal('0')
        price_details = []
        for target in paid_targets[:2]:
            provider = target if isinstance(target, ModelProvider) else target.provider
            estimate = PricingService.estimate(
                capability,
                provider.model_name,
                usage_per_item,
                provider=provider,
                context={'profile': profile_code, 'stage_type': stage_type},
            )
            if not estimate.is_complete:
                missing.append(f'price:{provider.id}')
            else:
                worst_per_item += estimate.amount_cny
            price_details.append({
                'provider_id': str(provider.id),
                'provider_name': provider.name,
                'amount_cny': estimate.amount_cny,
                'complete': estimate.is_complete,
                'missing_usage': estimate.missing_usage,
                'rate_ids': [str(line.rate_id) for line in estimate.lines],
            })

        policy = ai_settings.budget_policy or AIBudgetPolicy.objects.filter(
            is_active=True
        ).order_by('created_at').first()
        if not ai_settings.allow_cloud_data_transfer:
            missing.append('cloud_authorization')
        if ai_settings.project_budget_cny <= 0:
            missing.append('project_budget')
        if not policy or policy.daily_limit_cny <= 0:
            missing.append('global_daily_budget')
        if not policy or policy.monthly_limit_cny <= 0:
            missing.append('global_monthly_budget')
        if paid_targets and not provider_id and not ai_settings.allow_paid_fallback:
            missing.append('paid_fallback_disabled')

        return Response({
            'project_id': str(project.id),
            'capability': capability,
            'stage_type': stage_type,
            'profile': profile_code,
            'estimate_scope': 'explicit_provider' if provider_id else 'route',
            'provider_id': str(explicit_provider.pk) if explicit_provider else None,
            'requested_task_count': task_count,
            'effective_work_item_count': effective_task_count,
            'local_target_count': len(local_targets),
            'paid_target_count': len(paid_targets),
            'worst_api_cost_cny': worst_per_item * effective_task_count,
            'currency': 'CNY',
            'price_details': price_details,
            'missing_configuration': list(dict.fromkeys(missing)),
            # 显式 Provider 属于一次性人工付费，不得在 UI/审计中伪装成自动回退。
            'automatic_fallback_allowed': (
                not provider_id and not missing and bool(paid_targets)
            ),
            'manual_paid_execution_allowed': (
                bool(provider_id and paid_targets) and not missing
            ),
            'segment_plan': segment_plan,
        })

    @action(detail=True, methods=['get'], url_path='generation-work-items')
    def generation_work_items(self, request, pk=None):
        project = self.get_object()
        queryset = GenerationWorkItem.objects.filter(project=project).select_related(
            'profile', 'route', 'target', 'provider', 'runtime_node'
        ).prefetch_related('artifacts')
        for field in ('status', 'capability', 'stage_type'):
            value = request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        queryset = queryset.order_by('-priority', 'scheduled_at', 'created_at')
        page = self.paginate_queryset(queryset)
        serializer = GenerationWorkItemSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='retry-failed-items')
    def retry_failed_items(self, request, pk=None):
        """只补跑失败项；失败终态保留原审计记录，重试创建新的幂等工作项。"""

        project = self.get_object()
        selected_ids = request.data.get('work_item_ids') or []
        if selected_ids and not isinstance(selected_ids, list):
            return Response(
                {'error': 'work_item_ids 必须是数组'}, status=status.HTTP_400_BAD_REQUEST
            )
        queryset = GenerationWorkItem.objects.filter(
            project=project, status__in=['failed', 'retry_wait']
        )
        if selected_ids:
            queryset = queryset.filter(id__in=selected_ids)
        retried_ids = []
        with transaction.atomic():
            # 工作项与预算预留分别加锁，避免人工复核状态在“检查通过”和创建
            # 新幂等键之间发生变化。这里不 select_related nullable 关系。
            items = list(queryset.select_for_update()[:1000])
            reservation_statuses = {}
            reservations = BudgetReservation.objects.select_for_update().filter(
                work_item_id__in=[item.pk for item in items]
            ).values_list('work_item_id', 'status')
            for work_item_id, reservation_status in reservations:
                reservation_statuses.setdefault(work_item_id, []).append(reservation_status)
            blocked_items = [
                item for item in items
                if WorkItemService.requires_paid_manual_review(
                    item,
                    reservation_statuses.get(item.pk, []),
                )
            ]
            if blocked_items:
                return Response(
                    {
                        'error': {
                            'code': 'PAID_MANUAL_REVIEW_REQUIRED',
                            'message': '存在付费结果未完成核对的工作项，请先在预算预留中人工结算或释放。',
                            'work_item_ids': [str(item.pk) for item in blocked_items],
                        }
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            for item in items:
                if item.status == 'retry_wait':
                    try:
                        new_item = WorkItemService.retry(item, force=True)
                    except InvalidWorkItemTransition:
                        continue
                else:
                    retry_key = f'{item.idempotency_key}:manual:{uuid.uuid4().hex}'
                    new_item = WorkItemService.create(
                        project=project,
                        capability=item.capability,
                        idempotency_key=retry_key,
                        stage_type=item.stage_type,
                        storyboard_id=item.storyboard_id,
                        tile_index=item.tile_index,
                        segment_index=item.segment_index,
                        profile=item.profile,
                        route=item.route,
                        target=item.target,
                        provider=item.provider,
                        runtime_node=item.runtime_node,
                        priority=item.priority,
                        request_parameters=item.request_parameters,
                        effective_parameters=item.effective_parameters,
                        route_snapshot={
                            **(item.route_snapshot or {}),
                            'manual_retry_of': str(item.id),
                        },
                        max_attempts=item.max_attempts,
                    )
                retried_ids.append(str(new_item.id))

            if retried_ids:
                def dispatch_items():
                    try:
                        from apps.inference.tasks import enqueue_work_item
                        for item_id in retried_ids:
                            enqueue_work_item.apply_async(
                                args=[item_id], queue='orchestration'
                            )
                    except Exception:
                        # 数据库中的 waiting 状态可由 reconciler 恢复，不能因 Redis
                        # 暂时不可用而回滚用户已经确认的重试意图。
                        return
                transaction.on_commit(dispatch_items)

        return Response(
            {'retried_count': len(retried_ids), 'work_item_ids': retried_ids},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'], url_path='regenerate-work-item-with-api')
    def regenerate_work_item_with_api(self, request, pk=None):
        """主观质量不满意时，显式创建一次受三重门保护的 API 重生成。

        该入口不把“审美不满意”伪装成技术失败，也不修改原工作项。用户必须
        先看到精确 Provider 的最坏估算并确认费用；真正执行前 PaidCallGate 会
        再次锁定价目表与预算，价格变化超过确认上限时任务会被安全拒绝。
        """

        project = self.get_object()
        source = GenerationWorkItem.objects.filter(
            pk=request.data.get('work_item_id'),
            project=project,
            status__in=['succeeded', 'failed'],
        ).select_related('profile').first()
        if source is None:
            return Response(
                {'error': {'code': 'INPUT_MISSING', 'message': '源工作项不存在或尚未终态。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if WorkItemService.requires_paid_manual_review(source):
            return Response(
                {
                    'error': {
                        'code': 'PAID_MANUAL_REVIEW_REQUIRED',
                        'message': '源工作项仍有付费结果待人工核对，禁止再次提交。',
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        provider = ModelProvider.objects.filter(
            pk=request.data.get('provider_id'),
            provider_type=source.capability,
            deployment_mode='api',
            is_active=True,
        ).first()
        if provider is None:
            return Response(
                {'error': {'code': 'INVALID_REQUEST', 'message': '请选择能力匹配的 API Provider。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.data.get('confirm_paid_generation') is not True:
            return Response(
                {
                    'error': {
                        'code': 'PAID_CONFIRMATION_REQUIRED',
                        'message': '请先查看最大预计费用并显式确认本次 API 重生成。',
                        'provider_id': str(provider.pk),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        ai_settings = ProjectAISettings.objects.filter(project=project, is_active=True).first()
        if not ai_settings or not (
            ai_settings.allow_cloud_data_transfer
            and ai_settings.cloud_authorized_by_id
            and ai_settings.cloud_authorized_at
        ):
            return Response(
                {'error': {'code': 'CLOUD_NOT_AUTHORIZED', 'message': '项目未授权数据出站。'}},
                status=status.HTTP_403_FORBIDDEN,
            )
        usage = (source.request_parameters or {}).get('usage_estimate') or source.usage or {}
        estimate = PricingService.estimate(
            source.capability,
            provider.model_name,
            usage,
            provider=provider,
            context={
                'profile': getattr(source.profile, 'key', ai_settings.default_profile_code),
                'stage_type': source.stage_type,
            },
        )
        if not estimate.is_complete:
            return Response(
                {'error': {'code': 'PRICE_MISSING', 'message': '缺少匹配本次用量的有效价目表。'}},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            confirmed_max = Decimal(str(request.data.get('confirmed_max_cost_cny')))
        except (InvalidOperation, TypeError, ValueError):
            return Response(
                {'error': {'code': 'BUDGET_DENIED', 'message': '必须提交本次确认的最大费用。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if confirmed_max < estimate.amount_cny:
            return Response(
                {
                    'error': {
                        'code': 'BUDGET_DENIED',
                        'message': '确认金额低于当前价目表的最坏估算。',
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        client_key = str(request.headers.get('Idempotency-Key') or '').strip()
        if not client_key or len(client_key) > 200:
            return Response(
                {'error': {'code': 'INVALID_IDEMPOTENCY_KEY', 'message': '必须提供 1–200 字符的 Idempotency-Key。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        key_material = f'{source.pk}:{provider.pk}:{client_key}'
        work_key = f'api-regenerate:{hashlib.sha256(key_material.encode()).hexdigest()}'
        request_parameters = {
            **(source.request_parameters or {}),
            'manual_api': True,
            'confirmed_max_cost_cny': str(confirmed_max),
            'usage_estimate': usage,
        }
        with transaction.atomic():
            existed = GenerationWorkItem.objects.filter(
                project=project, idempotency_key=work_key
            ).exists()
            item = WorkItemService.create(
                project=project,
                capability=source.capability,
                idempotency_key=work_key,
                stage_execution_id=uuid.uuid4(),
                stage_type=source.stage_type,
                storyboard_id=source.storyboard_id,
                tile_index=source.tile_index,
                segment_index=source.segment_index,
                profile=source.profile,
                provider=provider,
                priority=source.priority,
                request_parameters=request_parameters,
                effective_parameters={},
                estimated_cost=estimate.amount_cny,
                route_snapshot={
                    'automatic_route': False,
                    'manual_api_regenerate_of': str(source.pk),
                    'confirmed_max_cost_cny': str(confirmed_max),
                },
                max_attempts=1,
            )
            item.depends_on.set(source.depends_on.all())
            if not existed:
                def dispatch_item():
                    try:
                        from apps.inference.tasks import enqueue_work_item
                        enqueue_work_item.apply_async(
                            args=[str(item.pk)], queue='orchestration'
                        )
                    except Exception:
                        # waiting 是持久化事实；reconciler 会在 broker 恢复后补发。
                        return
                transaction.on_commit(dispatch_item)

        return Response(
            {
                'work_item': GenerationWorkItemSerializer(item).data,
                'maximum_estimated_cost_cny': estimate.amount_cny,
                'idempotent_replay': existed,
            },
            status=status.HTTP_200_OK if existed else status.HTTP_202_ACCEPTED,
        )

    def get_serializer_class(self):
        """根据动作选择序列化器"""
        if self.action == 'batch_create':
            return ProjectBatchCreateSerializer
        if self.action == 'asset_bindings':
            if self.request.method.lower() == 'get':
                return ProjectAssetBindingSerializer
            return ProjectAssetBindingUpdateSerializer
        if self.action == "list":
            return ProjectListSerializer
        elif self.action == "retrieve":
            return ProjectDetailSerializer
        elif self.action == "create":
            return ProjectCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return ProjectUpdateSerializer
        return ProjectDetailSerializer

    def _get_accessible_assets(self, user):
        return GlobalVariable.objects.filter(
            Q(created_by=user, scope='user', is_active=True) |
            Q(scope='system', is_active=True)
        ).order_by('group', 'key')

    def _serialize_asset_stage(self, stage):
        output_data = stage.output_data or {}
        return {
            'source_text': output_data.get('source_text', ''),
            'source_type': output_data.get('source_type', 'original_topic'),
            'summary': output_data.get('summary', ''),
            'items': output_data.get('items', []),
            'raw_text': output_data.get('raw_text', ''),
            'updated_at': output_data.get('updated_at'),
        }

    def _serialize_asset_choice(self, asset):
        return {
            'id': str(asset.id),
            'key': asset.key,
            'group': asset.group,
            'description': asset.description,
            'variable_type': asset.variable_type,
            'scope': asset.scope,
            'scope_display': asset.get_scope_display(),
        }

    def _ensure_project_asset_binding(self, project, asset):
        ProjectAssetBinding.objects.get_or_create(project=project, asset=asset)

    def _get_project_image_provider(self, project):
        config = getattr(project, 'model_config', None)
        if config:
            providers = list(config.image_providers.all())
            if providers:
                return providers[0]

        template_set = getattr(project, 'prompt_template_set', None)
        if not template_set:
            template_set = PromptTemplateSet.objects.filter(is_default=True).first()

        if template_set:
            template = PromptTemplate.objects.select_related('model_provider').filter(
                template_set=template_set,
                stage_type='image_generation',
                is_active=True,
            ).first()
            if template and template.model_provider:
                return template.model_provider

        return ModelProvider.objects.filter(provider_type='text2image', is_active=True).first()

    def _build_image_asset_file(self, image_url):
        if not image_url:
            raise ValueError('缺少图片地址')

        if image_url.startswith('/api/v1/content/storage/image/'):
            relative_path = image_url.split('/api/v1/content/storage/image/', 1)[1]
            file_path = image_storage.base_dir / relative_path
            if not file_path.exists():
                raise ValueError('预览图片不存在，无法保存为资产')
            return ContentFile(file_path.read_bytes(), name=Path(relative_path).name)

        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        filename = Path(image_url.split('?', 1)[0]).name or f'{uuid.uuid4().hex}.png'
        return ContentFile(response.content, name=filename)

    def _extract_asset_item(self, stage, temp_id):
        output_data = stage.output_data or {}
        items = output_data.get('items') or []
        for index, item in enumerate(items):
            if item.get('temp_id') == temp_id:
                return output_data, items, index, dict(item)
        return output_data, items, -1, None

    def _persist_asset_item(self, stage, output_data, items, item_index, item):
        items[item_index] = item
        output_data['items'] = items
        output_data['updated_at'] = timezone.now().isoformat()
        stage.output_data = output_data
        stage.save(update_fields=['output_data'])

    def _serialize_asset_binding_results(self, project):
        bindings = project.asset_bindings.select_related('asset').all()
        serializer = ProjectAssetBindingSerializer(bindings, many=True, context=self.get_serializer_context())
        return {'results': serializer.data, 'count': len(serializer.data)}

    def _get_node_chat_template(self, project, node_type):
        stage_type_map = {
            'storyboard': 'storyboard',
            'camera_movement': 'camera_movement',
        }
        stage_type = stage_type_map.get(node_type)
        if not stage_type:
            return None

        template_set = getattr(project, 'prompt_template_set', None)
        if not template_set:
            template_set = PromptTemplateSet.objects.filter(is_default=True).first()

        if not template_set:
            return None

        return PromptTemplate.objects.select_related('model_provider').filter(
            template_set=template_set,
            stage_type=stage_type,
            is_active=True,
        ).first()

    def _get_default_llm_provider(self):
        provider = ModelProvider.objects.filter(
            provider_type='llm',
            is_active=True,
        ).first()
        if not provider:
            raise ValueError('未找到可用的 LLM 模型提供商')
        return provider

    def _get_node_chat_provider(self, project, node_type):
        config = getattr(project, 'model_config', None)
        provider = None

        if config:
            field_name = {
                'storyboard': 'storyboard_providers',
                'camera_movement': 'camera_providers',
            }.get(node_type)
            if field_name:
                providers = list(getattr(config, field_name).all())
                if providers:
                    provider = providers[0]

        if not provider:
            template = self._get_node_chat_template(project, node_type)
            if template and template.model_provider:
                provider = template.model_provider

        if not provider:
            provider = self._get_default_llm_provider()

        return provider

    def _get_node_chat_asset_context(self, project):
        from .asset_context import build_project_asset_context

        return build_project_asset_context(project)

    def _render_node_chat_system_prompt(self, project, node_type, node_payload):
        template = self._get_node_chat_template(project, node_type)
        if not template:
            return ''

        template_vars = {
            **self._get_node_chat_asset_context(project),
            'project': {
                'name': project.name,
                'description': project.description,
                'original_topic': project.original_topic,
            },
            'node': node_payload,
            'chat_mode': 'node_edit',
        }

        try:
            return Template(template.template_content).render(**template_vars)
        except TemplateError as exc:
            raise ValueError(f'节点对话模板渲染失败: {str(exc)}')

    def _build_storyboard_node_payload(self, storyboard):
        return {
            'id': str(storyboard.id),
            'sequence_number': storyboard.sequence_number,
            'scene_description': storyboard.scene_description or '',
            'narration_text': storyboard.narration_text or '',
            'image_prompt': storyboard.image_prompt or '',
            'duration_seconds': storyboard.duration_seconds,
        }

    def _build_camera_node_payload(self, camera):
        storyboard = camera.storyboard
        return {
            'id': str(camera.id),
            'storyboard_id': str(storyboard.id),
            'sequence_number': storyboard.sequence_number,
            'scene_description': storyboard.scene_description or '',
            'narration_text': storyboard.narration_text or '',
            'image_prompt': storyboard.image_prompt or '',
            'movement_type': camera.movement_type or '',
            'movement_params': camera.movement_params,
        }

    def _resolve_node_chat_target(self, project, node_type, node_id):
        from apps.content.models import Storyboard, CameraMovement

        if node_type == 'storyboard':
            storyboard = get_object_or_404(Storyboard, id=node_id, project=project)
            return storyboard, self._build_storyboard_node_payload(storyboard)

        if node_type == 'camera_movement':
            camera = get_object_or_404(
                CameraMovement.objects.select_related('storyboard'),
                id=node_id,
                storyboard__project=project,
            )
            return camera, self._build_camera_node_payload(camera)

        raise ValueError('不支持的节点类型')

    def _build_node_chat_user_prompt(self, node_type, node_payload, messages, user_message):
        node_title = '分镜节点' if node_type == 'storyboard' else '运镜节点'
        output_format = {
            'storyboard': {
                'reply_text': '自然语言回复',
                'apply_patch': {
                    'scene_description': '字符串',
                    'narration_text': '字符串',
                    'image_prompt': '字符串',
                    'duration_seconds': '数字，可选',
                }
            },
            'camera_movement': {
                'reply_text': '自然语言回复',
                'apply_patch': {
                    'movement_type': '字符串',
                    'movement_params': {
                        'description': '字符串',
                    }
                }
            }
        }[node_type]

        conversation = []
        for item in messages or []:
            role = item.get('role') or 'user'
            content = item.get('content') or ''
            if content.strip():
                conversation.append(f'[{role}] {content}')

        conversation_text = '\n'.join(conversation) if conversation else '无历史对话'

        prompt = f"""你是一个视频创作工作流中的{node_title}协作助手。

当前节点内容如下：
{json.dumps(node_payload, ensure_ascii=False, indent=2)}

历史对话：
{conversation_text}

本轮用户诉求：
{user_message}

请根据用户诉求修改当前节点内容，并严格返回 JSON，不要输出 Markdown 代码块。
要求：
1. `reply_text` 用中文解释你做了什么修改。
2. `apply_patch` 只返回应该写回当前节点的字段。
3. 不要返回多余字段。
4. 如果用户要求不明确，请在 `reply_text` 中给出合理假设，但仍提供可应用结果。

返回格式：
{json.dumps(output_format, ensure_ascii=False, indent=2)}
"""
        return prompt

    def _extract_node_chat_result(self, raw_text, node_type, node_payload):
        try:
            json_text = raw_text.strip()
            if '```json' in json_text:
                json_text = json_text.split('```json', 1)[1].split('```', 1)[0].strip()
            elif '```' in json_text:
                json_text = json_text.split('```', 1)[1].split('```', 1)[0].strip()
            data = json.loads(json_text)
        except Exception as exc:
            raise ValueError(f'节点对话结果解析失败: {str(exc)}')

        reply_text = (data.get('reply_text') or '').strip()
        apply_patch = data.get('apply_patch') or {}

        if node_type == 'storyboard':
            normalized_patch = {
                'scene_description': apply_patch.get('scene_description', node_payload.get('scene_description', '')),
                'narration_text': apply_patch.get('narration_text', node_payload.get('narration_text', '')),
                'image_prompt': apply_patch.get('image_prompt', node_payload.get('image_prompt', '')),
            }
            if 'duration_seconds' in apply_patch and apply_patch.get('duration_seconds') not in (None, ''):
                normalized_patch['duration_seconds'] = apply_patch.get('duration_seconds')
            return {
                'reply_text': reply_text or '我已经根据你的要求调整了分镜内容。',
                'apply_patch': normalized_patch,
            }

        current_movement_params = node_payload.get('movement_params') or {}
        patch_params = apply_patch.get('movement_params') or {}
        normalized_patch = {
            'movement_type': apply_patch.get('movement_type', node_payload.get('movement_type', '')),
            'movement_params': {
                **current_movement_params,
                **patch_params,
            }
        }
        return {
            'reply_text': reply_text or '我已经根据你的要求调整了运镜参数。',
            'apply_patch': normalized_patch,
        }

    def _authenticate_stream_user(self, request):
        token = (request.query_params.get('access_token') or '').strip()
        if not token:
            return None

        try:
            access_token = AccessToken(token)
            user_id = access_token.get('user_id')
            if not user_id:
                return None
            return get_user_model().objects.filter(id=user_id).first()
        except Exception:
            return None

    @action(detail=True, methods=['post'], url_path='node-chat-init')
    def node_chat_init(self, request, pk=None):
        project = self.get_object()
        node_type = (request.data.get('node_type') or '').strip()
        node_id = request.data.get('node_id')
        user_message = (request.data.get('user_message') or '').strip()
        messages = request.data.get('messages') or []

        if node_type not in ('storyboard', 'camera_movement'):
            return Response({'error': '不支持的节点类型'}, status=status.HTTP_400_BAD_REQUEST)
        if not node_id:
            return Response({'error': '缺少 node_id'}, status=status.HTTP_400_BAD_REQUEST)
        if not user_message:
            return Response({'error': '缺少 user_message'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            _, node_payload = self._resolve_node_chat_target(project, node_type, node_id)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            provider = self._get_node_chat_provider(project, node_type)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        paid_confirmed = request.data.get('confirm_paid') is True
        if provider.deployment_mode == 'api' and not paid_confirmed:
            return Response(
                {
                    'error': {
                        'code': 'PAID_CONFIRMATION_REQUIRED',
                        'message': '节点对话当前选择付费 API；请先查看估算并显式确认本次付费调用。',
                        'provider_id': str(provider.pk),
                        'capability': 'llm',
                        'stage_type': f'{node_type}_chat',
                        'usage_per_item': {
                            'request_count': 1,
                            'input_tokens': max(1, len(user_message)),
                            'output_tokens': int(getattr(provider, 'max_tokens', 2000) or 2000),
                        },
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        confirmed_max_cost = None
        if provider.deployment_mode == 'api':
            try:
                confirmed_max_cost = Decimal(
                    str(request.data.get('confirmed_max_cost_cny'))
                )
            except (InvalidOperation, TypeError, ValueError):
                return Response(
                    {
                        'error': {
                            'code': 'BUDGET_DENIED',
                            'message': '必须提交本次确认的最大费用。',
                        }
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if confirmed_max_cost < 0:
                return Response(
                    {'error': {'code': 'BUDGET_DENIED', 'message': '确认费用不能小于 0。'}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        stream_token = uuid.uuid4().hex
        cache.set(
            f'project_node_chat_stream:{stream_token}',
            {
                'project_id': str(project.id),
                'user_id': request.user.id,
                'node_type': node_type,
                'node_id': str(node_id),
                'user_message': user_message,
                'messages': messages,
                'node_payload': node_payload,
                'provider_id': str(provider.id),
                'paid_confirmed': paid_confirmed,
                'confirmed_max_cost_cny': (
                    str(confirmed_max_cost) if confirmed_max_cost is not None else None
                ),
            },
            timeout=300,
        )

        return Response({'stream_token': stream_token})

    @action(detail=True, methods=['get'], url_path='node-chat-stream', permission_classes=[AllowAny])
    def node_chat_stream(self, request, pk=None):
        user = self._authenticate_stream_user(request)
        if not user:
            return Response({'error': '未授权访问'}, status=status.HTTP_401_UNAUTHORIZED)

        project = Project.objects.filter(id=pk, user=user).first()
        if not project:
            return Response({'error': '项目不存在或无权限访问'}, status=status.HTTP_404_NOT_FOUND)

        stream_token = request.query_params.get('stream_token', '').strip()
        if not stream_token:
            return Response({'error': '缺少 stream_token'}, status=status.HTTP_400_BAD_REQUEST)

        cache_key = f'project_node_chat_stream:{stream_token}'
        stream_payload = cache.get(cache_key)
        if not stream_payload:
            return Response({'error': '流式对话令牌无效或已过期'}, status=status.HTTP_400_BAD_REQUEST)

        if stream_payload.get('project_id') != str(project.id):
            return Response({'error': '项目不匹配'}, status=status.HTTP_403_FORBIDDEN)
        if stream_payload.get('user_id') != user.id:
            return Response({'error': '无权访问该流式对话'}, status=status.HTTP_403_FORBIDDEN)

        def event_stream():
            try:
                node_type = stream_payload.get('node_type')
                node_id = stream_payload.get('node_id')
                user_message = stream_payload.get('user_message') or ''
                messages = stream_payload.get('messages') or []
                _, node_payload = self._resolve_node_chat_target(project, node_type, node_id)
                provider = ModelProvider.objects.filter(
                    id=stream_payload.get('provider_id'), is_active=True
                ).first()
                if not provider:
                    raise ValueError('节点对话模型已停用或不存在')
                paid_confirmed = stream_payload.get('paid_confirmed') is True
                if provider.deployment_mode == 'api' and not paid_confirmed:
                    raise ValueError('PAID_CONFIRMATION_REQUIRED: 未确认本次节点对话付费调用')
                system_prompt = self._render_node_chat_system_prompt(project, node_type, node_payload)
                prompt = self._build_node_chat_user_prompt(node_type, node_payload, messages, user_message)
                max_tokens = getattr(provider, 'max_tokens', None) or 2000
                temperature = getattr(provider, 'temperature', None)
                if temperature is None:
                    temperature = 0.7

                yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"

                captured_events = []

                def invoke(selected_provider, parameters, _repaired_structure):
                    # SSE 入口不能在三重门外先创建客户端。与阶段流式处理一致，
                    # Hybrid 内完整消费流，再回放 token，确保预算预留先于外呼。
                    ai_client = create_ai_client(selected_provider)
                    final_text = ''
                    metadata = {}
                    for event in ai_client.generate_stream(
                        prompt=parameters['prompt'],
                        system_prompt=parameters.get('system_prompt', ''),
                        max_tokens=parameters.get('max_tokens', selected_provider.max_tokens),
                        temperature=parameters.get('temperature', selected_provider.temperature),
                    ):
                        captured_events.append(event)
                        event_type = event.get('type')
                        if event_type == 'token':
                            final_text = event.get('full_text') or final_text + event.get('content', '')
                        elif event_type == 'done':
                            final_text = event.get('full_text') or final_text
                            metadata = event.get('metadata') or metadata
                        elif event_type == 'error':
                            raise RuntimeError(event.get('error') or '节点对话生成失败')
                    # JSON 契约也在结算前校验；解析失败不能伪装成成功结果。
                    try:
                        self._extract_node_chat_result(final_text, node_type, node_payload)
                    except ValueError as exc:
                        exc.code = 'OUTPUT_SCHEMA_INVALID'
                        raise
                    return AIResponse(success=True, text=final_text, metadata=metadata)

                execution = HybridInferenceService.execute(
                    project=project,
                    capability='llm',
                    stage_type=f'{node_type}_chat',
                    explicit_provider=provider,
                    manual_api=provider.deployment_mode == 'api' and paid_confirmed,
                    request_parameters={
                        'prompt': prompt,
                        'system_prompt': system_prompt,
                        'max_tokens': max_tokens,
                        'temperature': temperature,
                        'output_spec': {'format': 'json'},
                        'confirmed_max_cost_cny': stream_payload.get(
                            'confirmed_max_cost_cny'
                        ),
                    },
                    usage_estimate={
                        'request_count': 1,
                        'input_tokens': max(1, len(prompt) + len(system_prompt)),
                        'output_tokens': max_tokens,
                    },
                    invoke=invoke,
                )
                final_text = execution.value.text
                for event in captured_events:
                    if event.get('type') == 'token':
                        payload = {
                            'type': 'token',
                            'content': event.get('content', ''),
                            'full_text': event.get('full_text') or final_text,
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                result = self._extract_node_chat_result(final_text, node_type, node_payload)
                payload = {
                    'type': 'done',
                    'reply_text': result['reply_text'],
                    'apply_patch': result['apply_patch'],
                    'raw_text': final_text,
                    'metadata': execution.value.metadata or {},
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except Exception as exc:
                payload = json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)
                yield f'data: {payload}\n\n'
            finally:
                cache.delete(cache_key)

        response = StreamingHttpResponse(event_stream(), content_type='text/event-stream; charset=utf-8')
        response['Cache-Control'] = 'no-cache, no-transform'
        response['X-Accel-Buffering'] = 'no'
        return response

    def perform_create(self, serializer):
        """创建项目时自动设置当前用户"""
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        """删除项目前检查状态"""
        instance.delete()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        instance.refresh_from_db()
        detail_serializer = ProjectDetailSerializer(instance, context=self.get_serializer_context())
        return Response(detail_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    @action(detail=False, methods=['post'])
    def batch_create(self, request):
        """批量创建分集。"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        projects = serializer.save()
        response_serializer = ProjectListSerializer(
            projects,
            many=True,
            context=self.get_serializer_context(),
        )
        return Response(
            {
                'count': len(response_serializer.data),
                'results': response_serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'])
    def available_assets(self, request, pk=None):
        """获取当前项目可用的资产列表。"""
        project = self.get_object()
        queryset = self._get_accessible_assets(request.user)
        serializer = GlobalVariableListSerializer(
            queryset,
            many=True,
            context=self.get_serializer_context()
        )
        bound_asset_ids = {str(asset_id) for asset_id in project.asset_bindings.values_list('asset_id', flat=True)}
        results = []
        for item in serializer.data:
            entry = dict(item)
            entry['is_bound'] = entry.get('id') in bound_asset_ids
            results.append(entry)
        return Response({'results': results, 'count': len(results)})

    @action(detail=True, methods=['get', 'patch'])
    def asset_bindings(self, request, pk=None):
        """获取或更新项目资产绑定。"""
        project = self.get_object()

        if request.method.lower() == 'get':
            bindings = project.asset_bindings.select_related('asset').all()
            serializer = ProjectAssetBindingSerializer(
                bindings,
                many=True,
                context=self.get_serializer_context()
            )
            return Response({'results': serializer.data, 'count': len(serializer.data)})

        serializer = ProjectAssetBindingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        asset_ids = serializer.validated_data.get('asset_ids', [])

        accessible_assets = self._get_accessible_assets(request.user).filter(id__in=asset_ids)
        accessible_asset_map = {str(asset.id): asset for asset in accessible_assets}
        missing_ids = [str(asset_id) for asset_id in asset_ids if str(asset_id) not in accessible_asset_map]
        if missing_ids:
            return Response(
                {'error': '存在不可访问的资产', 'asset_ids': missing_ids},
                status=status.HTTP_400_BAD_REQUEST
            )

        project.asset_bindings.exclude(asset_id__in=asset_ids).delete()
        existing_ids = {str(asset_id) for asset_id in project.asset_bindings.values_list('asset_id', flat=True)}
        new_bindings = []
        for asset_id in asset_ids:
            asset_id_str = str(asset_id)
            if asset_id_str in existing_ids:
                continue
            new_bindings.append(ProjectAssetBinding(project=project, asset=accessible_asset_map[asset_id_str]))

        if new_bindings:
            ProjectAssetBinding.objects.bulk_create(new_bindings)

        bindings = project.asset_bindings.select_related('asset').all()
        response_serializer = ProjectAssetBindingSerializer(
            bindings,
            many=True,
            context=self.get_serializer_context()
        )
        return Response({'results': response_serializer.data, 'count': len(response_serializer.data)})

    def _project_task_cache_key(self, project_id):
        return f"project_active_tasks:{project_id}"

    def _get_project_task_ids(self, project_id):
        try:
            return cache.get(self._project_task_cache_key(project_id), [])
        except Exception as error:
            # 这里的缓存只用于批量取消的辅助索引，不是任务是否已入队的事实源。
            # Redis 短暂不可用不能把已经由 Celery 接受的任务误报为 HTTP 500。
            logger.warning('读取项目任务辅助缓存失败: %s', error)
            return []

    def _register_project_task(self, project_id, task_id):
        task_ids = self._get_project_task_ids(project_id)
        if task_id not in task_ids:
            task_ids.append(task_id)
        try:
            cache.set(
                self._project_task_cache_key(project_id),
                task_ids,
                timeout=24 * 60 * 60,
            )
        except Exception as error:
            logger.warning('写入项目任务辅助缓存失败: %s', error)

    def _clear_project_tasks(self, project_id):
        try:
            cache.delete(self._project_task_cache_key(project_id))
        except Exception as error:
            logger.warning('清理项目任务辅助缓存失败: %s', error)

    def _start_pipeline_directly(self, project):
        from apps.projects.tasks import run_full_pipeline_task

        if project.status != "processing":
            project.status = "processing"
            project.save()

        task = run_full_pipeline_task.delay(
            project_id=str(project.id),
            user_id=self.request.user.id
        )
        self._register_project_task(project.id, task.id)

        channel = f"ai_story:project:{project.id}:pipeline"

        return Response(
            {
                "task_id": task.id,
                "channel": channel,
                "message": "工作流已启动",
                "project_id": str(project.id),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _revoke_project_tasks(self, project_id):
        task_ids = self._get_project_task_ids(project_id)
        revoked_task_ids = []

        for task_id in task_ids:
            try:
                AsyncResult(task_id).revoke(terminate=True)
                revoked_task_ids.append(task_id)
            except Exception:
                continue

        self._clear_project_tasks(project_id)
        return revoked_task_ids

    @action(detail=True, methods=["get"])
    def stages(self, request, pk=None):
        """
        获取项目的所有阶段（简化版）
        GET /api/v1/projects/{id}/stages/

        返回格式:
        - 只返回 rewrite 和 storyboard 两条记录
        - storyboard 的 domain_data 中整合了 image_generation、camera_movement、video_generation 数据
        - 数据按分镜场景分组，每个场景包含对应的图片、运镜、视频数据
        """
        project = self.get_object()
        stage_template_states = get_stage_template_states(project)

        # 获取 rewrite / asset_extraction / storyboard 阶段
        rewrite_stage = project.stages.filter(stage_type='rewrite').first()
        asset_extraction_stage = project.stages.filter(stage_type='asset_extraction').first()
        storyboard_stage = project.stages.filter(stage_type='storyboard').first()

        # 获取其他阶段用于数据整合
        image_stage = project.stages.filter(stage_type='image_generation').first()
        multi_grid_stage = project.stages.filter(stage_type='multi_grid_image').first()
        camera_stage = project.stages.filter(stage_type='camera_movement').first()
        video_stage = project.stages.filter(stage_type='video_generation').first()
        image_edit_stage = project.stages.filter(stage_type='image_edit').first()

        result = []

        # 1. 添加 rewrite 阶段（即使不存在也返回空结构）
        if rewrite_stage:
            rewrite_serializer = ProjectStageSerializer(rewrite_stage)
            rewrite_data = rewrite_serializer.data
            rewrite_data['template_enabled'] = stage_template_states.get('rewrite', True)
            result.append(rewrite_data)
        else:
            # 返回空的结构化数据
            result.append({
                'id': None,
                'project': project.id,
                'stage_type': 'rewrite',
                'stage_type_display': '剧本精修',
                'status': 'pending',
                'status_display': '待处理',
                'input_data': None,
                'output_data': None,
                'retry_count': 0,
                'max_retries': 3,
                'error_message': None,
                'started_at': None,
                'completed_at': None,
                'created_at': None,
                'template_enabled': stage_template_states.get('rewrite', True),
                'domain_data': {
                    'content_rewrite': None
                }
            })

        if asset_extraction_stage:
            asset_extraction_serializer = ProjectStageSerializer(asset_extraction_stage)
            asset_extraction_data = asset_extraction_serializer.data
            asset_extraction_data['template_enabled'] = stage_template_states.get('asset_extraction', True)
            asset_extraction_data['domain_data'] = self._serialize_asset_stage(asset_extraction_stage)
            result.append(asset_extraction_data)
        else:
            result.append({
                'id': None,
                'project': project.id,
                'stage_type': 'asset_extraction',
                'stage_type_display': '资产抽取',
                'status': 'pending',
                'status_display': '待处理',
                'input_data': None,
                'output_data': None,
                'retry_count': 0,
                'max_retries': 3,
                'error_message': None,
                'started_at': None,
                'completed_at': None,
                'created_at': None,
                'template_enabled': stage_template_states.get('asset_extraction', True),
                'domain_data': {
                    'source_text': '',
                    'source_type': 'original_topic',
                    'summary': '',
                    'items': [],
                    'raw_text': '',
                    'updated_at': None,
                }
            })

        # 2. 添加 storyboard 阶段（整合其他阶段数据）
        if storyboard_stage:
            storyboard_serializer = ProjectStageSerializer(storyboard_stage)
            storyboard_data = storyboard_serializer.data
            storyboard_data['template_enabled'] = stage_template_states.get('storyboard', True)

            # 整合其他阶段的数据到 domain_data 中
            if storyboard_data.get('domain_data'):
                storyboard_data['domain_data'] = self._integrate_stage_data(
                    project,
                    storyboard_data['domain_data'],
                    image_stage,
                    multi_grid_stage,
                    camera_stage,
                    video_stage,
                    image_edit_stage,
                    stage_template_states
                )

            result.append(storyboard_data)

        return Response(result)

    def _integrate_stage_data(self, project, storyboard_domain_data, image_stage, multi_grid_stage, camera_stage, video_stage, image_edit_stage, stage_template_states=None):
        """
        将 image_generation、multi_grid_image、camera_movement、video_generation、image_edit 数据整合到 storyboard 的 domain_data 中

        Args:
            project: 项目对象
            storyboard_domain_data: storyboard 的原始 domain_data
            image_stage: image_generation 阶段对象
            multi_grid_stage: multi_grid_image 阶段对象
            camera_stage: camera_movement 阶段对象
            video_stage: video_generation 阶段对象
            image_edit_stage: image_edit 阶段对象
            stage_template_states: 各阶段模板启用状态

        Returns:
            dict: 整合后的 domain_data
        """
        from apps.content.models import GeneratedImage, CameraMovement, GeneratedVideo, MultiGridImageTask, EditedImage

        stage_template_states = stage_template_states or {}

        # 获取所有分镜场景
        storyboards = storyboard_domain_data.get('storyboards', [])

        # 为每个分镜场景整合数据
        for sb_data in storyboards:
            storyboard_id = sb_data.get('id')

            # 初始化默认数据结构
            sb_data['image_generation'] = {
                'template_enabled': stage_template_states.get('image_generation', True),
                'status': image_stage.status if image_stage else 'pending',
                'images': []
            }
            sb_data['multi_grid_image'] = {
                'template_enabled': stage_template_states.get('multi_grid_image', True),
                'status': multi_grid_stage.status if multi_grid_stage else 'pending',
                'tasks': []
            }
            sb_data['camera_movement'] = {
                'template_enabled': stage_template_states.get('camera_movement', True),
                'status': camera_stage.status if camera_stage else 'pending',
                'data': None
            }
            sb_data['video_generation'] = {
                'template_enabled': stage_template_states.get('video_generation', True),
                'status': video_stage.status if video_stage else 'pending',
                'videos': []
            }
            sb_data['image_edit'] = {
                'template_enabled': stage_template_states.get('image_edit', True),
                'status': image_edit_stage.status if image_edit_stage else 'pending',
                'results': []
            }

            if not storyboard_id:
                continue

            try:
                # 1. 整合 image_generation 数据
                if image_stage:
                    images = GeneratedImage.objects.filter(
                        storyboard_id=storyboard_id
                    ).select_related('model_provider').order_by('-created_at')

                    sb_data['image_generation']['images'] = [
                        {
                            'id': str(img.id),
                            'image_url': img.image_url,
                            'thumbnail_url': img.thumbnail_url,
                            'width': img.width,
                            'height': img.height,
                            'file_size': img.file_size,
                            'status': img.status,
                            'status_display': img.get_status_display(),
                            'model_provider': {
                                'id': str(img.model_provider.id) if img.model_provider else None,
                                'name': img.model_provider.name if img.model_provider else None,
                                'model_name': img.model_provider.model_name if img.model_provider else None,
                            } if img.model_provider else None,
                            'generation_params': img.generation_params,
                            'retry_count': img.retry_count,
                            'created_at': img.created_at.isoformat() if img.created_at else None,
                        }
                        for img in images
                    ]

                # 2. 整合 multi_grid_image 数据
                if multi_grid_stage:
                    tasks = MultiGridImageTask.objects.filter(
                        storyboard_id=storyboard_id
                    ).select_related('model_provider').prefetch_related('tiles').order_by('-created_at')

                    sb_data['multi_grid_image']['tasks'] = [
                        {
                            'id': str(task.id),
                            'source_image_url': task.source_image_url,
                            'grid_rows': task.grid_rows,
                            'grid_cols': task.grid_cols,
                            'tile_gap': task.tile_gap,
                            'outer_padding': task.outer_padding,
                            'status': task.status,
                            'model_provider': {
                                'id': str(task.model_provider.id) if task.model_provider else None,
                                'name': task.model_provider.name if task.model_provider else None,
                                'model_name': task.model_provider.model_name if task.model_provider else None,
                            } if task.model_provider else None,
                            'tiles': [
                                {
                                    'id': str(tile.id),
                                    'tile_index': tile.tile_index,
                                    'row_index': tile.row_index,
                                    'col_index': tile.col_index,
                                    'crop_box': tile.crop_box,
                                    'tile_image_url': tile.tile_image_url,
                                    'width': tile.width,
                                    'height': tile.height,
                                    'status': tile.status,
                                }
                                for tile in task.tiles.all().order_by('tile_index')
                            ],
                        }
                        for task in tasks
                    ]

                # 3. 整合 camera_movement 数据
                if camera_stage:
                    try:
                        camera = CameraMovement.objects.select_related('model_provider').get(
                            storyboard_id=storyboard_id
                        )
                        sb_data['camera_movement']['data'] = {
                            'id': str(camera.id),
                            'movement_type': camera.movement_type,
                            'movement_type_display': camera.get_movement_type_display() if camera.movement_type else None,
                            'movement_params': camera.movement_params,
                            'model_provider': {
                                'id': str(camera.model_provider.id) if camera.model_provider else None,
                                'name': camera.model_provider.name if camera.model_provider else None,
                                'model_name': camera.model_provider.model_name if camera.model_provider else None,
                            } if camera.model_provider else None,
                            'prompt_used': camera.prompt_used,
                            'generation_metadata': camera.generation_metadata,
                            'created_at': camera.created_at.isoformat() if camera.created_at else None,
                            'updated_at': camera.updated_at.isoformat() if camera.updated_at else None,
                        }
                    except CameraMovement.DoesNotExist:
                        pass

                # 4. 整合 video_generation 数据
                if video_stage:
                    videos = GeneratedVideo.objects.filter(
                        storyboard_id=storyboard_id
                    ).select_related('model_provider', 'image', 'camera_movement').order_by('-created_at')

                    sb_data['video_generation']['videos'] = [
                        {
                            'id': str(video.id),
                            'video_url': video.video_url,
                            'thumbnail_url': video.thumbnail_url,
                            'duration': video.duration,
                            'width': video.width,
                            'height': video.height,
                            'fps': video.fps,
                            'file_size': video.file_size,
                            'status': video.status,
                            'status_display': video.get_status_display(),
                            'image_id': str(video.image.id) if video.image else None,
                            'camera_movement_id': str(video.camera_movement.id) if video.camera_movement else None,
                            'model_provider': {
                                'id': str(video.model_provider.id) if video.model_provider else None,
                                'name': video.model_provider.name if video.model_provider else None,
                                'model_name': video.model_provider.model_name if video.model_provider else None,
                            } if video.model_provider else None,
                            'generation_params': video.generation_params,
                            'retry_count': video.retry_count,
                            'created_at': video.created_at.isoformat() if video.created_at else None,
                        }
                        for video in videos
                    ]


                # 5. 整合 image_edit 数据
                if image_edit_stage:
                    edited_images = EditedImage.objects.filter(
                        storyboard_id=storyboard_id
                    ).select_related('model_provider', 'multi_grid_task', 'multi_grid_tile').order_by('-created_at')

                    sb_data['image_edit']['results'] = [
                        {
                            'id': str(item.id),
                            'source_stage_type': item.source_stage_type,
                            'source_image_url': item.source_image_url,
                            'edited_image_url': item.edited_image_url,
                            'status': item.status,
                            'tile_index': item.multi_grid_tile.tile_index if item.multi_grid_tile else None,
                            'row_index': item.multi_grid_tile.row_index if item.multi_grid_tile else None,
                            'col_index': item.multi_grid_tile.col_index if item.multi_grid_tile else None,
                            'width': item.width,
                            'height': item.height,
                            'model_provider': {
                                'id': str(item.model_provider.id) if item.model_provider else None,
                                'name': item.model_provider.name if item.model_provider else None,
                                'model_name': item.model_provider.model_name if item.model_provider else None,
                            } if item.model_provider else None,
                            'generation_params': item.generation_params,
                            'generation_metadata': item.generation_metadata,
                            'created_at': item.created_at.isoformat() if item.created_at else None,
                        }
                        for item in edited_images
                    ]

            except Exception as e:
                # 记录错误但不中断处理
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"整合分镜 {storyboard_id} 的数据失败: {str(e)}", exc_info=True)

        return storyboard_domain_data

    @action(detail=True, methods=["post"])
    def execute_stage(self, request, pk=None):
        """
        执行指定阶段
        POST /api/v1/projects/projects/{id}/execute_stage/
        Body: {
            "stage_name": "rewrite",
            "input_data": {...},
            "use_streaming": false  // 可选，true=SSE流式(旧方式), false=Celery异步(默认)
        }

        模式1 (默认): Celery异步任务
        返回:
        {
            "task_id": "celery-task-id",
            "channel": "ai_story:project:xxx:stage:rewrite",
            "message": "任务已启动"
        }
        """
        project = self.get_object()
        serializer = StageExecuteSerializer(
            data=request.data, context={"project_id": str(project.id)}
        )
        serializer.is_valid(raise_exception=True)

        stage_name = serializer.validated_data["stage_name"]
        input_data = serializer.validated_data.get("input_data", {})
        if "original_topic" in input_data and "raw_text" not in input_data:
            input_data["raw_text"] = input_data["original_topic"]
        # 获取阶段
        stage = get_object_or_404(ProjectStage, project=project, stage_type=stage_name)

        if not is_stage_template_enabled(project, stage_name):
            now = timezone.now()
            stage.status = 'skipped'
            stage.started_at = now
            stage.completed_at = now
            stage.error_message = f'{stage.get_stage_type_display()} 对应提示词模板未开启，已跳过该阶段'
            stage.save(update_fields=['status', 'started_at', 'completed_at', 'error_message'])

            return Response(
                {
                    'message': stage.error_message,
                    'skipped': True,
                    'stage': ProjectStageSerializer(stage).data,
                    'project_id': str(project.id),
                },
                status=status.HTTP_200_OK,
            )

        explicit_provider_id = (
            request.data.get('explicit_provider_id')
            or input_data.get('explicit_provider_id')
        )
        manual_api = bool(request.data.get('manual_api') or input_data.get('manual_api'))
        if explicit_provider_id or manual_api:
            return self._execute_manual_api_stage(
                project=project,
                stage_name=stage_name,
                input_data=input_data,
                provider_id=explicit_provider_id,
                confirmed=(
                    request.data.get('confirm_paid_generation') is True
                    or input_data.get('confirm_paid_generation') is True
                ),
                confirmed_max_cost_cny=(
                    request.data.get('confirmed_max_cost_cny')
                    or input_data.get('confirmed_max_cost_cny')
                ),
                idempotency_key=request.headers.get('Idempotency-Key', ''),
            )

        # 模式2: Celery异步任务 (默认，推荐)
        return self._execute_stage_async(project, stage_name, input_data)

    def _execute_manual_api_stage(
        self,
        *,
        project,
        stage_name,
        input_data,
        provider_id,
        confirmed,
        confirmed_max_cost_cny,
        idempotency_key,
    ):
        """将一次明确 API 重生成原子规划为付费工作项。

        先在同一数据库事务中生成完整工作项集合并汇总最坏费用；任一费率缺失
        或总额超过本次确认值都会回滚整个计划，保证不会出现“只派发了一半”的
        付费阶段。视频的最终 compose 项仍自动走本地 motion_render 路由。
        """

        if not getattr(settings, 'AI_ROUTER_V2_ENABLED', False):
            return Response(
                {
                    'error': {
                        'code': 'ROUTER_DISABLED',
                        'message': '显式 API 阶段重生成仅在 AI_ROUTER_V2_ENABLED 开启后可用。',
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        capability_by_stage = {
            'rewrite': 'llm',
            'asset_extraction': 'llm',
            'storyboard': 'llm',
            'camera_movement': 'llm',
            'image_generation': 'text2image',
            'image_edit': 'image_edit',
            'video_generation': 'image2video',
        }
        capability = capability_by_stage.get(stage_name)
        provider = ModelProvider.objects.filter(
            pk=provider_id,
            provider_type=capability,
            deployment_mode='api',
            is_active=True,
        ).first()
        if provider is None:
            return Response(
                {'error': {'code': 'INVALID_REQUEST', 'message': '显式 API Provider 不存在或能力不匹配。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not confirmed:
            return Response(
                {
                    'error': {
                        'code': 'PAID_CONFIRMATION_REQUIRED',
                        'message': '请先查看最大预计费用并确认本次 API 阶段重生成。',
                        'provider_id': str(provider.pk),
                        'capability': capability,
                        'stage_type': stage_name,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            confirmed_max = Decimal(str(confirmed_max_cost_cny))
        except (InvalidOperation, TypeError, ValueError):
            return Response(
                {'error': {'code': 'BUDGET_DENIED', 'message': '必须提交本次确认的最大费用。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        client_key = str(idempotency_key or '').strip()
        if not client_key or len(client_key) > 200:
            return Response(
                {'error': {'code': 'INVALID_IDEMPOTENCY_KEY', 'message': '必须提供 1–200 字符的 Idempotency-Key。'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ai_settings = ProjectAISettings.objects.filter(project=project, is_active=True).first()
        policy = getattr(ai_settings, 'budget_policy', None) or AIBudgetPolicy.objects.filter(
            is_active=True
        ).order_by('created_at').first()
        if not ai_settings or not (
            ai_settings.allow_cloud_data_transfer
            and ai_settings.cloud_authorized_by_id
            and ai_settings.cloud_authorized_at
        ):
            return Response(
                {'error': {'code': 'CLOUD_NOT_AUTHORIZED', 'message': '项目未授权数据出站。'}},
                status=status.HTTP_403_FORBIDDEN,
            )
        if (
            ai_settings.project_budget_cny <= 0
            or policy is None
            or policy.daily_limit_cny <= 0
            or policy.monthly_limit_cny <= 0
        ):
            return Response(
                {'error': {'code': 'BUDGET_DENIED', 'message': '项目或全局预算仍为 0。'}},
                status=status.HTTP_409_CONFLICT,
            )

        execution_material = f'{project.pk}:{stage_name}:{provider.pk}:{client_key}'
        execution_id = uuid.uuid5(uuid.NAMESPACE_URL, execution_material)
        storyboard_ids = input_data.get('storyboard_ids')
        runtime_overrides = {
            key: value for key, value in input_data.items()
            if key not in {
                'storyboard_ids', 'force_regenerate', 'manual_api',
                'explicit_provider_id', 'confirm_paid_generation',
                'confirmed_max_cost_cny',
            }
        }
        try:
            with transaction.atomic():
                plan = StageWorkItemPlanner.plan_stage(
                    project=project,
                    stage_type=stage_name,
                    storyboard_ids=storyboard_ids,
                    force_regenerate=bool(input_data.get('force_regenerate', True)),
                    runtime_overrides=runtime_overrides,
                    stage_execution_id=execution_id,
                    enqueue=False,
                    explicit_provider=provider,
                    manual_api=True,
                )
                total = Decimal('0')
                paid_items = []
                for item in plan.work_items:
                    if item.provider_id != provider.pk:
                        continue
                    usage = (item.request_parameters or {}).get('usage_estimate') or {}
                    estimate = PricingService.estimate(
                        item.capability,
                        provider.model_name,
                        usage,
                        provider=provider,
                        context={
                            'profile': getattr(item.profile, 'key', ai_settings.default_profile_code),
                            'stage_type': item.stage_type,
                        },
                    )
                    if not estimate.is_complete:
                        raise StagePlanningError(
                            f'PRICE_MISSING:{item.pk}:缺少匹配工作项用量的有效价目表'
                        )
                    total += estimate.amount_cny
                    paid_items.append((item, estimate.amount_cny))
                if not paid_items:
                    raise StagePlanningError('INVALID_REQUEST:阶段没有可由该 API Provider 执行的工作项')
                if total > confirmed_max:
                    raise StagePlanningError(
                        f'BUDGET_DENIED:当前最坏估算 {total} CNY 超过确认上限 {confirmed_max} CNY'
                    )
                for item, item_cost in paid_items:
                    item.request_parameters = {
                        **(item.request_parameters or {}),
                        'manual_api': True,
                        # 每个工作项只可消费自己的估算份额；总额已在本事务中校验。
                        'confirmed_max_cost_cny': str(item_cost),
                    }
                    item.estimated_cost = item_cost
                    item.max_attempts = 1
                    item.route_snapshot = {
                        **(item.route_snapshot or {}),
                        'manual_api_stage': True,
                        'confirmed_stage_max_cost_cny': str(confirmed_max),
                        'estimated_stage_cost_cny': str(total),
                    }
                    item.save(update_fields=[
                        'request_parameters', 'estimated_cost', 'max_attempts',
                        'route_snapshot', 'updated_at',
                    ])

                root_ids = [
                    str(item.pk) for item in plan.work_items if not item.depends_on.exists()
                ]
                def dispatch_items():
                    try:
                        from apps.inference.tasks import enqueue_work_item
                        for item_id in root_ids:
                            enqueue_work_item.apply_async(
                                args=[item_id], queue='orchestration'
                            )
                    except Exception:
                        return
                transaction.on_commit(dispatch_items)
        except StagePlanningError as error:
            raw = str(error)
            code, _, message = raw.partition(':')
            known = {'PRICE_MISSING', 'BUDGET_DENIED', 'INVALID_REQUEST'}
            return Response(
                {
                    'error': {
                        'code': code if code in known else 'INVALID_REQUEST',
                        'message': message or raw,
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                'stage': stage_name,
                'stage_execution_id': str(plan.stage_execution_id),
                'work_item_ids': [str(item.pk) for item in plan.work_items],
                'paid_work_item_count': len(paid_items),
                'maximum_estimated_cost_cny': total,
                'idempotent_replay': plan.created_count == 0,
                'message': '已创建受费用上限保护的 API 重生成工作项。',
            },
            status=status.HTTP_200_OK if plan.created_count == 0 else status.HTTP_202_ACCEPTED,
        )

    def _execute_stage_async(self, project, stage_name, input_data):
        """
        使用Celery异步执行阶段 (推荐方式)
        """
        from apps.projects.tasks import (
            execute_image2video_stage,
            execute_llm_stage,
            execute_image_edit_stage,
            execute_multi_grid_image_stage,
            execute_text2image_stage,
        )

        # 根据阶段类型启动对应的Celery任务
        if stage_name in ["rewrite", "asset_extraction", "storyboard", "camera_movement"]:
            # LLM类阶段
            print("execute_llm_stage", settings.CELERY_BROKER_URL, execute_llm_stage.app.conf.broker_url)
            task = execute_llm_stage.delay(
                project_id=str(project.id),
                stage_name=stage_name,
                input_data=input_data,
                user_id=self.request.user.id,
            )
        elif stage_name == "image_generation":
            storyboard_ids = input_data.get("storyboard_ids", None)
            force_regenerate = input_data.get("force_regenerate", False)
            task = execute_text2image_stage.delay(
                project_id=str(project.id),
                storyboard_ids=storyboard_ids,
                force_regenerate=force_regenerate,
                user_id=self.request.user.id,
            )
        elif stage_name == "multi_grid_image":
            storyboard_ids = input_data.get("storyboard_ids", None)
            force_regenerate = input_data.get("force_regenerate", False)
            task = execute_multi_grid_image_stage.delay(
                project_id=str(project.id),
                storyboard_ids=storyboard_ids,
                force_regenerate=force_regenerate,
                user_id=self.request.user.id,
                grid_rows=input_data.get("grid_rows", 2),
                grid_cols=input_data.get("grid_cols", 2),
                tile_gap=input_data.get("tile_gap", 0),
                outer_padding=input_data.get("outer_padding", 0),
            )
        elif stage_name == "image_edit":
            storyboard_ids = input_data.get("storyboard_ids", None)
            force_regenerate = input_data.get("force_regenerate", False)
            task = execute_image_edit_stage.delay(
                project_id=str(project.id),
                storyboard_ids=storyboard_ids,
                force_regenerate=force_regenerate,
                user_id=self.request.user.id,
                strength=input_data.get("strength", 0.35),
                width=input_data.get("width", None),
                height=input_data.get("height", None),
            )
        elif stage_name == "video_generation":
            # 图生视频阶段
            storyboard_ids = input_data.get("storyboard_ids", None)
            force_regenerate = input_data.get("force_regenerate", False)
            task = execute_image2video_stage.delay(
                project_id=str(project.id),
                storyboard_ids=storyboard_ids,
                force_regenerate=force_regenerate,
                user_id=self.request.user.id,
            )
        else:
            return Response(
                {"error": f"未知阶段类型: {stage_name}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 构建Redis频道名称
        channel = f"ai_story:project:{project.id}:stage:{stage_name}"
        self._register_project_task(project.id, task.id)

        return Response(
            {
                "task_id": task.id,
                "channel": channel,
                "stage": stage_name,
                "message": f"阶段 {stage_name} 任务已启动",
                "project_id": str(project.id),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @staticmethod
    def _pipeline_direct_api_providers(project):
        return pipeline_direct_api_providers(project)

    @action(detail=True, methods=['post'])
    def apply_asset_extraction(self, request, pk=None):
        project = self.get_object()
        stage = get_object_or_404(ProjectStage, project=project, stage_type='asset_extraction')
        output_data = stage.output_data or {}
        stage_items = output_data.get('items') or []
        request_items = request.data.get('items') or []

        stage_item_map = {item.get('temp_id'): dict(item) for item in stage_items if item.get('temp_id')}
        accessible_assets = {
            str(asset.id): asset
            for asset in self._get_accessible_assets(request.user)
        }

        results = []
        for entry in request_items:
            temp_id = entry.get('temp_id')
            action = entry.get('action')
            stage_item = stage_item_map.get(temp_id)
            if not stage_item or action not in {'bind_existing', 'create_new', 'overwrite_existing', 'skip'}:
                continue

            stage_item['selected_action'] = action
            stage_item['selected_asset_id'] = entry.get('asset_id')
            result_entry = {'temp_id': temp_id, 'action': action, 'status': 'skipped'}

            if action == 'skip':
                result_entry['status'] = 'skipped'
                results.append(result_entry)
                stage_item_map[temp_id] = stage_item
                continue

            if action in {'bind_existing', 'overwrite_existing'}:
                asset_id = str(entry.get('asset_id') or '')
                asset = accessible_assets.get(asset_id)
                if not asset:
                    result_entry['status'] = 'error'
                    result_entry['error'] = '指定资产不存在或无权限访问'
                    results.append(result_entry)
                    stage_item_map[temp_id] = stage_item
                    continue

                if action == 'overwrite_existing':
                    asset.key = entry.get('asset', {}).get('key') or stage_item.get('key') or asset.key
                    asset.group = entry.get('asset', {}).get('group') or stage_item.get('group') or asset.group
                    asset.description = entry.get('asset', {}).get('description') or asset.description
                    asset.variable_type = entry.get('asset', {}).get('variable_type') or stage_item.get('variable_type') or asset.variable_type
                    stage_value = entry.get('asset', {}).get('value', stage_item.get('value'))
                    asset.value = json.dumps(stage_value, ensure_ascii=False) if asset.variable_type == 'json' else str(stage_value or '')
                    asset.save()

                self._ensure_project_asset_binding(project, asset)
                stage_item['selected_asset_id'] = str(asset.id)
                stage_item['match_status'] = 'matched'
                result_entry['status'] = 'applied'
                result_entry['asset'] = self._serialize_asset_choice(asset)
                results.append(result_entry)
                stage_item_map[temp_id] = stage_item
                continue

            if action == 'create_new':
                asset_payload = entry.get('asset') or {}
                key = asset_payload.get('key') or stage_item.get('key')
                variable_type = asset_payload.get('variable_type') or stage_item.get('variable_type') or 'json'
                value = asset_payload.get('value', stage_item.get('value'))
                asset = GlobalVariable.objects.create(
                    key=key,
                    value=json.dumps(value, ensure_ascii=False) if variable_type == 'json' else str(value or ''),
                    variable_type=variable_type,
                    scope='user',
                    group=asset_payload.get('group') or stage_item.get('group') or '',
                    description=asset_payload.get('description') or '从资产抽取节点生成',
                    created_by=request.user,
                    is_active=True,
                )
                self._ensure_project_asset_binding(project, asset)
                stage_item['selected_asset_id'] = str(asset.id)
                stage_item['match_status'] = 'matched'
                result_entry['status'] = 'applied'
                result_entry['asset'] = self._serialize_asset_choice(asset)
                results.append(result_entry)
                stage_item_map[temp_id] = stage_item

        output_data['items'] = [stage_item_map.get(item.get('temp_id'), item) for item in stage_items]
        output_data['updated_at'] = timezone.now().isoformat()
        stage.output_data = output_data
        stage.save(update_fields=['output_data'])

        return Response({
            'message': '资产抽取结果已应用',
            'results': results,
            'stage': ProjectStageSerializer(stage).data,
            'bindings': self._serialize_asset_binding_results(project),
        })

    @action(detail=True, methods=['patch'])
    def asset_extraction_item(self, request, pk=None):
        project = self.get_object()
        stage = get_object_or_404(ProjectStage, project=project, stage_type='asset_extraction')
        temp_id = request.data.get('temp_id')

        if not temp_id:
            return Response({'error': '缺少 temp_id'}, status=status.HTTP_400_BAD_REQUEST)

        output_data, items, item_index, item = self._extract_asset_item(stage, temp_id)
        if item is None:
            return Response({'error': '未找到对应的抽取项'}, status=status.HTTP_404_NOT_FOUND)

        editable_fields = [
            'key', 'label', 'group', 'variable_type', 'value', 'description', 'generation_prompt',
            'selected_action', 'selected_asset_id',
        ]
        for field in editable_fields:
            if field in request.data:
                item[field] = request.data.get(field)

        self._persist_asset_item(stage, output_data, items, item_index, item)

        return Response({
            'message': '抽取项已更新',
            'item': item,
            'stage': ProjectStageSerializer(stage).data,
        })

    @action(detail=True, methods=['post'])
    def asset_extraction_generate_image(self, request, pk=None):
        project = self.get_object()
        stage = get_object_or_404(ProjectStage, project=project, stage_type='asset_extraction')
        temp_id = request.data.get('temp_id')
        prompt = (request.data.get('prompt') or '').strip()

        if not temp_id:
            return Response({'error': '缺少 temp_id'}, status=status.HTTP_400_BAD_REQUEST)
        if not prompt:
            return Response({'error': '缺少生成提示词'}, status=status.HTTP_400_BAD_REQUEST)

        output_data, items, item_index, item = self._extract_asset_item(stage, temp_id)
        if item is None:
            return Response({'error': '未找到对应的抽取项'}, status=status.HTTP_404_NOT_FOUND)

        provider = self._get_project_image_provider(project)
        if not provider:
            return Response({'error': '未配置可用的文生图模型'}, status=status.HTTP_400_BAD_REQUEST)

        paid_confirmed = request.data.get('confirm_paid') is True
        if provider.deployment_mode == 'api' and not paid_confirmed:
            return Response(
                {
                    'error': {
                        'code': 'PAID_CONFIRMATION_REQUIRED',
                        'message': '图片预览当前选择付费 API；请先查看估算并显式确认本次付费调用。',
                        'provider_id': str(provider.pk),
                        'capability': 'text2image',
                        'stage_type': 'asset_extraction_preview',
                        'usage_per_item': {
                            'request_count': 1,
                            'image_count': 1,
                        },
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        confirmed_max_cost = None
        if provider.deployment_mode == 'api':
            try:
                confirmed_max_cost = Decimal(
                    str(request.data.get('confirmed_max_cost_cny'))
                )
            except (InvalidOperation, TypeError, ValueError):
                return Response(
                    {'error': {'code': 'BUDGET_DENIED', 'message': '必须提交本次确认的最大费用。'}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        width = int(provider.extra_config.get('width', 1024)) if provider.extra_config else 1024
        height = int(provider.extra_config.get('height', 1024)) if provider.extra_config else 1024

        request_parameters = {
            'prompt': prompt,
            'ratio': '1:1',
            'resolution': '2k',
            'width': width,
            'height': height,
            'output_spec': {'width': width, 'height': height, 'sample_count': 1},
            'confirmed_max_cost_cny': (
                str(confirmed_max_cost) if confirmed_max_cost is not None else None
            ),
        }

        def invoke(selected_provider, parameters, _repaired_structure):
            client = create_ai_client(selected_provider)
            return client.generate(
                api_url=selected_provider.api_url,
                session_id=selected_provider.api_key,
                model=selected_provider.model_name,
                prompt=parameters['prompt'],
                ratio=parameters['ratio'],
                resolution=parameters['resolution'],
                width=parameters['width'],
                height=parameters['height'],
            )

        try:
            execution = HybridInferenceService.execute(
                project=project,
                capability='text2image',
                stage_type='asset_extraction_preview',
                explicit_provider=provider,
                manual_api=provider.deployment_mode == 'api' and paid_confirmed,
                request_parameters=request_parameters,
                usage_estimate={
                    'request_count': 1,
                    'image_count': 1,
                    'width': width,
                    'height': height,
                },
                invoke=invoke,
            )
            response = execution.value
        except Exception as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if isinstance(response, dict):
            images = response.get('data')
            response_success = response.get('success', bool(images))
            response_error = response.get('error')
        else:
            images = getattr(response, 'data', None)
            response_success = getattr(response, 'success', bool(images))
            response_error = getattr(response, 'error', None)
        if isinstance(images, dict):
            images = [images]
        if not response or not response_success or not isinstance(images, list) or not images:
            return Response(
                {'error': response_error or '文生图生成失败'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        preview = dict(images[0])
        preview['prompt'] = prompt
        preview['provider'] = {
            'id': str(provider.id),
            'name': provider.name,
            'model_name': provider.model_name,
        }
        preview['created_at'] = timezone.now().isoformat()

        item['generated_image_preview'] = preview
        item['generation_prompt'] = prompt
        self._persist_asset_item(stage, output_data, items, item_index, item)

        return Response({
            'message': '图片预览已生成',
            'preview': preview,
            'item': item,
            'stage': ProjectStageSerializer(stage).data,
        })

    @action(detail=True, methods=['post'])
    def asset_extraction_confirm_image(self, request, pk=None):
        project = self.get_object()
        stage = get_object_or_404(ProjectStage, project=project, stage_type='asset_extraction')
        temp_id = request.data.get('temp_id')

        if not temp_id:
            return Response({'error': '缺少 temp_id'}, status=status.HTTP_400_BAD_REQUEST)

        output_data, items, item_index, item = self._extract_asset_item(stage, temp_id)
        if item is None:
            return Response({'error': '未找到对应的抽取项'}, status=status.HTTP_404_NOT_FOUND)

        preview = item.get('generated_image_preview') or {}
        preview_url = preview.get('url')
        if not preview_url:
            return Response({'error': '当前抽取项没有可确认的图片预览'}, status=status.HTTP_400_BAD_REQUEST)

        asset_key = (request.data.get('key') or item.get('key') or '').strip()
        if not asset_key:
            return Response({'error': '资产键不能为空'}, status=status.HTTP_400_BAD_REQUEST)

        existing_asset = GlobalVariable.objects.filter(
            key=asset_key,
            created_by=request.user,
            scope='user',
        ).first()
        if existing_asset:
            return Response({'error': f'资产键 {asset_key} 已存在，请先修改资产键'}, status=status.HTTP_400_BAD_REQUEST)

        image_file = self._build_image_asset_file(preview_url)
        asset = GlobalVariable(
            key=asset_key,
            value=(request.data.get('value') or item.get('generation_prompt') or item.get('value') or '').strip(),
            variable_type='image',
            scope='user',
            group=(request.data.get('group') or item.get('group') or '图片资产').strip(),
            description=(request.data.get('description') or item.get('description') or f'从资产抽取节点确认生成：{item.get("label") or asset_key}').strip(),
            created_by=request.user,
            is_active=True,
        )
        asset.image_file.save(image_file.name, image_file, save=False)
        asset.save()

        self._ensure_project_asset_binding(project, asset)

        candidates = item.get('candidates') or []
        candidates.insert(0, {
            'asset_id': str(asset.id),
            'key': asset.key,
            'group': asset.group,
            'description': asset.description,
            'variable_type': asset.variable_type,
            'scope': asset.scope,
            'scope_display': asset.get_scope_display(),
        })
        item['candidates'] = candidates[:4]
        item['selected_asset_id'] = str(asset.id)
        item['selected_action'] = 'bind_existing'
        item['variable_type'] = 'image'
        item['match_status'] = 'matched'
        item['confirmed_image_asset'] = self._serialize_asset_choice(asset)
        self._persist_asset_item(stage, output_data, items, item_index, item)

        return Response({
            'message': '图片资产已创建并绑定',
            'asset': GlobalVariableListSerializer(asset, context=self.get_serializer_context()).data,
            'item': item,
            'stage': ProjectStageSerializer(stage).data,
            'bindings': self._serialize_asset_binding_results(project),
        })

    @action(detail=True, methods=['get'])
    def asset_extraction_assets(self, request, pk=None):
        queryset = self._get_accessible_assets(request.user)
        serializer = GlobalVariableListSerializer(queryset, many=True, context=self.get_serializer_context())
        return Response({'results': serializer.data, 'count': len(serializer.data)})

    @action(detail=True, methods=["get"])
    def task_status(self, request, pk=None):
        """
        获取Celery任务状态
        GET /api/v1/projects/{id}/task-status/?task_id=xxx

        返回:
        {
            "task_id": "xxx",
            "state": "PENDING|STARTED|SUCCESS|FAILURE|RETRY",
            "result": {...},
            "info": {...}
        }
        """
        project = self.get_object()
        task_id = request.query_params.get("task_id")

        if not task_id:
            return Response(
                {"error": "缺少task_id参数"}, status=status.HTTP_400_BAD_REQUEST
            )

        # 获取Celery任务结果
        task_result = AsyncResult(task_id)

        response_data = {
            "task_id": task_id,
            "state": task_result.state,
            "project_id": str(project.id),
        }

        if task_result.state == "PENDING":
            response_data["info"] = "任务等待执行"
        elif task_result.state == "STARTED":
            response_data["info"] = "任务正在执行"
        elif task_result.state == "SUCCESS":
            response_data["result"] = task_result.result
            response_data["info"] = "任务执行成功"
        elif task_result.state == "FAILURE":
            response_data["error"] = str(task_result.info)
            response_data["info"] = "任务执行失败"
        elif task_result.state == "RETRY":
            response_data["info"] = "任务正在重试"
        else:
            response_data["info"] = task_result.info

        return Response(response_data)

    @action(detail=True, methods=["post"])
    def retry_stage(self, request, pk=None):
        """
        重试失败的阶段
        POST /api/v1/projects/{id}/retry-stage/
        Body: {"stage_name": "rewrite"}
        """
        project = self.get_object()
        serializer = StageRetrySerializer(
            data=request.data, context={"project_id": str(project.id)}
        )
        serializer.is_valid(raise_exception=True)

        stage_name = serializer.validated_data["stage_name"]
        stage = get_object_or_404(ProjectStage, project=project, stage_type=stage_name)

        # 增加重试次数
        stage.retry_count += 1
        stage.status = "processing"
        stage.error_message = ""
        stage.started_at = timezone.now()
        stage.save()

        # TODO: 调用Celery任务
        # from apps.content.tasks import execute_project_stage
        # execute_project_stage.delay(str(project.id), stage_name)

        return Response(
            {
                "message": f"阶段 {stage.get_stage_type_display()} 开始重试 (第{stage.retry_count}次)",
                "stage": ProjectStageSerializer(stage).data,
            }
        )

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        """
        暂停项目
        POST /api/v1/projects/{id}/pause/
        """
        project = self.get_object()

        if project.status != "processing":
            return Response(
                {"error": "只有处理中的项目才能暂停"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        revoked_task_ids = self._revoke_project_tasks(project.id)

        project.stages.filter(status='processing').update(
            status='pending',
            completed_at=None,
            error_message=''
        )

        cancel_running_queue_task(project)

        project.status = "paused"
        project.save()

        return Response(
            {
                "message": "项目已暂停",
                "project": ProjectDetailSerializer(project).data,
                "revoked_task_ids": revoked_task_ids,
            }
        )

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        """
        恢复暂停的项目
        POST /api/v1/projects/{id}/resume/
        """
        project = self.get_object()

        if project.status != "paused":
            return Response(
                {"error": "只有暂停的项目才能恢复"}, status=status.HTTP_400_BAD_REQUEST
            )

        if not project.series_id:
            return self._start_pipeline_directly(project)

        queue_result = enqueue_episode_task(
            project=project,
            created_by=request.user,
            task_type='pipeline',
        )
        queue_task = queue_result['queue_task']
        channel = f"ai_story:project:{project.id}:pipeline"

        return Response(
            {
                "message": "项目已恢复并重新加入队列" if not queue_result['started'] else "项目已恢复",
                "project": ProjectDetailSerializer(project).data,
                "task_id": queue_task.celery_task_id or None,
                "queue_task_id": str(queue_task.id),
                "queue_status": queue_task.status,
                "queue_position": queue_result['queue_position'],
                "channel": channel,
                "project_id": str(project.id),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"])
    def rollback_stage(self, request, pk=None):
        """
        回滚到指定阶段
        POST /api/v1/projects/{id}/rollback-stage/
        Body: {"stage_name": "rewrite"}
        """
        project = self.get_object()
        stage_name = request.data.get("stage_name")

        if not stage_name:
            return Response(
                {"error": "缺少阶段名称"}, status=status.HTTP_400_BAD_REQUEST
            )

        stage = get_object_or_404(ProjectStage, project=project, stage_type=stage_name)

        # 重置该阶段及后续阶段
        stage_order = get_project_stage_order(get_stage_template_states(project))
        current_index = stage_order.index(stage_name)

        # 重置当前及后续阶段
        for stage_type in stage_order[current_index:]:
            ProjectStage.objects.filter(project=project, stage_type=stage_type).update(
                status="pending",
                output_data={},
                error_message="",
                retry_count=0,
                started_at=None,
                completed_at=None,
            )

        # 更新项目状态
        project.status = "draft"
        project.save()

        return Response(
            {
                "message": f"已回滚到阶段 {stage.get_stage_type_display()}",
                "project": ProjectDetailSerializer(project).data,
            }
        )

    @action(detail=True, methods=["get"])
    def model_config(self, request, pk=None):
        """
        获取项目模型配置
        GET /api/v1/projects/{id}/model-config/
        """
        project = self.get_object()
        config, created = ProjectModelConfig.objects.get_or_create(project=project)
        serializer = ProjectModelConfigSerializer(config)
        return Response(serializer.data)

    @action(detail=True, methods=["patch"])
    def update_model_config(self, request, pk=None):
        """
        更新项目模型配置
        PATCH /api/v1/projects/{id}/update-model-config/
        """
        project = self.get_object()
        config, created = ProjectModelConfig.objects.get_or_create(project=project)
        serializer = ProjectModelConfigSerializer(
            config, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def save_as_template(self, request, pk=None):
        """
        将项目保存为模板
        POST /api/v1/projects/{id}/save-as-template/
        Body: {"template_name": "我的模板", "include_model_config": true}
        """
        project = self.get_object()
        serializer = ProjectTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        template_name = serializer.validated_data["template_name"]
        include_model_config = serializer.validated_data["include_model_config"]

        # TODO: 实现模板保存逻辑
        # 1. 复制项目基本信息
        # 2. 复制提示词集配置
        # 3. 如果include_model_config=True,复制模型配置
        # 4. 保存为可复用模板

        return Response(
            {
                "message": f"项目已保存为模板: {template_name}",
                "template_name": template_name,
            }
        )

    @action(detail=True, methods=["post"])
    def export(self, request, pk=None):
        """
        导出项目(合成视频、生成字幕)
        POST /api/v1/projects/{id}/export/
        Body: {"include_subtitles": true, "video_format": "mp4"}
        """
        project = self.get_object()

        if project.status != "completed":
            return Response(
                {"error": "只有完成的项目才能导出"}, status=status.HTTP_400_BAD_REQUEST
            )

        include_subtitles = request.data.get("include_subtitles", True)
        video_format = request.data.get("video_format", "mp4")

        # TODO: 实现视频导出逻辑
        # 1. 获取所有生成的视频片段
        # 2. 按分镜顺序合成完整视频
        # 3. 如果include_subtitles=True,生成并嵌入字幕
        # 4. 返回下载链接

        return Response(
            {
                "message": "导出任务已创建",
                "export_id": "TODO",
                "status": "processing",
            }
        )

    @action(detail=False, methods=["get"])
    def statistics(self, request):
        """
        获取项目统计信息
        GET /api/v1/projects/statistics/
        """
        user = request.user
        projects = Project.objects.filter(user=user)

        stats = {
            "total_projects": projects.count(),
            "draft_projects": projects.filter(status="draft").count(),
            "processing_projects": projects.filter(status="processing").count(),
            "completed_projects": projects.filter(status="completed").count(),
            "failed_projects": projects.filter(status="failed").count(),
            "paused_projects": projects.filter(status="paused").count(),
        }

        return Response(stats)

    @action(detail=True, methods=["patch"])
    def update_stage_data(self, request, pk=None):
        """
        更新指定阶段的输入/输出数据
        PATCH /api/v1/projects/{id}/update-stage-data/
        Body: {
            "stage_name": "rewrite",
            "input_data": {...},
            "output_data": {...}
        }
        """
        project = self.get_object()
        stage_name = request.data.get("stage_name")

        if not stage_name:
            return Response(
                {"error": "缺少阶段名称"}, status=status.HTTP_400_BAD_REQUEST
            )

        # 获取阶段
        try:
            stage = ProjectStage.objects.get(project=project, stage_type=stage_name)
        except ProjectStage.DoesNotExist:
            return Response(
                {"error": f"阶段 {stage_name} 不存在"}, status=status.HTTP_404_NOT_FOUND
            )

        # 更新输入数据(如果提供)
        if "input_data" in request.data:
            stage.input_data = request.data["input_data"]

        # 更新输出数据(如果提供)
        if "output_data" in request.data:
            stage.output_data = request.data["output_data"]

        stage.save()

        return Response(
            {
                "message": f"阶段 {stage.get_stage_type_display()} 数据已更新",
                "stage": ProjectStageSerializer(stage).data,
            }
        )

    @action(detail=True, methods=["patch"])
    def update_rewrite(self, request, pk=None):
        """
        更新文案改写内容
        PATCH /api/v1/projects/projects/{id}/update_rewrite/
        Body: {
            "rewritten_text": "...",
            "original_text": "..."  # 可选
        }
        """
        from apps.content.models import ContentRewrite

        project = self.get_object()
        rewritten_text = request.data.get("rewritten_text", None)
        original_text = request.data.get("original_text", None)

        if rewritten_text is None:
            return Response(
                {"error": "缺少 rewritten_text"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        defaults = {
            "original_text": original_text if original_text is not None else project.original_topic,
            "rewritten_text": rewritten_text,
        }

        content_rewrite, _ = ContentRewrite.objects.update_or_create(
            project=project,
            defaults=defaults,
        )

        return Response(
            {
                "message": "文案改写已更新",
                "rewrite": {
                    "id": str(content_rewrite.id),
                    "original_text": content_rewrite.original_text,
                    "rewritten_text": content_rewrite.rewritten_text,
                    "updated_at": content_rewrite.updated_at.isoformat() if content_rewrite.updated_at else None,
                },
            }
        )

    @action(detail=True, methods=["patch"])
    def update_storyboard(self, request, pk=None):
        """
        更新分镜内容
        PATCH /api/v1/projects/projects/{id}/update_storyboard/
        Body: {
            "storyboard_id": "...",
            "scene_description": "...",
            "narration_text": "...",
            "image_prompt": "...",
            "duration_seconds": 3.0
        }
        """
        from apps.content.models import Storyboard

        project = self.get_object()
        storyboard_id = request.data.get("storyboard_id")

        if not storyboard_id:
            return Response(
                {"error": "缺少 storyboard_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        storyboard = get_object_or_404(
            Storyboard,
            id=storyboard_id,
            project=project,
        )

        allowed_fields = [
            "scene_description",
            "narration_text",
            "image_prompt",
            "duration_seconds",
        ]

        updates = {}
        for field in allowed_fields:
            if field in request.data:
                updates[field] = request.data[field]

        if not updates:
            return Response(
                {"error": "未提供可更新的字段"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for field, value in updates.items():
            setattr(storyboard, field, value)

        storyboard.save()

        return Response(
            {
                "message": "分镜已更新",
                "storyboard": {
                    "id": str(storyboard.id),
                    "sequence_number": storyboard.sequence_number,
                    "scene_description": storyboard.scene_description,
                    "narration_text": storyboard.narration_text,
                    "image_prompt": storyboard.image_prompt,
                    "duration_seconds": storyboard.duration_seconds,
                    "updated_at": storyboard.updated_at.isoformat() if storyboard.updated_at else None,
                },
            }
        )

    @action(detail=True, methods=["patch"])
    def update_camera_movement(self, request, pk=None):
        """
        更新运镜参数
        PATCH /api/v1/projects/projects/{id}/update_camera_movement/
        Body: {
            "camera_id": "...",
            "movement_type": "...",
            "movement_params": {...}
        }
        """
        from apps.content.models import CameraMovement

        project = self.get_object()
        camera_id = request.data.get("camera_id")

        if not camera_id:
            return Response(
                {"error": "缺少 camera_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        camera = get_object_or_404(
            CameraMovement,
            id=camera_id,
            storyboard__project=project,
        )

        allowed_fields = [
            "movement_type",
            "movement_params",
        ]

        updates = {}
        for field in allowed_fields:
            if field in request.data:
                updates[field] = request.data[field]

        if not updates:
            return Response(
                {"error": "未提供可更新的字段"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for field, value in updates.items():
            setattr(camera, field, value)

        camera.save()

        return Response(
            {
                "message": "运镜参数已更新",
                "camera_movement": {
                    "id": str(camera.id),
                    "movement_type": camera.movement_type,
                    "movement_params": camera.movement_params,
                    "updated_at": camera.updated_at.isoformat() if camera.updated_at else None,
                },
            }
        )

    @action(detail=True, methods=["post"])
    def retry_pipeline(self, request, pk=None):
        """
        重新发起当前分集的完整流程任务。
        POST /api/v1/projects/{id}/retry_pipeline/
        """
        project = self.get_object()

        if project.status == 'processing':
            return Response(
                {"error": "当前分集正在执行中，无需重试"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project.stages.exclude(status='completed').update(
            status='pending',
            error_message='',
            started_at=None,
            completed_at=None,
        )
        project.completed_at = None
        project.save(update_fields=['completed_at', 'updated_at'])

        return self.run_pipeline(request, pk=pk)

    @action(detail=True, methods=["post"])
    def force_release_queue(self, request, pk=None):
        """
        手动释放当前分集的队列任务。
        POST /api/v1/projects/{id}/force_release_queue/
        """
        project = self.get_object()
        reason = request.data.get('reason', '').strip()
        queue_task = force_release_queue_task(project, reason=reason)

        if not queue_task:
            return Response(
                {"error": "当前分集没有可释放的队列任务"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project.refresh_from_db()
        return Response(
            {
                "message": "队列任务已释放",
                "queue_task_id": str(queue_task.id),
                "queue_status": queue_task.status,
                "project": ProjectDetailSerializer(project).data,
            }
        )

    @action(detail=True, methods=["post"])
    def run_pipeline(self, request, pk=None):
        """
        运行完整工作流（同一作品下按分集串行排队）
        POST /api/v1/projects/{id}/run_pipeline/
        """
        project = self.get_object()

        direct_api_providers = list(self._pipeline_direct_api_providers(project))
        if direct_api_providers:
            return Response(
                {
                    'error': {
                        'code': 'PAID_PIPELINE_CONFIRMATION_REQUIRED',
                        'message': '完整流水线包含直接 API Provider，当前无法可靠聚合最大费用；请逐阶段确认执行。',
                        'providers': [
                            {'id': str(provider.pk), 'name': provider.name}
                            for provider in direct_api_providers
                        ],
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not project.series_id:
            return self._start_pipeline_directly(project)

        queue_result = enqueue_episode_task(
            project=project,
            created_by=request.user,
            task_type='pipeline',
        )
        queue_task = queue_result['queue_task']
        channel = f"ai_story:project:{project.id}:pipeline"

        response_status = status.HTTP_202_ACCEPTED
        if queue_result['started']:
            message = '工作流已启动'
        elif queue_result['already_exists']:
            message = '该分集已有待执行任务'
        else:
            message = '当前有分集正在执行，已进入等待队列'

        return Response(
            {
                'task_id': queue_task.celery_task_id or None,
                'queue_task_id': str(queue_task.id),
                'queue_status': queue_task.status,
                'queue_position': queue_result['queue_position'],
                'channel': channel,
                'message': message,
                'project_id': str(project.id),
            },
            status=response_status,
        )

    @action(detail=True, methods=["post"])
    def generate_jianying_draft(self, request, pk=None):
        """
        生成剪映草稿
        POST /api/v1/projects/{id}/generate_jianying_draft/
        Body: {
            "background_music": "/path/to/music.mp3",  // 可选
            "draft_folder_path": "/path/to/drafts",    // 可选
            "music_volume": 0.6,                       // 可选，默认0.6
            "add_intro_animation": true,               // 可选，默认true
            "subtitle_size": 15,                       // 可选，默认15
            "width": 1080,                             // 可选，默认1080
            "height": 1920                             // 可选，默认1920（竖屏）
        }

        返回:
        {
            "task_id": "celery-task-id",
            "channel": "ai_story:project:xxx:jianying_draft",
            "message": "剪映草稿生成任务已启动"
        }
        """
        from apps.projects.tasks import generate_jianying_draft

        project = self.get_object()

        from apps.content.models import GeneratedVideo

        has_generated_video = GeneratedVideo.objects.filter(
            storyboard__project=project,
            status="completed"
        ).exists()

        if not has_generated_video:
            return Response(
                {"error": "没有找到已生成的视频，无法生成剪映草稿"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 获取可选参数
        background_music = request.data.get("background_music")
        options = {
            "draft_folder_path": request.data.get("draft_folder_path"),
            "music_volume": request.data.get("music_volume", 0.6),
            "add_intro_animation": request.data.get("add_intro_animation", True),
            "subtitle_size": request.data.get("subtitle_size", 15),
            "width": request.data.get("width", 1080),
            "height": request.data.get("height", 1920),
        }

        # 过滤掉None值
        options = {k: v for k, v in options.items() if v is not None}

        # 启动Celery任务
        task = generate_jianying_draft.delay(
            project_id=str(project.id),
            user_id=self.request.user.id,
            background_music=background_music,
            **options
        )
        self._register_project_task(project.id, task.id)

        # 构建Redis频道名称
        channel = f"ai_story:project:{project.id}:jianying_draft"

        return Response(
            {
                "task_id": task.id,
                "channel": channel,
                "draft_path": task.result.get("draft_path", "todo"),
            },
            status=status.HTTP_200_OK,
        )


class ProjectStageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    项目阶段ViewSet

    只读API,用于查询阶段信息
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ProjectStageSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["stage_type", "status", "project"]
    ordering = ["created_at"]

    def get_queryset(self):
        """只返回当前用户项目的阶段"""
        return ProjectStage.objects.filter(
            project__user=self.request.user
        ).select_related("project")


class ProjectModelConfigViewSet(viewsets.ModelViewSet):
    """
    项目模型配置ViewSet
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ProjectModelConfigSerializer

    def get_queryset(self):
        """只返回当前用户项目的配置"""
        return (
            ProjectModelConfig.objects.filter(project__user=self.request.user)
            .select_related("project")
            .prefetch_related(
                "rewrite_providers",
                "storyboard_providers",
                "image_providers",
                "camera_providers",
                "video_providers",
            )
        )
