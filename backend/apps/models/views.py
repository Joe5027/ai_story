"""
模型管理视图集
职责: 处理HTTP请求和业务逻辑编排
遵循单一职责原则(SRP)
"""

import csv

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser, IsAuthenticated, SAFE_METHODS
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from asgiref.sync import async_to_sync

from .models import ModelProvider, ModelUsageLog
from .serializers import (
    ModelProviderListSerializer,
    ModelProviderDetailSerializer,
    ModelProviderCreateSerializer,
    ModelProviderUpdateSerializer,
    ModelUsageLogSerializer,
    ModelProviderTestSerializer,
    ModelProviderSimpleSerializer,
    VendorModelDiscoverySerializer,
    VendorModelBatchCreateSerializer,
    VendorConnectionConfigSerializer,
    VendorConnectionConfigQuerySerializer,
)
from .services import ModelProviderService, ModelUsageLogService


class ModelProviderViewSet(viewsets.ModelViewSet):
    """
    模型提供商ViewSet

    提供模型提供商的CRUD操作和测试功能
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        'provider_type', 'deployment_mode', 'health_status', 'runtime_node', 'is_active'
    ]
    search_fields = ['name', 'model_name', 'api_url']
    ordering_fields = ['created_at', 'updated_at', 'priority', 'name']
    ordering = ['-created_at']

    def get_permissions(self):
        """读取面向已登录用户，任何写入或带凭据探测仅允许管理员。"""

        permission_class = (
            IsAuthenticated if self.request.method in SAFE_METHODS else IsAdminUser
        )
        return [permission_class()]

    def get_queryset(self):
        """获取所有模型提供商"""
        return ModelProvider.objects.all().select_related('runtime_node')

    def get_serializer_class(self):
        """根据动作选择序列化器"""
        if self.action == 'list':
            return ModelProviderListSerializer
        elif self.action == 'retrieve':
            return ModelProviderDetailSerializer
        elif self.action == 'create':
            return ModelProviderCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return ModelProviderUpdateSerializer
        return ModelProviderDetailSerializer

    def create(self, request, *args, **kwargs):
        """创建模型提供商"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 使用服务层创建
        provider = ModelProviderService.create_provider(serializer.validated_data)

        # 返回详情
        response_serializer = ModelProviderDetailSerializer(
            provider, context=self.get_serializer_context()
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """更新模型提供商"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        # 使用服务层更新
        provider = ModelProviderService.update_provider(
            str(instance.id),
            serializer.validated_data
        )

        # 返回详情
        response_serializer = ModelProviderDetailSerializer(
            provider, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)

    def destroy(self, request, *args, **kwargs):
        """删除模型提供商"""
        instance = self.get_object()

        # 检查是否被项目使用
        from apps.projects.models import ProjectModelConfig

        # 检查所有关联字段
        in_use = (
            ProjectModelConfig.objects.filter(rewrite_providers=instance).exists() or
            ProjectModelConfig.objects.filter(storyboard_providers=instance).exists() or
            ProjectModelConfig.objects.filter(image_providers=instance).exists() or
            ProjectModelConfig.objects.filter(camera_providers=instance).exists() or
            ProjectModelConfig.objects.filter(video_providers=instance).exists()
        )

        if in_use:
            return Response(
                {'error': '该模型提供商正在被项目使用,无法删除'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 使用服务层删除
        success = ModelProviderService.delete_provider(str(instance.id))

        if success:
            return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            return Response(
                {'error': '删除失败'},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def toggle_status(self, request, pk=None):
        """
        切换模型提供商激活状态
        POST /api/v1/models/providers/{id}/toggle_status/
        """
        instance = self.get_object()
        provider = ModelProviderService.toggle_provider_status(str(instance.id))

        return Response({
            'message': f'模型提供商已{"激活" if provider.is_active else "停用"}',
            'is_active': provider.is_active,
            'provider': ModelProviderDetailSerializer(
                provider, context=self.get_serializer_context()
            ).data
        })

    @action(detail=True, methods=['get'])
    def statistics(self, request, pk=None):
        """
        获取模型提供商统计信息
        GET /api/v1/models/providers/{id}/statistics/
        """
        instance = self.get_object()
        stats = ModelProviderService.get_provider_statistics(
            str(instance.id), request.user
        )

        return Response(stats)

    @action(detail=True, methods=['post'])
    def test_connection(self, request, pk=None):
        """
        测试模型提供商连接
        POST /api/v1/models/providers/{id}/test_connection/
        Body: {"test_prompt": "Hello, this is a test."}
        """
        instance = self.get_object()
        serializer = ModelProviderTestSerializer(
            data=request.data,
            context={'provider_id': str(instance.id)}
        )
        serializer.is_valid(raise_exception=True)

        if not serializer.validated_data.get('billable_smoke', False):
            result = ModelProviderService.check_provider_health(str(instance.id))
            response_status = status.HTTP_200_OK if result.get('success') else status.HTTP_400_BAD_REQUEST
            return Response({
                **result,
                'message': '健康检查成功' if result.get('success') else '健康检查失败',
                'generated_content': False,
            }, status=response_status)

        try:
            authorization = ModelProviderService.authorize_billable_smoke(
                instance,
                serializer.validated_data['confirmed_max_cost_cny'],
            )
        except ValueError as error:
            code, _, message = str(error).partition(':')
            return Response({
                'success': False,
                'error': {'code': code, 'message': message.strip() or str(error)},
            }, status=status.HTTP_400_BAD_REQUEST)

        test_prompt = serializer.validated_data.get(
            'test_prompt',
            'Hello, this is a test.'
        )
        test_image_url = serializer.validated_data.get('test_image_url', '')
        test_image_base64 = serializer.validated_data.get('test_image_base64', '')
        test_image_mime_type = serializer.validated_data.get('test_image_mime_type', 'image/jpeg')

        # 异步测试转同步执行
        result = async_to_sync(ModelProviderService.test_provider_connection)(
            str(instance.id),
            test_prompt,
            test_image_url=test_image_url,
            test_image_base64=test_image_base64,
            test_image_mime_type=test_image_mime_type,
            usage_log_id=authorization['usage_log_id'],
            stage_type='billable_smoke',
        )

        if result.get('success'):
            ModelUsageLog.objects.filter(pk=authorization['usage_log_id']).update(
                status='success',
                estimated_cost=authorization['estimate'],
                settled_cost=authorization['estimate'],
                currency=authorization['currency'],
                deployment_mode='api',
            )
        else:
            # 无法确认上游是否已经计费时保留占用，避免无人值守任务自动重提。
            ModelUsageLog.objects.filter(pk=authorization['usage_log_id']).update(
                status='manual_review',
                error_code='UPSTREAM_BILLING_AMBIGUOUS',
            )

        if result['success']:
            return Response({
                'success': True,
                'message': '连接测试成功',
                'latency_ms': result['latency_ms'],
                'response': result.get('response'),
                'data': result.get('data', {}),
                'estimated_cost_cny': authorization['estimate'],
                'generated_content': True,
            })
        else:
            return Response({
                'success': False,
                'message': '连接测试失败',
                'error': result.get('error'),
                'latency_ms': result.get('latency_ms', 0)
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def usage_logs(self, request, pk=None):
        """
        获取模型提供商的使用日志
        GET /api/v1/models/providers/{id}/usage_logs/
        """
        instance = self.get_object()
        limit = int(request.query_params.get('limit', 100))

        logs = ModelUsageLogService.get_logs_by_provider(
            str(instance.id),
            request.user,
            limit=limit
        )

        serializer = ModelUsageLogSerializer(logs, many=True)
        return Response({
            'count': len(logs),
            'results': serializer.data
        })

    @action(detail=False, methods=['get'])
    def active_providers(self, request):
        """
        获取所有激活的模型提供商
        GET /api/v1/models/providers/active_providers/
        Query: ?provider_type=llm
        """
        provider_type = request.query_params.get('provider_type')
        providers = ModelProviderService.get_active_providers(provider_type)

        serializer = ModelProviderListSerializer(
            providers, many=True, context=self.get_serializer_context()
        )
        return Response({
            'count': len(providers),
            'results': serializer.data
        })

    @action(detail=False, methods=['get'])
    def by_type(self, request):
        """
        按类型分组获取模型提供商
        GET /api/v1/models/providers/by_type/
        """
        llm_providers = ModelProvider.objects.filter(
            provider_type='llm',
            is_active=True
        ).order_by('-priority')

        text2image_providers = ModelProvider.objects.filter(
            provider_type='text2image',
            is_active=True
        ).order_by('-priority')

        image2video_providers = ModelProvider.objects.filter(
            provider_type='image2video',
            is_active=True
        ).order_by('-priority')

        image_edit_providers = ModelProvider.objects.filter(
            provider_type='image_edit',
            is_active=True
        ).order_by('-priority')

        return Response({
            'llm': ModelProviderListSerializer(
                llm_providers, many=True, context=self.get_serializer_context()
            ).data,
            'text2image': ModelProviderListSerializer(
                text2image_providers, many=True, context=self.get_serializer_context()
            ).data,
            'image2video': ModelProviderListSerializer(
                image2video_providers, many=True, context=self.get_serializer_context()
            ).data,
            'image_edit': ModelProviderListSerializer(
                image_edit_providers, many=True, context=self.get_serializer_context()
            ).data,
        })


    @action(detail=False, methods=['get'])
    def simple_list(self, request):
        """
        获取简化的模型列表(仅id和name) - 用于下拉选择
        GET /api/v1/models/providers/simple_list/
        Query: ?provider_type=llm
        """
        provider_type = request.query_params.get('provider_type')

        queryset = ModelProvider.objects.filter(is_active=True)

        if provider_type:
            queryset = queryset.filter(provider_type=provider_type)

        queryset = queryset.order_by('-priority', 'name')

        serializer = ModelProviderSimpleSerializer(queryset, many=True)
        return Response({
            'count': queryset.count(),
            'results': serializer.data
        })

    @action(detail=False, methods=['get'])
    def executor_choices(self, request):
        """
        获取执行器选项列表
        GET /api/v1/models/providers/executor_choices/
        Query: ?provider_type=llm

        返回格式:
        {
            "llm": [
                {"value": "core.ai_client.openai_client.OpenAIClient", "label": "OpenAI兼容客户端"}
            ],
            "text2image": [...],
            "image2video": [...]
        }
        """
        provider_type = request.query_params.get('provider_type')

        # 如果指定了provider_type，只返回该类型的执行器
        if provider_type:
            temp_instance = ModelProvider(provider_type=provider_type)
            executor_choices = temp_instance.get_executor_choices()

            return Response({
                'provider_type': provider_type,
                'executors': [
                    {'value': choice[0], 'label': choice[1]}
                    for choice in executor_choices
                ]
            })

        # 否则返回所有类型的执行器
        all_executors = {}

        for ptype, _ in ModelProvider.PROVIDER_TYPES:
            temp_instance = ModelProvider(provider_type=ptype)
            executor_choices = temp_instance.get_executor_choices()

            all_executors[ptype] = [
                {'value': choice[0], 'label': choice[1]}
                for choice in executor_choices
            ]

        return Response(all_executors)

    @action(detail=False, methods=['get'])
    def opencode_config_status(self, request):
        """获取 opencode 配置同步状态。"""
        return Response(ModelProviderService.get_opencode_config_status())

    @action(detail=False, methods=['post'])
    def sync_opencode_config(self, request):
        """手动同步模型管理到 opencode 配置文件。"""
        result = ModelProviderService.sync_opencode_config()
        return Response(result)

    @action(detail=False, methods=['get'])
    def builtin_vendors(self, request):
        """获取内置厂商目录。"""
        vendors = ModelProviderService.list_builtin_vendors()
        return Response({
            'count': len(vendors),
            'results': vendors,
        })

    @action(detail=False, methods=['get', 'put'])
    def vendor_connection_config(self, request):
        """获取或保存当前用户的厂商导入连接配置。"""
        if request.method.lower() == 'get':
            serializer = VendorConnectionConfigQuerySerializer(data=request.query_params)
            serializer.is_valid(raise_exception=True)
            config = ModelProviderService.get_vendor_connection_config(
                user=request.user,
                vendor=serializer.validated_data['vendor'],
                capability=serializer.validated_data['capability'],
            )
            if not config:
                return Response({
                    'vendor': serializer.validated_data['vendor'],
                    'capability': serializer.validated_data['capability'],
                    'has_api_key': False,
                    'api_key_masked': '',
                    'api_url': '',
                })
            return Response(VendorConnectionConfigSerializer(config).data)

        serializer = VendorConnectionConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        config = ModelProviderService.save_vendor_connection_config(
            user=request.user,
            vendor=serializer.validated_data['vendor'],
            capability=serializer.validated_data['capability'],
            api_key=serializer.validated_data.get('api_key', ''),
            api_url=serializer.validated_data.get('api_url', ''),
        )
        return Response(VendorConnectionConfigSerializer(config).data)

    @action(detail=False, methods=['post'])
    def discover_vendor_models(self, request):
        """根据厂商和 API Key 拉取模型列表。"""
        serializer = VendorModelDiscoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = ModelProviderService.discover_vendor_models(
                vendor=serializer.validated_data['vendor'],
                capability=serializer.validated_data['capability'],
                api_key=serializer.validated_data['api_key'],
                api_url=serializer.validated_data.get('api_url'),
            )
        except ValueError as error:
            return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as error:
            return Response({'error': f'获取厂商模型失败: {error}'}, status=status.HTTP_400_BAD_REQUEST)

        ModelProviderService.save_vendor_connection_config(
            user=request.user,
            vendor=serializer.validated_data['vendor'],
            capability=serializer.validated_data['capability'],
            api_key=serializer.validated_data['api_key'],
            api_url=result.get('api_url', serializer.validated_data.get('api_url', '')),
        )

        return Response(result)

    @action(detail=False, methods=['post'])
    def batch_create_vendor_models(self, request):
        """批量创建厂商模型。"""
        serializer = VendorModelBatchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = ModelProviderService.batch_create_vendor_models(serializer.validated_data)

        ModelProviderService.save_vendor_connection_config(
            user=request.user,
            vendor=serializer.validated_data['vendor'],
            capability=serializer.validated_data['capability'],
            api_key=serializer.validated_data['api_key'],
            api_url=result.get('api_url', serializer.validated_data.get('api_url', '')),
        )

        return Response({
            'vendor': result['vendor'],
            'vendor_label': result['vendor_label'],
            'capability': result['capability'],
            'provider_type': result['provider_type'],
            'created_count': result['created_count'],
            'skipped_count': result['skipped_count'],
            'created': ModelProviderDetailSerializer(
                result['created'], many=True, context=self.get_serializer_context()
            ).data,
            'skipped': result['skipped'],
        }, status=status.HTTP_201_CREATED)


class ModelUsageLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    模型使用日志ViewSet

    只读API,用于查询使用日志
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ModelUsageLogSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = [
        'model_provider', 'status', 'project_id', 'stage_type', 'deployment_mode',
        'runtime_node', 'error_code', 'fallback_from',
    ]
    ordering = ['-created_at']

    def get_queryset(self):
        """普通用户仅可见自己的项目账本；staff 才能审计全局与无项目日志。"""
        return ModelUsageLogService.visible_to(self.request.user).select_related(
            'model_provider', 'runtime_node', 'price_rate', 'fallback_from', 'work_item'
        )

    @action(detail=False, methods=['get'], url_path='export_csv')
    def export_csv(self, request):
        """导出脱敏调用账本；不包含 Key、完整提示词或媒体地址。"""

        queryset = self.filter_queryset(self.get_queryset()).order_by('-created_at')
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="ai-usage-ledger.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow([
            'created_at', 'project_id', 'stage_type', 'provider', 'model',
            'deployment_mode', 'runtime_node', 'status', 'attempt_number',
            'input_tokens', 'output_tokens', 'image_count', 'video_seconds',
            'estimated_cost', 'settled_cost', 'currency', 'price_version',
            'fallback_from', 'fallback_reason', 'error_code', 'latency_ms',
            'work_item_id',
        ])
        for item in queryset.iterator():
            writer.writerow([
                item.created_at.isoformat() if item.created_at else '',
                item.project_id or '',
                item.stage_type,
                item.model_provider.name,
                item.model_provider.model_name,
                item.deployment_mode,
                item.runtime_node.name if item.runtime_node else '',
                item.status,
                item.attempt_number,
                item.input_tokens,
                item.output_tokens,
                item.image_count,
                item.video_seconds,
                item.estimated_cost,
                item.settled_cost,
                item.currency,
                item.price_rate.version if item.price_rate else '',
                item.fallback_from.name if item.fallback_from else '',
                item.fallback_reason,
                item.error_code,
                item.latency_ms,
                item.work_item_id or '',
            ])
        return response

    @action(detail=False, methods=['get'])
    def by_project(self, request):
        """
        按项目获取使用日志
        GET /api/v1/models/usage-logs/by_project/
        Query: ?project_id=xxx&stage_type=rewrite
        """
        project_id = request.query_params.get('project_id')
        if not project_id:
            return Response(
                {'error': '缺少project_id参数'},
                status=status.HTTP_400_BAD_REQUEST
            )

        stage_type = request.query_params.get('stage_type')
        logs = ModelUsageLogService.get_logs_by_project(
            project_id, request.user, stage_type
        )

        serializer = self.get_serializer(logs, many=True)
        return Response({
            'count': len(logs),
            'results': serializer.data
        })

    @action(detail=False, methods=['get'])
    def failed_logs(self, request):
        """
        获取失败的使用日志
        GET /api/v1/models/usage-logs/failed_logs/
        """
        limit = int(request.query_params.get('limit', 100))
        logs = ModelUsageLogService.get_failed_logs(request.user, limit=limit)

        serializer = self.get_serializer(logs, many=True)
        return Response({
            'count': len(logs),
            'results': serializer.data
        })
