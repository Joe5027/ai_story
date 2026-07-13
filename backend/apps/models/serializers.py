"""
模型管理序列化器
职责: 数据序列化与验证
遵循单一职责原则(SRP)
"""

import hashlib

from rest_framework import serializers
from .models import ModelProvider, ModelUsageLog, VendorConnectionConfig
from .vendor_catalog import VENDOR_CATALOG
from apps.inference.services.security import mask_sensitive_data
from core.url_security import validate_service_url


def mask_secret(value):
    """只显示末四位，避免 serializer、CSV 或浏览器状态持有完整密钥。"""
    value = str(value or '')
    if not value:
        return ''
    return f'****{value[-4:]}'


def summarize_ledger_payload(value, key=''):
    """账本只保留结构和摘要，不回传完整提示词、媒体 URL 或 base64。"""
    normalized = str(key).lower()
    content_tokens = ('prompt', 'content', 'raw_text', 'base64', 'image_url', 'video_url')
    if isinstance(value, dict):
        return {item_key: summarize_ledger_payload(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [summarize_ledger_payload(item, key) for item in value]
    if isinstance(value, str) and any(token in normalized for token in content_tokens):
        return {
            'omitted': True,
            'length': len(value),
            'sha256': hashlib.sha256(value.encode('utf-8')).hexdigest(),
        }
    return value


def visible_provider_usage_logs(serializer, provider):
    """返回当前请求可见的 Provider 账本；缺少请求上下文时默认拒绝。"""

    request = serializer.context.get('request')
    if not request:
        return ModelUsageLog.objects.none()
    # 延迟导入避免 serializers/services 的模块加载环；服务层是唯一隔离规则源。
    from .services import ModelUsageLogService
    return ModelUsageLogService.visible_to(request.user).filter(model_provider=provider)


class ModelProviderListSerializer(serializers.ModelSerializer):
    """模型提供商列表序列化器 - 轻量级"""

    provider_type_display = serializers.CharField(
        source='get_provider_type_display',
        read_only=True
    )

    # 统计信息
    total_usage_count = serializers.SerializerMethodField()
    recent_usage_count = serializers.SerializerMethodField()
    runtime_node_name = serializers.CharField(source='runtime_node.name', read_only=True, default='')

    class Meta:
        model = ModelProvider
        fields = [
            'id', 'name', 'provider_type', 'provider_type_display',
            'model_name', 'executor_class', 'deployment_mode', 'health_status',
            'runtime_node', 'runtime_node_name', 'runtime_model_id',
            'is_active', 'priority',
            'total_usage_count', 'recent_usage_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_total_usage_count(self, obj):
        """获取总使用次数"""
        return visible_provider_usage_logs(self, obj).count()

    def get_recent_usage_count(self, obj):
        """获取最近7天使用次数"""
        from django.utils import timezone
        from datetime import timedelta
        seven_days_ago = timezone.now() - timedelta(days=7)
        return visible_provider_usage_logs(self, obj).filter(
            created_at__gte=seven_days_ago
        ).count()


class ModelProviderDetailSerializer(serializers.ModelSerializer):
    """模型提供商详情序列化器 - 完整信息"""

    provider_type_display = serializers.CharField(
        source='get_provider_type_display',
        read_only=True
    )

    # 统计信息
    total_usage_count = serializers.SerializerMethodField()
    success_count = serializers.SerializerMethodField()
    failed_count = serializers.SerializerMethodField()
    success_rate = serializers.SerializerMethodField()
    avg_latency_ms = serializers.SerializerMethodField()
    total_tokens_used = serializers.SerializerMethodField()
    has_api_key = serializers.SerializerMethodField()
    api_key_masked = serializers.SerializerMethodField()
    runtime_node_name = serializers.CharField(source='runtime_node.name', read_only=True, default='')

    class Meta:
        model = ModelProvider
        fields = [
            'id', 'name', 'provider_type', 'provider_type_display',
            'api_url', 'has_api_key', 'api_key_masked', 'model_name', 'executor_class',
            'deployment_mode', 'runtime_node', 'runtime_node_name',
            'runtime_model_id', 'runtime_adapter', 'supports_cloud_fallback',
            'health_status',
            # LLM专用参数
            'max_tokens', 'temperature', 'top_p',
            # 通用参数
            'timeout', 'is_active', 'priority',
            # 限流配置
            'rate_limit_rpm', 'rate_limit_rpd',
            # 额外配置
            'extra_config',
            # 统计信息
            'total_usage_count', 'success_count', 'failed_count',
            'success_rate', 'avg_latency_ms', 'total_tokens_used',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_has_api_key(self, obj):
        return bool(obj.api_key)

    def get_api_key_masked(self, obj):
        return mask_secret(obj.api_key)

    def get_total_usage_count(self, obj):
        """获取总使用次数"""
        return visible_provider_usage_logs(self, obj).count()

    def get_success_count(self, obj):
        """获取成功次数"""
        return visible_provider_usage_logs(self, obj).filter(status='success').count()

    def get_failed_count(self, obj):
        """获取失败次数"""
        return visible_provider_usage_logs(self, obj).filter(status='failed').count()

    def get_success_rate(self, obj):
        """获取成功率"""
        logs = visible_provider_usage_logs(self, obj)
        total = logs.count()
        if total == 0:
            return 0.0
        success = logs.filter(status='success').count()
        return round((success / total) * 100, 2)

    def get_avg_latency_ms(self, obj):
        """获取平均延迟"""
        from django.db.models import Avg
        result = visible_provider_usage_logs(self, obj).aggregate(
            avg_latency=Avg('latency_ms')
        )
        return round(result['avg_latency'] or 0, 2)

    def get_total_tokens_used(self, obj):
        """获取总Token使用量"""
        from django.db.models import Sum
        result = visible_provider_usage_logs(self, obj).aggregate(
            total_tokens=Sum('tokens_used')
        )
        return result['total_tokens'] or 0


class ModelProviderCreateSerializer(serializers.ModelSerializer):
    """模型提供商创建序列化器"""

    class Meta:
        model = ModelProvider
        fields = [
            'name', 'provider_type', 'api_url', 'api_key', 'model_name',
            'executor_class',
            'deployment_mode', 'runtime_node', 'runtime_model_id',
            'runtime_adapter', 'supports_cloud_fallback',
            'max_tokens', 'temperature', 'top_p',
            'timeout', 'is_active', 'priority',
            'rate_limit_rpm', 'rate_limit_rpd',
            'extra_config'
        ]
        extra_kwargs = {
            'api_key': {'write_only': True, 'required': False, 'allow_blank': True},
            'api_url': {'required': False, 'allow_blank': True},
        }

    def validate_api_url(self, value):
        """验证API URL格式"""
        try:
            return validate_service_url(value, field_label='API URL')
        except ValueError as error:
            raise serializers.ValidationError(str(error))

    def validate_api_key(self, value):
        """验证API Key"""
        return (value or '').strip()

    def validate_temperature(self, value):
        """验证温度参数"""
        if value < 0 or value > 2:
            raise serializers.ValidationError("温度参数必须在0-2之间")
        return value

    def validate_top_p(self, value):
        """验证Top P参数"""
        if value < 0 or value > 1:
            raise serializers.ValidationError("Top P参数必须在0-1之间")
        return value

    def validate_priority(self, value):
        """验证优先级"""
        if value < 0:
            raise serializers.ValidationError("优先级不能为负数")
        return value

    def validate(self, attrs):
        """交叉验证"""
        provider_type = attrs.get('provider_type')
        deployment_mode = attrs.get('deployment_mode', 'api')

        try:
            attrs['api_url'] = validate_service_url(
                attrs.get('api_url'),
                require_https_for_non_loopback=deployment_mode == 'api',
                field_label='API URL',
            )
        except ValueError as error:
            raise serializers.ValidationError({'api_url': str(error)})

        if deployment_mode == 'api':
            if not attrs.get('api_url'):
                raise serializers.ValidationError({'api_url': 'API Provider 必须配置地址'})
            if not attrs.get('api_key'):
                raise serializers.ValidationError({'api_key': 'API Provider 必须配置密钥'})
        elif deployment_mode == 'local' and not attrs.get('runtime_node'):
            raise serializers.ValidationError({'runtime_node': '本地 Provider 必须选择 Runtime 节点'})

        # 根据提供商类型验证必要配置
        if provider_type == 'llm':
            # LLM模型需要配置max_tokens和temperature
            if attrs.get('max_tokens', 0) <= 0:
                raise serializers.ValidationError({
                    'max_tokens': 'LLM模型必须配置有效的max_tokens'
                })

        elif provider_type == 'text2image':
            # 文生图模型建议配置extra_config中的图片参数
            extra_config = attrs.get('extra_config', {})
            if not extra_config.get('width') or not extra_config.get('height'):
                # 设置默认值
                if not extra_config.get('width'):
                    extra_config['width'] = 1024
                if not extra_config.get('height'):
                    extra_config['height'] = 1024
                attrs['extra_config'] = extra_config

        elif provider_type == 'image2video':
            # 图生视频模型建议配置extra_config中的视频参数
            extra_config = attrs.get('extra_config', {})
            if not extra_config.get('fps'):
                extra_config['fps'] = 24
            if not extra_config.get('duration'):
                extra_config['duration'] = 5
            attrs['extra_config'] = extra_config

        elif provider_type == 'image_edit':
            # 图片编辑模型建议配置extra_config中的基础图片参数
            extra_config = attrs.get('extra_config', {})
            if not extra_config.get('width'):
                extra_config['width'] = 1024
            if not extra_config.get('height'):
                extra_config['height'] = 1024
            if extra_config.get('strength') is None:
                extra_config['strength'] = 0.35
            attrs['extra_config'] = extra_config

        return attrs


class ModelProviderUpdateSerializer(serializers.ModelSerializer):
    """模型提供商更新序列化器"""

    class Meta:
        model = ModelProvider
        fields = [
            'name', 'api_url', 'api_key', 'model_name',
            'executor_class', 'deployment_mode', 'runtime_node',
            'runtime_model_id', 'runtime_adapter', 'supports_cloud_fallback',
            'max_tokens', 'temperature', 'top_p',
            'timeout', 'is_active', 'priority',
            'rate_limit_rpm', 'rate_limit_rpd',
            'extra_config'
        ]
        extra_kwargs = {
            'api_key': {'write_only': True, 'required': False, 'allow_blank': True},
            'api_url': {'required': False, 'allow_blank': True},
        }

    def validate_api_url(self, value):
        """验证API URL格式"""
        try:
            return validate_service_url(value, field_label='API URL')
        except ValueError as error:
            raise serializers.ValidationError(str(error))

    def validate_api_key(self, value):
        """验证API Key"""
        return (value or '').strip()

    def validate_temperature(self, value):
        """验证温度参数"""
        if value < 0 or value > 2:
            raise serializers.ValidationError("温度参数必须在0-2之间")
        return value

    def validate_top_p(self, value):
        """验证Top P参数"""
        if value < 0 or value > 1:
            raise serializers.ValidationError("Top P参数必须在0-1之间")
        return value

    def validate_priority(self, value):
        """验证优先级"""
        if value < 0:
            raise serializers.ValidationError("优先级不能为负数")
        return value

    def validate(self, attrs):
        deployment_mode = attrs.get('deployment_mode', self.instance.deployment_mode)
        runtime_node = attrs.get('runtime_node', self.instance.runtime_node)
        api_url = attrs.get('api_url', self.instance.api_url)
        api_key = attrs.get('api_key')

        try:
            attrs['api_url'] = validate_service_url(
                api_url,
                require_https_for_non_loopback=deployment_mode == 'api',
                field_label='API URL',
            )
            api_url = attrs['api_url']
        except ValueError as error:
            raise serializers.ValidationError({'api_url': str(error)})

        # 编辑页不会回显旧密钥；空字符串表示“保持不变”，而不是擦除凭据。
        if api_key == '':
            attrs.pop('api_key', None)
            api_key = self.instance.api_key
        if deployment_mode == 'api' and (not api_url or not api_key):
            raise serializers.ValidationError('API Provider 必须保留地址和密钥')
        if deployment_mode == 'local' and not runtime_node:
            raise serializers.ValidationError({'runtime_node': '本地 Provider 必须选择 Runtime 节点'})
        return attrs


class ModelProviderSimpleSerializer(serializers.ModelSerializer):
    """模型提供商简化序列化器 - 仅返回id和name,用于下拉选择"""

    class Meta:
        model = ModelProvider
        fields = ['id', 'name']
        read_only_fields = ['id', 'name']


class ModelUsageLogSerializer(serializers.ModelSerializer):
    """模型使用日志序列化器"""

    model_provider_name = serializers.CharField(
        source='model_provider.name',
        read_only=True
    )
    model_provider_type = serializers.CharField(
        source='model_provider.provider_type',
        read_only=True
    )

    class Meta:
        model = ModelUsageLog
        fields = [
            'id', 'model_provider', 'model_provider_name', 'model_provider_type',
            'request_data', 'response_data', 'request_summary',
            'tokens_used', 'input_tokens', 'output_tokens', 'image_count', 'video_seconds',
            'latency_ms', 'status', 'error_message', 'error_code',
            'deployment_mode', 'estimated_cost', 'settled_cost', 'currency',
            'price_rate', 'work_item', 'runtime_node', 'attempt_number',
            'idempotency_key', 'fallback_from', 'fallback_reason',
            'media_hashes', 'media_dimensions', 'started_at', 'finished_at',
            'project_id', 'stage_type',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['request_data'] = summarize_ledger_payload(
            mask_sensitive_data(data.get('request_data', {}))
        )
        data['response_data'] = summarize_ledger_payload(
            mask_sensitive_data(data.get('response_data', {}))
        )
        data['request_summary'] = mask_sensitive_data(data.get('request_summary', {}))
        data['error_message'] = mask_sensitive_data(data.get('error_message', ''))
        return data


class ModelProviderTestSerializer(serializers.Serializer):
    """模型提供商测试连接序列化器"""

    billable_smoke = serializers.BooleanField(
        required=False,
        default=False,
        help_text='仅在明确执行真实生成测试时设为 true',
    )
    confirmed_max_cost_cny = serializers.DecimalField(
        required=False,
        max_digits=18,
        decimal_places=6,
        min_value=0,
        default=0,
    )

    test_prompt = serializers.CharField(
        required=False,
        default="Hello, this is a test.",
        help_text="测试用的提示词"
    )
    test_image_url = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='测试用图片URL，图生视频模型可传'
    )
    test_image_base64 = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='测试用图片base64，支持传入 data URL 或纯base64'
    )
    test_image_mime_type = serializers.CharField(
        required=False,
        allow_blank=True,
        default='image/jpeg',
        help_text='测试图片 MIME 类型'
    )

    def validate(self, attrs):
        """验证模型提供商配置"""
        provider_id = self.context.get('provider_id')
        if not provider_id:
            raise serializers.ValidationError("缺少模型提供商ID")

        try:
            provider = ModelProvider.objects.get(id=provider_id)
        except ModelProvider.DoesNotExist:
            raise serializers.ValidationError("模型提供商不存在")

        if not provider.is_active:
            raise serializers.ValidationError("模型提供商未激活")

        if attrs.get('billable_smoke'):
            if provider.deployment_mode != 'api':
                raise serializers.ValidationError({'billable_smoke': '本地或 Mock Provider 不属于付费 smoke'})
            if attrs.get('confirmed_max_cost_cny', 0) <= 0:
                raise serializers.ValidationError({
                    'confirmed_max_cost_cny': '付费 smoke 必须明确确认最大费用'
                })

        test_image_base64 = (attrs.get('test_image_base64') or '').strip()
        mime_type = (attrs.get('test_image_mime_type') or 'image/jpeg').strip() or 'image/jpeg'

        if test_image_base64.startswith('data:') and ';base64,' in test_image_base64:
            header, encoded = test_image_base64.split(';base64,', 1)
            mime_type = header.split(':', 1)[1] if ':' in header else mime_type
            test_image_base64 = encoded.strip()

        attrs['test_image_url'] = (attrs.get('test_image_url') or '').strip()
        attrs['test_image_base64'] = test_image_base64
        attrs['test_image_mime_type'] = mime_type

        attrs['provider'] = provider
        return attrs


class VendorModelDiscoverySerializer(serializers.Serializer):
    """厂商模型发现请求。"""

    vendor = serializers.ChoiceField(choices=[(key, value['label']) for key, value in VENDOR_CATALOG.items()])
    capability = serializers.ChoiceField(choices=ModelProvider.PROVIDER_TYPES)
    api_key = serializers.CharField(required=True, trim_whitespace=True)
    api_url = serializers.URLField(required=False, allow_blank=True)

    def validate_api_key(self, value):
        if not value:
            raise serializers.ValidationError('API Key不能为空')
        return value

    def validate_api_url(self, value):
        try:
            return validate_service_url(
                value,
                require_https_for_non_loopback=True,
                field_label='API URL',
            )
        except ValueError as error:
            raise serializers.ValidationError(str(error))

    def validate(self, attrs):
        vendor_config = VENDOR_CATALOG.get(attrs['vendor'], {})
        capabilities = vendor_config.get('capabilities', {})
        if attrs['capability'] not in capabilities:
            raise serializers.ValidationError({'capability': '当前厂商不支持该模型能力'})
        capability_config = capabilities[attrs['capability']]
        return attrs


class VendorModelBatchCreateSerializer(serializers.Serializer):
    """厂商模型批量创建请求。"""

    vendor = serializers.ChoiceField(choices=[(key, value['label']) for key, value in VENDOR_CATALOG.items()])
    capability = serializers.ChoiceField(choices=ModelProvider.PROVIDER_TYPES)
    api_key = serializers.CharField(required=True, trim_whitespace=True)
    api_url = serializers.URLField(required=False, allow_blank=True)
    model_names = serializers.ListField(
        child=serializers.CharField(trim_whitespace=True),
        allow_empty=False,
    )
    is_active = serializers.BooleanField(required=False, default=True)
    timeout = serializers.IntegerField(required=False, min_value=1, max_value=600, default=60)
    max_tokens = serializers.IntegerField(required=False, min_value=1, default=4096)
    temperature = serializers.FloatField(required=False, min_value=0, max_value=2, default=0.7)
    top_p = serializers.FloatField(required=False, min_value=0, max_value=1, default=1.0)
    rate_limit_rpm = serializers.IntegerField(required=False, min_value=1, default=60)
    rate_limit_rpd = serializers.IntegerField(required=False, min_value=1, default=1000)
    priority = serializers.IntegerField(required=False, min_value=0, default=0)

    def validate_api_key(self, value):
        if not value:
            raise serializers.ValidationError('API Key不能为空')
        return value

    def validate_api_url(self, value):
        try:
            return validate_service_url(
                value,
                require_https_for_non_loopback=True,
                field_label='API URL',
            )
        except ValueError as error:
            raise serializers.ValidationError(str(error))

    def validate_model_names(self, value):
        cleaned_names = []
        seen = set()
        for item in value:
            model_name = item.strip()
            if not model_name:
                continue
            if model_name in seen:
                continue
            seen.add(model_name)
            cleaned_names.append(model_name)

        if not cleaned_names:
            raise serializers.ValidationError('至少选择一个模型')

        return cleaned_names

    def validate(self, attrs):
        vendor_config = VENDOR_CATALOG.get(attrs['vendor'], {})
        capabilities = vendor_config.get('capabilities', {})
        if attrs['capability'] not in capabilities:
            raise serializers.ValidationError({'capability': '当前厂商不支持该模型能力'})
        capability_config = capabilities[attrs['capability']]
        return attrs


class VendorConnectionConfigSerializer(serializers.ModelSerializer):
    """厂商导入连接配置序列化器。"""

    vendor = serializers.ChoiceField(choices=[(key, value['label']) for key, value in VENDOR_CATALOG.items()])
    capability = serializers.ChoiceField(choices=ModelProvider.PROVIDER_TYPES)
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_api_key = serializers.SerializerMethodField()
    api_key_masked = serializers.SerializerMethodField()

    class Meta:
        model = VendorConnectionConfig
        fields = [
            'vendor', 'capability', 'api_key', 'has_api_key', 'api_key_masked', 'api_url',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def validate_api_key(self, value):
        return (value or '').strip()

    def get_has_api_key(self, obj):
        return bool(obj.api_key)

    def get_api_key_masked(self, obj):
        return mask_secret(obj.api_key)

    def validate_api_url(self, value):
        try:
            return validate_service_url(
                value,
                require_https_for_non_loopback=True,
                field_label='API URL',
            )
        except ValueError as error:
            raise serializers.ValidationError(str(error))

    def validate(self, attrs):
        vendor_config = VENDOR_CATALOG.get(attrs['vendor'], {})
        capabilities = vendor_config.get('capabilities', {})
        if attrs['capability'] not in capabilities:
            raise serializers.ValidationError({'capability': '当前厂商不支持该模型能力'})
        return attrs


class VendorConnectionConfigQuerySerializer(serializers.Serializer):
    """厂商导入连接配置查询参数。"""

    vendor = serializers.ChoiceField(choices=[(key, value['label']) for key, value in VENDOR_CATALOG.items()])
    capability = serializers.ChoiceField(choices=ModelProvider.PROVIDER_TYPES)

    def validate(self, attrs):
        vendor_config = VENDOR_CATALOG.get(attrs['vendor'], {})
        capabilities = vendor_config.get('capabilities', {})
        if attrs['capability'] not in capabilities:
            raise serializers.ValidationError({'capability': '当前厂商不支持该模型能力'})
        return attrs
