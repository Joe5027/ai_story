"""AI 推理领域模型。

这里仅保存稳定的控制面事实：运行节点、路由、价格、预算、工作项与产物。
具体厂商调用仍由 ``apps.models.ModelProvider`` 和现有执行器负责。
"""

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


CAPABILITY_CHOICES = [
    ('llm', '文本生成'),
    ('text2image', '文生图'),
    ('image_edit', '图片编辑'),
    ('image2video', '图生视频'),
    ('motion_render', '非生成式视频运镜'),
    ('embedding', '向量化'),
    ('speech', '语音'),
]


class UUIDTimestampModel(models.Model):
    """带 UUID 主键和审计时间的抽象基类。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class RuntimeNode(UUIDTimestampModel):
    """可承载开源模型或远端代理的推理运行节点。"""

    NODE_TYPE_CHOICES = [
        ('local_gpu', '本地 GPU'),
        ('rented_gpu', '租用 GPU'),
        ('remote_api', '远端 API'),
        ('cpu', 'CPU 节点'),
        ('edge', '边缘节点'),
    ]
    HEALTH_CHOICES = [
        ('unknown', '未知'),
        ('healthy', '健康'),
        ('degraded', '降级'),
        ('unavailable', '不可用'),
    ]

    name = models.CharField('节点名称', max_length=120, unique=True)
    node_type = models.CharField('节点类型', max_length=20, choices=NODE_TYPE_CHOICES)
    agent_url = models.URLField('Runtime Agent 地址', max_length=500, blank=True, default='')
    access_token = models.CharField(
        'Runtime Agent 访问令牌', max_length=512, blank=True, default='',
        help_text='当前兼容字段为明文；输出和日志必须经过敏感字段遮罩。',
    )
    agent_version = models.CharField('Agent 版本', max_length=64, blank=True, default='')
    endpoint = models.URLField('服务地址', max_length=500, blank=True, default='')
    credential_ref = models.CharField(
        '凭据引用', max_length=255, blank=True, default='',
        help_text='仅保存密钥管理器引用，不保存原始密钥。',
    )
    capabilities = models.JSONField('支持能力', default=list, blank=True)
    hardware_snapshot = models.JSONField('硬件快照', default=dict, blank=True)
    resource_groups = models.JSONField('资源组容量', default=dict, blank=True)
    slot_count = models.PositiveIntegerField('总槽位数', default=1)
    reserved_slot_count = models.PositiveIntegerField('保留槽位数', default=0)
    slot_config = models.JSONField('槽位配置', default=dict, blank=True)
    is_local = models.BooleanField('是否本机节点', default=True)
    health_status = models.CharField(
        '健康状态', max_length=20, choices=HEALTH_CHOICES, default='unknown'
    )
    concurrency_limit = models.PositiveIntegerField('并发上限', default=1)
    priority = models.IntegerField('节点优先级', default=0)
    is_active = models.BooleanField('是否启用', default=True)
    metadata = models.JSONField('节点元数据', default=dict, blank=True)
    last_seen_at = models.DateTimeField('最后在线时间', null=True, blank=True)

    class Meta:
        db_table = 'inference_runtime_nodes'
        ordering = ['-priority', 'name']
        indexes = [
            models.Index(fields=['is_active', 'health_status', '-priority'], name='inf_node_health_idx'),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.reserved_slot_count > self.slot_count:
            raise ValidationError({'reserved_slot_count': '保留槽位不能超过总槽位。'})


class GenerationProfile(UUIDTimestampModel):
    """一类生成任务的模型和安全参数基线。"""

    key = models.SlugField('配置标识', max_length=100)
    name = models.CharField('配置名称', max_length=120)
    capability = models.CharField('生成能力', max_length=20, choices=CAPABILITY_CHOICES)
    model_name = models.CharField('默认模型', max_length=255, blank=True, default='')
    default_parameters = models.JSONField('默认参数', default=dict, blank=True)
    hard_limits = models.JSONField('参数硬限制', default=dict, blank=True)
    allowed_parameters = models.JSONField('允许覆盖的参数', default=list, blank=True)
    description = models.TextField('说明', blank=True, default='')
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        db_table = 'inference_generation_profiles'
        ordering = ['capability', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['capability', 'key'], name='inf_profile_capability_key_uniq'
            ),
        ]
        indexes = [
            models.Index(fields=['capability', 'is_active'], name='inf_profile_cap_idx'),
        ]

    def __str__(self):
        return self.name


class GenerationRoute(UUIDTimestampModel):
    """按优先级匹配上下文并提供候选目标的生成路由。"""

    SCOPE_CHOICES = [('global', '全局'), ('project', '项目')]
    PROFILE_CODE_CHOICES = [('draft', '草稿'), ('balanced', '均衡'), ('final', '成片')]

    name = models.CharField('路由名称', max_length=120, unique=True)
    capability = models.CharField('生成能力', max_length=20, choices=CAPABILITY_CHOICES)
    scope = models.CharField('作用域', max_length=20, choices=SCOPE_CHOICES, default='global')
    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, null=True, blank=True,
        related_name='generation_routes', verbose_name='所属项目',
    )
    stage_type = models.CharField('业务阶段', max_length=40, blank=True, default='')
    profile_code = models.CharField(
        '生成档位', max_length=20, choices=PROFILE_CODE_CHOICES, default='balanced'
    )
    profile = models.ForeignKey(
        GenerationProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='routes', verbose_name='默认生成配置',
    )
    priority = models.IntegerField('路由优先级', default=0)
    match_rules = models.JSONField('匹配规则', default=dict, blank=True)
    local_first = models.BooleanField('本地节点优先', default=True)
    fallback_error_classes = models.JSONField('允许回退的错误分类', default=list, blank=True)
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        db_table = 'inference_generation_routes'
        ordering = ['-priority', 'name']
        indexes = [
            models.Index(fields=['capability', 'is_active', '-priority'], name='inf_route_cap_idx'),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.scope == 'project' and not self.project_id:
            raise ValidationError({'project': '项目作用域路由必须绑定项目。'})
        if self.scope == 'global' and self.project_id:
            raise ValidationError({'project': '全局路由不能绑定项目。'})


class GenerationTarget(UUIDTimestampModel):
    """路由中的具体目标，可同时绑定配置提供商和实际运行节点。"""

    ROLE_CHOICES = [
        ('local_primary', '本地主目标'),
        ('local_secondary', '本地备目标'),
        ('paid_fallback', '付费回退'),
    ]

    route = models.ForeignKey(
        GenerationRoute, on_delete=models.CASCADE, related_name='targets', verbose_name='所属路由'
    )
    name = models.CharField('目标名称', max_length=120)
    role = models.CharField('目标角色', max_length=30, choices=ROLE_CHOICES, default='local_primary')
    position = models.PositiveIntegerField('路由位置', default=0)
    provider = models.ForeignKey(
        'models.ModelProvider', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='inference_targets', verbose_name='模型提供商',
    )
    runtime_node = models.ForeignKey(
        RuntimeNode, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='generation_targets', verbose_name='运行节点',
    )
    profile = models.ForeignKey(
        GenerationProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='targets', verbose_name='目标生成配置',
    )
    priority = models.IntegerField('目标优先级', default=0)
    weight = models.DecimalField('权重', max_digits=10, decimal_places=4, default=Decimal('1'))
    parameter_overrides = models.JSONField('目标参数覆盖', default=dict, blank=True)
    max_concurrency = models.PositiveIntegerField('目标并发上限', default=1)
    timeout_seconds = models.PositiveIntegerField('超时秒数', default=300)
    max_attempts = models.PositiveIntegerField('最大尝试次数', default=1)
    cooldown_until = models.DateTimeField('冷却截止时间', null=True, blank=True)
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        db_table = 'inference_generation_targets'
        ordering = ['position', '-priority', '-weight', 'name']
        unique_together = [('route', 'name')]
        indexes = [
            models.Index(fields=['route', 'is_active', '-priority'], name='inf_target_route_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(provider__isnull=False) | models.Q(runtime_node__isnull=False),
                name='inf_target_has_destination',
            ),
        ]

    def clean(self):
        if not self.provider_id and not self.runtime_node_id:
            raise ValidationError('生成目标至少需要模型提供商或运行节点之一。')
        if self.max_attempts < 1:
            raise ValidationError({'max_attempts': '最大尝试次数至少为 1。'})

    def __str__(self):
        return f'{self.route.name} / {self.name}'


class AIBudgetPolicy(UUIDTimestampModel):
    """可被项目复用的 AI 预算策略及其当前账本摘要。"""

    PERIOD_CHOICES = [
        ('day', '每日'),
        ('month', '每月'),
        ('project', '项目周期'),
        ('lifetime', '永久'),
    ]
    EXHAUSTED_ACTION_CHOICES = [
        ('block', '阻断'),
        ('local_only', '仅本地模型'),
        ('warn', '仅告警'),
    ]

    name = models.CharField('策略名称', max_length=120)
    currency = models.CharField('币种', max_length=3, default='CNY')
    period_type = models.CharField('预算周期', max_length=20, choices=PERIOD_CHOICES, default='project')
    soft_limit = models.DecimalField(
        '软限额', max_digits=18, decimal_places=6, null=True, blank=True
    )
    hard_limit = models.DecimalField(
        '硬限额', max_digits=18, decimal_places=6, null=True, blank=True,
        default=Decimal('0'),
    )
    daily_limit_cny = models.DecimalField(
        '全局每日预算（CNY）', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    monthly_limit_cny = models.DecimalField(
        '全局每月预算（CNY）', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    tooling_limit_cny = models.DecimalField(
        '全局工具预算（CNY）', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    spent_amount = models.DecimalField(
        '已结算金额', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    reserved_amount = models.DecimalField(
        '已预留金额', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    allow_overage = models.BooleanField('允许超额结算', default=False)
    exhausted_action = models.CharField(
        '额度耗尽动作', max_length=20, choices=EXHAUSTED_ACTION_CHOICES, default='block'
    )
    reservation_ttl_seconds = models.PositiveIntegerField('预留有效期秒数', default=1800)
    period_started_at = models.DateTimeField('周期开始时间', null=True, blank=True)
    period_ends_at = models.DateTimeField('周期结束时间', null=True, blank=True)
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        db_table = 'inference_budget_policies'
        indexes = [models.Index(fields=['is_active', 'period_ends_at'], name='inf_budget_active_idx')]

    @property
    def remaining_amount(self):
        if self.hard_limit is None:
            return None
        return self.hard_limit - self.spent_amount - self.reserved_amount

    def clean(self):
        if self.soft_limit is not None and self.soft_limit < 0:
            raise ValidationError({'soft_limit': '软限额不能小于 0。'})
        if self.hard_limit is not None and self.hard_limit < 0:
            raise ValidationError({'hard_limit': '硬限额不能小于 0。'})
        for field in ('daily_limit_cny', 'monthly_limit_cny', 'tooling_limit_cny'):
            if getattr(self, field) < 0:
                raise ValidationError({field: '预算不能小于 0。'})
        if (
            self.soft_limit is not None and self.hard_limit is not None
            and self.soft_limit > self.hard_limit
        ):
            raise ValidationError({'soft_limit': '软限额不能大于硬限额。'})

    def __str__(self):
        return self.name


class ProjectAISettings(UUIDTimestampModel):
    """项目级推理、回退和预算设置。"""

    PROFILE_CODE_CHOICES = [('draft', '草稿'), ('balanced', '均衡'), ('final', '成片')]

    project = models.OneToOneField(
        'projects.Project', on_delete=models.CASCADE, related_name='ai_settings', verbose_name='项目'
    )
    default_profile = models.ForeignKey(
        GenerationProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='project_settings', verbose_name='默认生成配置',
    )
    default_route = models.ForeignKey(
        GenerationRoute, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='project_settings', verbose_name='默认路由',
    )
    budget_policy = models.ForeignKey(
        AIBudgetPolicy, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='project_settings', verbose_name='预算策略',
    )
    capability_routes = models.JSONField('能力路由映射', default=dict, blank=True)
    parameter_overrides = models.JSONField('项目参数覆盖', default=dict, blank=True)
    default_profile_code = models.CharField(
        '默认生成档位', max_length=20, choices=PROFILE_CODE_CHOICES, default='balanced'
    )
    prefer_local = models.BooleanField('优先本地推理', default=True)
    allow_paid_fallback = models.BooleanField('允许付费回退', default=False)
    allow_cloud_data_transfer = models.BooleanField('允许素材出站', default=False)
    cloud_authorized_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='authorized_ai_cloud_projects', verbose_name='出站授权人',
    )
    cloud_authorized_at = models.DateTimeField('出站授权时间', null=True, blank=True)
    project_budget_cny = models.DecimalField(
        '项目付费预算（CNY）', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        db_table = 'inference_project_settings'

    def __str__(self):
        return f'{self.project} / AI 设置'

    def clean(self):
        if self.project_budget_cny < 0:
            raise ValidationError({'project_budget_cny': '项目预算不能小于 0。'})
        if self.allow_cloud_data_transfer and (
            not self.cloud_authorized_by_id or not self.cloud_authorized_at
        ):
            raise ValidationError('允许素材出站时必须记录授权人和授权时间。')


class ProviderPriceRate(UUIDTimestampModel):
    """按提供商、模型模式和计费单位生效的单价。"""

    UNIT_CHOICES = [
        ('input_token', '输入 Token'),
        ('output_token', '输出 Token'),
        ('token', '总 Token'),
        ('image', '图片张数'),
        ('image_megapixel', '图片百万像素'),
        ('video_second', '视频秒数'),
        ('video_task', '视频任务数'),
        ('request', '请求次数'),
        ('gpu_second', 'GPU 秒数'),
    ]

    provider = models.ForeignKey(
        'models.ModelProvider', on_delete=models.CASCADE, null=True, blank=True,
        related_name='price_rates', verbose_name='模型提供商',
    )
    runtime_node = models.ForeignKey(
        RuntimeNode, on_delete=models.CASCADE, null=True, blank=True,
        related_name='price_rates', verbose_name='运行节点',
    )
    capability = models.CharField('生成能力', max_length=20, choices=CAPABILITY_CHOICES)
    model_pattern = models.CharField('模型匹配模式', max_length=255, default='*')
    billing_unit = models.CharField('计费单位', max_length=30, choices=UNIT_CHOICES)
    unit_size = models.DecimalField(
        '单位基数', max_digits=20, decimal_places=6, default=Decimal('1')
    )
    unit_price = models.DecimalField('单位价格', max_digits=20, decimal_places=8)
    minimum_charge = models.DecimalField(
        '最低收费', max_digits=20, decimal_places=8, default=Decimal('0')
    )
    currency = models.CharField('币种', max_length=3, default='CNY')
    exchange_rate_to_cny = models.DecimalField(
        '折算人民币汇率', max_digits=20, decimal_places=8, default=Decimal('1')
    )
    source_note = models.TextField('价格来源说明', blank=True, default='')
    conditions = models.JSONField('附加计价条件', default=dict, blank=True)
    version = models.CharField('价格版本', max_length=64, default='v1')
    priority = models.IntegerField('匹配优先级', default=0)
    effective_from = models.DateTimeField('生效时间', null=True, blank=True)
    effective_to = models.DateTimeField('失效时间', null=True, blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    metadata = models.JSONField('计价元数据', default=dict, blank=True)

    class Meta:
        db_table = 'inference_provider_price_rates'
        ordering = ['-priority', '-effective_from']
        indexes = [
            models.Index(
                fields=['capability', 'billing_unit', 'is_active', '-priority'],
                name='inf_price_match_idx',
            ),
        ]

    def clean(self):
        if not self.provider_id and not self.runtime_node_id:
            raise ValidationError('价格至少需要绑定模型提供商或运行节点。')
        if self.unit_size <= 0:
            raise ValidationError({'unit_size': '单位基数必须大于 0。'})
        if self.unit_price < 0:
            raise ValidationError({'unit_price': '单位价格不能小于 0。'})
        if self.exchange_rate_to_cny <= 0:
            raise ValidationError({'exchange_rate_to_cny': '人民币折算汇率必须大于 0。'})

    def __str__(self):
        owner = self.provider or self.runtime_node
        return f'{owner} / {self.model_pattern} / {self.billing_unit}'


class GenerationWorkItem(UUIDTimestampModel):
    """一次可恢复、可审计的生成工作项。"""

    STATUS_CHOICES = [
        ('waiting', '等待认领'),
        ('leased', '已租约认领'),
        ('running', '执行中'),
        ('retry_wait', '等待重试'),
        ('succeeded', '成功'),
        ('failed', '失败'),
        ('cancelled', '已取消'),
    ]

    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, related_name='generation_work_items', verbose_name='项目'
    )
    stage_execution_id = models.UUIDField('阶段执行批次', default=uuid.uuid4, db_index=True)
    capability = models.CharField('生成能力', max_length=20, choices=CAPABILITY_CHOICES)
    stage_type = models.CharField('业务阶段', max_length=40, blank=True, default='')
    storyboard_id = models.UUIDField('分镜 ID', null=True, blank=True)
    tile_index = models.PositiveIntegerField('切片序号', null=True, blank=True)
    segment_index = models.PositiveIntegerField('分段序号', null=True, blank=True)
    profile = models.ForeignKey(
        GenerationProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='work_items', verbose_name='生成配置',
    )
    route = models.ForeignKey(
        GenerationRoute, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='work_items', verbose_name='命中路由',
    )
    target = models.ForeignKey(
        GenerationTarget, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='work_items', verbose_name='命中目标',
    )
    provider = models.ForeignKey(
        'models.ModelProvider', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='generation_work_items', verbose_name='模型提供商',
    )
    runtime_node = models.ForeignKey(
        RuntimeNode, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='work_items', verbose_name='运行节点',
    )
    idempotency_key = models.CharField('幂等键', max_length=128, default=uuid.uuid4)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='waiting')
    priority = models.IntegerField('优先级', default=0)
    request_parameters = models.JSONField('请求参数', default=dict, blank=True)
    effective_parameters = models.JSONField('生效参数', default=dict, blank=True)
    usage = models.JSONField('计费用量', default=dict, blank=True)
    estimated_cost = models.DecimalField(
        '预估成本', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    actual_cost = models.DecimalField(
        '实际成本', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    currency = models.CharField('币种', max_length=3, default='CNY')
    provider_request_id = models.CharField('厂商请求 ID', max_length=255, blank=True, default='')
    agent_job_id = models.CharField('Runtime Agent Job ID', max_length=255, blank=True, default='')
    route_snapshot = models.JSONField('路由快照', default=dict, blank=True)
    attempt_count = models.PositiveIntegerField('尝试次数', default=0)
    max_attempts = models.PositiveIntegerField('最大尝试次数', default=1)
    version = models.PositiveIntegerField('状态版本', default=0)
    error_class = models.CharField('错误分类', max_length=40, blank=True, default='')
    error_code = models.CharField('错误码', max_length=120, blank=True, default='')
    error_message = models.TextField('错误信息', blank=True, default='')
    scheduled_at = models.DateTimeField('计划执行时间', null=True, blank=True)
    claimed_at = models.DateTimeField('领取时间', null=True, blank=True)
    lease_owner = models.CharField('租约持有者', max_length=255, blank=True, default='')
    lease_expires_at = models.DateTimeField('租约截止时间', null=True, blank=True)
    heartbeat_at = models.DateTimeField('最后心跳时间', null=True, blank=True)
    next_retry_at = models.DateTimeField('下次重试时间', null=True, blank=True)
    started_at = models.DateTimeField('开始时间', null=True, blank=True)
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)
    depends_on = models.ManyToManyField(
        'self', symmetrical=False, blank=True,
        related_name='dependent_work_items', verbose_name='前置工作项',
    )
    projection_status = models.CharField(
        '业务投影状态', max_length=20,
        choices=[
            ('pending', '等待投影'),
            ('completed', '投影完成'),
            ('failed', '投影失败'),
        ],
        default='pending',
    )
    projection_error = models.TextField('业务投影错误', blank=True, default='')
    projected_at = models.DateTimeField('业务投影时间', null=True, blank=True)

    class Meta:
        db_table = 'inference_generation_work_items'
        ordering = ['-priority', 'created_at']
        indexes = [
            models.Index(fields=['status', '-priority', 'created_at'], name='inf_work_queue_idx'),
            models.Index(fields=['project', 'capability', 'status'], name='inf_work_project_idx'),
            models.Index(
                fields=['project', 'stage_type', 'stage_execution_id'],
                name='inf_work_stage_exec_idx',
            ),
            models.Index(fields=['provider_request_id'], name='inf_work_request_idx'),
            models.Index(fields=['status', 'lease_expires_at'], name='inf_work_lease_idx'),
            models.Index(fields=['status', 'next_retry_at'], name='inf_work_retry_idx'),
        ]
        unique_together = [('project', 'idempotency_key')]

    def __str__(self):
        return f'{self.project} / {self.capability} / {self.status}'


class BudgetReservation(UUIDTimestampModel):
    """预算预留与最终结算记录，幂等键保证重复请求不重复占额。"""

    STATUS_CHOICES = [
        ('active', '已预留'),
        ('settled', '已结算'),
        ('released', '已释放'),
        ('expired', '已过期'),
        ('ambiguous', '结果不明确'),
        ('manual_review', '等待人工复核'),
    ]

    policy = models.ForeignKey(
        AIBudgetPolicy, on_delete=models.CASCADE, related_name='reservations', verbose_name='预算策略'
    )
    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, related_name='budget_reservations', verbose_name='项目'
    )
    work_item = models.ForeignKey(
        GenerationWorkItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='budget_reservations', verbose_name='工作项',
    )
    idempotency_key = models.CharField('幂等键', max_length=120)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='active')
    estimated_amount = models.DecimalField('预估金额', max_digits=18, decimal_places=6)
    reserved_amount = models.DecimalField('预留金额', max_digits=18, decimal_places=6)
    settled_amount = models.DecimalField(
        '结算金额', max_digits=18, decimal_places=6, default=Decimal('0')
    )
    currency = models.CharField('币种', max_length=3, default='CNY')
    expires_at = models.DateTimeField('过期时间', null=True, blank=True)
    settled_at = models.DateTimeField('结算时间', null=True, blank=True)
    released_at = models.DateTimeField('释放时间', null=True, blank=True)
    details = models.JSONField('预留详情', default=dict, blank=True)

    class Meta:
        db_table = 'inference_budget_reservations'
        unique_together = [('policy', 'idempotency_key')]
        indexes = [
            models.Index(fields=['policy', 'status', 'expires_at'], name='inf_reserve_policy_idx'),
            models.Index(fields=['project', 'status'], name='inf_reserve_project_idx'),
        ]

    def __str__(self):
        return f'{self.policy} / {self.idempotency_key} / {self.status}'


class MediaArtifact(UUIDTimestampModel):
    """由生成工作项产出的媒体或文本工件。"""

    KIND_CHOICES = [
        ('text', '文本'),
        ('image', '图片'),
        ('video', '视频'),
        ('audio', '音频'),
        ('metadata', '元数据'),
    ]
    STATUS_CHOICES = [
        ('pending', '待写入'),
        ('ready', '可用'),
        ('failed', '失败'),
        ('deleted', '已删除'),
    ]
    LIFECYCLE_CHOICES = [
        ('source', '源素材'),
        ('intermediate', '中间产物'),
        ('failed', '失败隔离产物'),
        ('final', '最终产物'),
    ]

    work_item = models.ForeignKey(
        GenerationWorkItem, on_delete=models.CASCADE, related_name='artifacts', verbose_name='工作项'
    )
    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, related_name='media_artifacts', verbose_name='项目'
    )
    kind = models.CharField('工件类型', max_length=20, choices=KIND_CHOICES)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    lifecycle = models.CharField(
        '生命周期', max_length=20, choices=LIFECYCLE_CHOICES, default='intermediate'
    )
    uri = models.CharField('工件地址', max_length=1024)
    storage_backend = models.CharField('存储后端', max_length=40, default='local')
    mime_type = models.CharField('MIME 类型', max_length=120, blank=True, default='')
    checksum = models.CharField('校验值', max_length=128, blank=True, default='')
    sha256 = models.CharField('SHA-256', max_length=64, blank=True, default='')
    file_size = models.BigIntegerField('文件大小字节', default=0)
    width = models.PositiveIntegerField('宽度', default=0)
    height = models.PositiveIntegerField('高度', default=0)
    duration_seconds = models.DecimalField(
        '时长秒数', max_digits=12, decimal_places=3, default=Decimal('0')
    )
    fps = models.DecimalField('帧率', max_digits=8, decimal_places=3, default=Decimal('0'))
    segment_index = models.PositiveIntegerField('分段序号', null=True, blank=True)
    segment_start = models.DecimalField(
        '分段开始秒', max_digits=12, decimal_places=3, null=True, blank=True
    )
    segment_end = models.DecimalField(
        '分段结束秒', max_digits=12, decimal_places=3, null=True, blank=True
    )
    metadata = models.JSONField('工件元数据', default=dict, blank=True)
    expires_at = models.DateTimeField('过期时间', null=True, blank=True)
    protected = models.BooleanField('受保护', default=False)
    quarantined = models.BooleanField('已隔离', default=False)
    deleted_at = models.DateTimeField('删除时间', null=True, blank=True)

    class Meta:
        db_table = 'inference_media_artifacts'
        ordering = ['work_item', 'segment_index', 'created_at']
        indexes = [
            models.Index(fields=['work_item', 'kind', 'status'], name='inf_artifact_work_idx'),
            models.Index(fields=['project', 'kind', 'created_at'], name='inf_artifact_proj_idx'),
            models.Index(fields=['lifecycle', 'expires_at'], name='inf_artifact_life_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['work_item', 'uri'], name='inf_artifact_work_uri_uniq'
            ),
        ]

    def __str__(self):
        return f'{self.work_item_id} / {self.kind}'
