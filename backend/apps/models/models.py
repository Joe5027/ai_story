"""
模型管理领域模型
遵循依赖倒置原则(DIP): 依赖抽象的ModelProvider,而非具体实现
"""

import uuid
from django.conf import settings
from django.db import models


class ModelProvider(models.Model):
    """
    模型提供商
    职责: 存储AI模型的配置信息
    """

    PROVIDER_TYPES = [
        ('llm', 'LLM模型'),
        ('text2image', '文生图模型'),
        ('image2video', '图生视频模型'),
        ('image_edit', '图片编辑模型'),
        ('motion_render', '非生成式视频运镜'),
    ]

    # 执行器选项定义
    LLM_EXECUTORS = [
        ('core.ai_client.runtime_agent_client.RuntimeAgentLLMClient', 'Runtime Agent 本地 LLM'),
        ('core.ai_client.openai_client.OpenAIClient', 'OpenAI兼容客户端'),
        ('core.ai_client.mock_llm_client.MockLLMClient', 'Mock LLM客户端（测试用）'),
    ]

    TEXT2IMAGE_EXECUTORS = [
        ('core.ai_client.runtime_agent_client.RuntimeAgentText2ImageClient', 'Runtime Agent 本地文生图'),
        ('core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor', 'OpenAI Images Generations 执行器'),
        ('core.ai_client.executors.chat_completions_image_executor.ChatCompletionsImageExecutor', 'Chat Completions 图片执行器'),
        ('core.ai_client.text2image_client.Text2ImageClient', '兼容文生图客户端（旧版）'),
        ('core.ai_client.comfyui_client.ComfyUIClient', 'ComfyUI客户端'),
        ('core.ai_client.mock_text2image_client.MockText2ImageClient', 'Mock 文生图客户端（测试用）'),
    ]

    IMAGE2VIDEO_EXECUTORS = [
        ('core.ai_client.runtime_agent_client.RuntimeAgentImage2VideoClient', 'Runtime Agent 本地图生视频'),
        ('core.ai_client.image2video_client.VideoGeneratorClient', '图生视频客户端'),
        ('core.ai_client.volcengine_image2video_client.VolcengineImage2VideoClient', '火山引擎图生视频客户端'),
        ('core.ai_client.siliconflow_video_client.SiliconFlowVideoClient', '硅基流动视频客户端'),
        ('core.ai_client.comfyui_client.ComfyUIClient', 'ComfyUI客户端'),
        ('core.ai_client.mock_image2video_client.MockImage2VideoClient', 'Mock 图生视频客户端（测试用）'),
    ]

    IMAGE_EDIT_EXECUTORS = [
        ('core.ai_client.runtime_agent_client.RuntimeAgentImageEditClient', 'Runtime Agent 本地图片编辑'),
        ('core.ai_client.executors.openai_images_edit_executor.OpenAIImagesEditExecutor', 'OpenAI Images Edits 执行器'),
        ('core.ai_client.image_edit_client.ImageEditClient', '兼容图片编辑客户端（旧版）'),
        ('core.ai_client.mock_image_edit_client.MockImageEditClient', 'Mock 图片编辑客户端（测试用）'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('名称', max_length=255)
    provider_type = models.CharField('模型作用分类', max_length=20, choices=PROVIDER_TYPES)
    executor_class = models.CharField(
        '执行器类',
        max_length=255,
        help_text='执行器的完整类路径，如: core.ai_client.openai_client.OpenAIClient',
        blank=True,
        default=''
    )

    # API配置
    DEPLOYMENT_MODES = [
        ('mock', 'Mock'),
        ('local', '本地'),
        ('api', 'API'),
    ]

    MOTION_RENDER_EXECUTORS = [
        ('core.ai_client.runtime_agent_client.RuntimeAgentMotionRenderClient', 'Runtime Agent FFmpeg/RIFE 运镜'),
    ]
    HEALTH_STATUSES = [
        ('unknown', '未知'),
        ('healthy', '健康'),
        ('degraded', '降级'),
        ('unavailable', '不可用'),
    ]

    api_url = models.URLField('API地址', blank=True, default='')
    # 用户已选择继续明文保存；所有读取接口、日志与导出必须执行脱敏。
    api_key = models.CharField('API密钥', max_length=512, blank=True, default='')
    model_name = models.CharField('模型名称', max_length=255)

    deployment_mode = models.CharField(
        '部署模式', max_length=10, choices=DEPLOYMENT_MODES, default='api'
    )
    runtime_node = models.ForeignKey(
        'inference.RuntimeNode',
        on_delete=models.SET_NULL,
        related_name='providers',
        null=True,
        blank=True,
        verbose_name='Runtime节点',
    )
    runtime_model_id = models.CharField('Runtime模型ID', max_length=255, blank=True, default='')
    runtime_adapter = models.CharField('Runtime适配器', max_length=64, blank=True, default='')
    supports_cloud_fallback = models.BooleanField('允许作为云端回退目标', default=False)
    health_status = models.CharField(
        '健康状态', max_length=20, choices=HEALTH_STATUSES, default='unknown'
    )

    # LLM专用参数
    max_tokens = models.IntegerField('最大Token数', default=2000)
    temperature = models.FloatField('温度', default=0.7)
    top_p = models.FloatField('Top P', default=1.0)

    # 通用参数
    timeout = models.IntegerField('超时时间(秒)', default=60)
    is_active = models.BooleanField('是否激活', default=True)
    priority = models.IntegerField('优先级/权重', default=0, help_text='用于负载均衡')

    # 限流配置
    rate_limit_rpm = models.IntegerField('每分钟请求数限制', default=60)
    rate_limit_rpd = models.IntegerField('每天请求数限制', default=1000)

    # 额外配置 (JSON格式,存储特定模型的额外参数)
    extra_config = models.JSONField('额外配置', default=dict, blank=True)

    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'model_providers'
        verbose_name = '模型提供商'
        verbose_name_plural = '模型提供商'
        ordering = ['-priority', '-created_at']
        indexes = [
            models.Index(fields=['provider_type', 'is_active', '-priority']),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_provider_type_display()})'

    def get_executor_choices(self):
        """
        根据provider_type返回对应的执行器选项

        Returns:
            list: 执行器选项列表
        """
        executor_map = {
            'llm': self.LLM_EXECUTORS,
            'text2image': self.TEXT2IMAGE_EXECUTORS,
            'image2video': self.IMAGE2VIDEO_EXECUTORS,
            'image_edit': self.IMAGE_EDIT_EXECUTORS,
            'motion_render': self.MOTION_RENDER_EXECUTORS,
        }
        return executor_map.get(self.provider_type, [])

    def get_default_executor(self):
        """
        获取当前provider_type的默认执行器类路径

        Returns:
            str: 默认执行器类路径
        """
        choices = self.get_executor_choices()
        if choices:
            return choices[0][0]  # 返回第一个选项的值
        return ''

    def validate_executor_class(self):
        """
        验证executor_class是否在允许的选项中

        Returns:
            bool: 是否有效
        """
        if not self.executor_class:
            return False

        valid_executors = [choice[0] for choice in self.get_executor_choices()]
        return self.executor_class in valid_executors


class VendorConnectionConfig(models.Model):
    """
    内置厂商导入连接配置
    职责: 保存用户在厂商模型导入场景下使用的API Key和API地址
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vendor_connection_configs',
        verbose_name='用户'
    )
    vendor = models.CharField('厂商标识', max_length=64)
    capability = models.CharField('模型能力', max_length=20, choices=ModelProvider.PROVIDER_TYPES)
    api_key = models.CharField('API密钥', max_length=512, blank=True, default='')
    api_url = models.URLField('API地址', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'vendor_connection_configs'
        verbose_name = '厂商导入连接配置'
        verbose_name_plural = '厂商导入连接配置'
        ordering = ['vendor', 'capability']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'vendor', 'capability'],
                name='unique_vendor_connection_config_per_user'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'vendor', 'capability']),
        ]

    def __str__(self):
        return f'{self.user_id} - {self.vendor} - {self.capability}'


class ModelUsageLog(models.Model):
    """
    模型使用日志
    职责: 记录模型调用历史,用于统计和成本计算
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_provider = models.ForeignKey(
        ModelProvider,
        on_delete=models.CASCADE,
        related_name='usage_logs',
        verbose_name='模型提供商'
    )

    # 使用信息
    request_data = models.JSONField('请求数据', default=dict)
    response_data = models.JSONField('响应数据', default=dict)

    # 统计信息
    tokens_used = models.IntegerField('使用Token数', default=0)
    input_tokens = models.IntegerField('输入Token数', default=0)
    output_tokens = models.IntegerField('输出Token数', default=0)
    image_count = models.IntegerField('图片数量', default=0)
    video_seconds = models.DecimalField('视频秒数', max_digits=12, decimal_places=3, default=0)
    latency_ms = models.IntegerField('延迟(毫秒)', default=0)
    status = models.CharField('状态', max_length=20, default='success')
    error_message = models.TextField('错误信息', blank=True)
    error_code = models.CharField('标准错误码', max_length=64, blank=True, default='')

    # 成本账本；本地调用通常为 0，但仍记录时延与资源节点用于成本对比。
    deployment_mode = models.CharField('部署模式', max_length=10, default='api')
    estimated_cost = models.DecimalField('预计成本', max_digits=18, decimal_places=6, default=0)
    settled_cost = models.DecimalField('结算成本', max_digits=18, decimal_places=6, default=0)
    currency = models.CharField('币种', max_length=3, default='CNY')
    price_rate = models.ForeignKey(
        'inference.ProviderPriceRate',
        on_delete=models.SET_NULL,
        related_name='usage_logs',
        null=True,
        blank=True,
        verbose_name='价目表版本',
    )
    work_item = models.ForeignKey(
        'inference.GenerationWorkItem',
        on_delete=models.SET_NULL,
        related_name='usage_logs',
        null=True,
        blank=True,
        verbose_name='工作项',
    )
    runtime_node = models.ForeignKey(
        'inference.RuntimeNode',
        on_delete=models.SET_NULL,
        related_name='usage_logs',
        null=True,
        blank=True,
        verbose_name='Runtime节点',
    )
    attempt_number = models.PositiveIntegerField('尝试序号', default=1)
    idempotency_key = models.CharField('幂等键', max_length=128, blank=True, default='')
    fallback_from = models.ForeignKey(
        ModelProvider,
        on_delete=models.SET_NULL,
        related_name='fallback_source_logs',
        null=True,
        blank=True,
        verbose_name='回退来源',
    )
    fallback_reason = models.CharField('回退原因', max_length=255, blank=True, default='')
    request_summary = models.JSONField('脱敏请求摘要', default=dict, blank=True)
    media_hashes = models.JSONField('媒体哈希', default=list, blank=True)
    media_dimensions = models.JSONField('媒体尺寸', default=list, blank=True)
    started_at = models.DateTimeField('开始时间', null=True, blank=True)
    finished_at = models.DateTimeField('结束时间', null=True, blank=True)

    # 关联信息
    project_id = models.UUIDField('项目ID', null=True, blank=True)
    stage_type = models.CharField('阶段类型', max_length=20, blank=True)

    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'model_usage_logs'
        verbose_name = '模型使用日志'
        verbose_name_plural = '模型使用日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['model_provider', '-created_at']),
            models.Index(fields=['project_id', 'stage_type']),
        ]

    def __str__(self):
        return f'{self.model_provider.name} - {self.created_at}'
