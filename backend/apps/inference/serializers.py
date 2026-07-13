"""混合推理控制面的 REST 序列化器。"""

import hashlib

from django.db import transaction
from rest_framework import serializers

from core.url_security import validate_service_url
from .services.security import mask_sensitive_data

from .models import (
    AIBudgetPolicy,
    BudgetReservation,
    GenerationProfile,
    GenerationRoute,
    GenerationTarget,
    GenerationWorkItem,
    MediaArtifact,
    ProjectAISettings,
    ProviderPriceRate,
    RuntimeNode,
)


def _secret_tail(value):
    """仅返回凭据尾部摘要；任何读取接口都不能回显完整 Token。"""

    if not value:
        return ''
    return f'***{value[-4:]}' if len(value) > 4 else '***'


class RuntimeNodeSerializer(serializers.ModelSerializer):
    access_token = serializers.CharField(
        write_only=True, required=False, allow_blank=True, trim_whitespace=False
    )
    has_access_token = serializers.SerializerMethodField()
    access_token_masked = serializers.SerializerMethodField()
    available_slots = serializers.SerializerMethodField()

    class Meta:
        model = RuntimeNode
        fields = [
            'id', 'name', 'node_type', 'agent_url', 'access_token',
            'has_access_token', 'access_token_masked', 'agent_version',
            'capabilities', 'hardware_snapshot', 'resource_groups', 'slot_count',
            'reserved_slot_count', 'available_slots', 'slot_config', 'is_local',
            'health_status', 'concurrency_limit', 'priority', 'is_active',
            'metadata', 'last_seen_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'agent_version', 'capabilities', 'hardware_snapshot',
            'resource_groups', 'health_status', 'last_seen_at',
            'created_at', 'updated_at',
        ]

    def get_has_access_token(self, obj):
        return bool(obj.access_token)

    def get_access_token_masked(self, obj):
        return _secret_tail(obj.access_token)

    def get_available_slots(self, obj):
        return max(0, obj.slot_count - obj.reserved_slot_count)

    def update(self, instance, validated_data):
        # 管理页不会拿到旧 Token；空值代表保留，而不是意外清空。
        if validated_data.get('access_token') == '':
            validated_data.pop('access_token')
        return super().update(instance, validated_data)

    def validate(self, attrs):
        active = attrs.get('is_active', getattr(self.instance, 'is_active', True))
        agent_url = attrs.get('agent_url', getattr(self.instance, 'agent_url', ''))
        is_local = attrs.get('is_local', getattr(self.instance, 'is_local', True))
        token = attrs.get('access_token')
        if agent_url:
            try:
                # Agent 请求会携带节点 Token；除显式回环外一律要求 TLS。
                attrs['agent_url'] = validate_service_url(
                    agent_url,
                    require_https_for_non_loopback=True,
                    field_label='Agent URL',
                )
                agent_url = attrs['agent_url']
            except ValueError as error:
                raise serializers.ValidationError({'agent_url': str(error)})
        if active and not agent_url:
            raise serializers.ValidationError({'agent_url': '启用的节点必须配置 Agent URL。'})
        if active and not is_local and not (token or getattr(self.instance, 'access_token', '')):
            raise serializers.ValidationError({'access_token': '远程节点必须配置访问令牌。'})
        return attrs


class GenerationProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationProfile
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class GenerationTargetSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source='provider.name', read_only=True)
    runtime_node_name = serializers.CharField(source='runtime_node.name', read_only=True)

    class Meta:
        model = GenerationTarget
        fields = [
            'id', 'route', 'name', 'role', 'position', 'provider', 'provider_name',
            'runtime_node', 'runtime_node_name', 'profile', 'priority', 'weight',
            'parameter_overrides', 'max_concurrency', 'timeout_seconds',
            'max_attempts', 'cooldown_until', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'route', 'created_at', 'updated_at']

    def validate(self, attrs):
        if not attrs.get('provider') and not attrs.get('runtime_node'):
            raise serializers.ValidationError('路由目标至少需要 Provider 或 RuntimeNode。')
        role = attrs.get('role', 'local_primary')
        provider = attrs.get('provider')
        if role == 'paid_fallback' and (
            provider is None or getattr(provider, 'deployment_mode', 'api') != 'api'
        ):
            raise serializers.ValidationError({'provider': '付费回退目标必须绑定 API Provider。'})
        if role != 'paid_fallback' and provider is not None and provider.deployment_mode == 'api':
            raise serializers.ValidationError({'role': 'API Provider 必须明确标记为 paid_fallback。'})
        return attrs


class GenerationRouteSerializer(serializers.ModelSerializer):
    targets = GenerationTargetSerializer(many=True, required=False)
    project_name = serializers.CharField(source='project.name', read_only=True)

    class Meta:
        model = GenerationRoute
        fields = [
            'id', 'name', 'capability', 'scope', 'project', 'project_name',
            'stage_type', 'profile_code', 'profile', 'priority', 'match_rules',
            'local_first', 'fallback_error_classes', 'is_active', 'targets',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        scope = attrs.get('scope', getattr(self.instance, 'scope', 'global'))
        project = attrs.get('project', getattr(self.instance, 'project', None))
        if scope == 'project' and not project:
            raise serializers.ValidationError({'project': '项目路由必须绑定项目。'})
        if scope == 'global' and project:
            raise serializers.ValidationError({'project': '全局路由不能绑定项目。'})
        request = self.context.get('request')
        if scope == 'global' and request and not request.user.is_staff:
            raise serializers.ValidationError({'scope': '只有管理员可以管理全局路由。'})
        if project and request and project.user_id != request.user.id:
            raise serializers.ValidationError({'project': '不能管理其他用户的项目路由。'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        targets = validated_data.pop('targets', [])
        route = super().create(validated_data)
        self._replace_targets(route, targets)
        return route

    @transaction.atomic
    def update(self, instance, validated_data):
        targets = validated_data.pop('targets', None)
        route = super().update(instance, validated_data)
        if targets is not None:
            # 配置型目标采用整体替换，保证 position 的快照是一个原子版本。
            route.targets.all().delete()
            self._replace_targets(route, targets)
        return route

    @staticmethod
    def _replace_targets(route, targets):
        for position, target_data in enumerate(targets):
            target_data['position'] = target_data.get('position', position)
            GenerationTarget.objects.create(route=route, **target_data)


class ProjectAISettingsSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source='project.name', read_only=True)
    cloud_authorized_by_name = serializers.CharField(
        source='cloud_authorized_by.username', read_only=True
    )

    class Meta:
        model = ProjectAISettings
        fields = [
            'id', 'project', 'project_name', 'default_profile', 'default_route',
            'budget_policy', 'capability_routes', 'parameter_overrides',
            'default_profile_code', 'prefer_local', 'allow_paid_fallback',
            'allow_cloud_data_transfer', 'cloud_authorized_by',
            'cloud_authorized_by_name', 'cloud_authorized_at', 'project_budget_cny',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'project', 'cloud_authorized_by', 'cloud_authorized_at',
            'created_at', 'updated_at',
        ]

    def validate(self, attrs):
        cloud_allowed = attrs.get(
            'allow_cloud_data_transfer',
            getattr(self.instance, 'allow_cloud_data_transfer', False),
        )
        paid_fallback = attrs.get('allow_paid_fallback')
        if paid_fallback is True and not cloud_allowed:
            raise serializers.ValidationError({
                'allow_paid_fallback': '启用自动付费回退前必须显式授权数据出站。'
            })
        return attrs


class ProviderPriceRateSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source='provider.name', read_only=True)
    runtime_node_name = serializers.CharField(source='runtime_node.name', read_only=True)

    class Meta:
        model = ProviderPriceRate
        fields = [
            'id', 'provider', 'provider_name', 'runtime_node', 'runtime_node_name',
            'capability', 'model_pattern', 'billing_unit', 'unit_size', 'unit_price',
            'minimum_charge', 'currency', 'exchange_rate_to_cny', 'conditions',
            'version', 'priority', 'effective_from', 'effective_to', 'source_note',
            'is_active', 'metadata', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        provider = attrs.get('provider', getattr(self.instance, 'provider', None))
        node = attrs.get('runtime_node', getattr(self.instance, 'runtime_node', None))
        if not provider and not node:
            raise serializers.ValidationError('价目表必须绑定 Provider 或 RuntimeNode。')
        if attrs.get('effective_to') and attrs.get('effective_from'):
            if attrs['effective_to'] <= attrs['effective_from']:
                raise serializers.ValidationError({'effective_to': '失效时间必须晚于生效时间。'})
        return attrs


class AIBudgetPolicySerializer(serializers.ModelSerializer):
    remaining_amount = serializers.DecimalField(
        max_digits=18, decimal_places=6, read_only=True, allow_null=True
    )

    class Meta:
        model = AIBudgetPolicy
        fields = '__all__'
        read_only_fields = ['id', 'spent_amount', 'reserved_amount', 'created_at', 'updated_at']


class BudgetReservationSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source='project.name', read_only=True)
    details = serializers.SerializerMethodField()

    class Meta:
        model = BudgetReservation
        fields = [
            'id', 'policy', 'project', 'project_name', 'work_item', 'idempotency_key',
            'status', 'estimated_amount', 'reserved_amount', 'settled_amount',
            'currency', 'expires_at', 'settled_at', 'released_at', 'details',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_details(self, obj):
        """人工复核备注也可能来自厂商异常，读取时再做一次纵深脱敏。"""

        return mask_sensitive_data(obj.details or {})


class MediaArtifactSerializer(serializers.ModelSerializer):
    metadata = serializers.SerializerMethodField()

    class Meta:
        model = MediaArtifact
        fields = [
            'id', 'work_item', 'project', 'kind', 'status', 'lifecycle', 'uri',
            'storage_backend', 'mime_type', 'sha256', 'file_size', 'width', 'height',
            'duration_seconds', 'fps', 'segment_index', 'segment_start', 'segment_end',
            'metadata', 'expires_at', 'protected', 'quarantined', 'deleted_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_metadata(self, obj):
        return mask_sensitive_data(obj.metadata or {})


class GenerationWorkItemSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source='provider.name', read_only=True)
    runtime_node_name = serializers.CharField(source='runtime_node.name', read_only=True)
    artifacts = MediaArtifactSerializer(many=True, read_only=True)
    request_summary = serializers.SerializerMethodField()
    effective_parameters = serializers.SerializerMethodField()
    depends_on = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    error_message = serializers.SerializerMethodField()
    projection_error = serializers.SerializerMethodField()

    class Meta:
        model = GenerationWorkItem
        fields = [
            'id', 'project', 'stage_execution_id', 'capability', 'stage_type',
            'storyboard_id', 'tile_index',
            'segment_index', 'profile', 'route', 'target', 'provider', 'provider_name',
            'runtime_node', 'runtime_node_name', 'idempotency_key', 'status', 'priority',
            'request_summary', 'effective_parameters', 'usage', 'estimated_cost',
            'actual_cost', 'currency', 'provider_request_id', 'agent_job_id',
            'route_snapshot', 'attempt_count', 'max_attempts', 'version', 'error_class',
            'error_code', 'error_message', 'scheduled_at', 'claimed_at',
            'lease_expires_at', 'heartbeat_at', 'next_retry_at', 'started_at',
            'completed_at', 'depends_on', 'projection_status', 'projection_error',
            'projected_at', 'artifacts', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_request_summary(self, obj):
        parameters = obj.request_parameters or {}
        prompt = str(parameters.get('prompt', ''))
        return {
            'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest() if prompt else '',
            'prompt_length': len(prompt),
            'input_artifact_count': len(parameters.get('input_artifacts', []) or []),
            'parameter_keys': sorted(
                key for key in parameters.keys()
                if key not in {'prompt', 'negative_prompt', 'input_artifacts'}
            ),
        }

    def get_effective_parameters(self, obj):
        """展示可复现参数，但不回显完整提示词、输入媒体或内部幂等键。"""

        parameters = obj.effective_parameters or {}
        hidden = {
            'prompt', 'negative_prompt', 'input_artifacts', 'idempotency_key',
            'project_id', 'work_item_id',
        }
        return {
            key: mask_sensitive_data(value, key)
            for key, value in parameters.items()
            if key not in hidden
        }

    def get_error_message(self, obj):
        return mask_sensitive_data(obj.error_message or '')

    def get_projection_error(self, obj):
        return mask_sensitive_data(obj.projection_error or '')
