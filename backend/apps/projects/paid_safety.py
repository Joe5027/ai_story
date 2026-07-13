"""项目旧流水线的付费直连安全检查。"""


def pipeline_direct_api_providers(project):
    """返回旧完整流水线可能直接选择、且无法预聚合成本的 API Provider。

    兼容期完整流水线仍使用旧 Processor 大循环。若模型配置或 PromptTemplate
    直接绑定 API，它可能在没有逐阶段费用确认的情况下被选择；因此 Web 入队和
    Celery 真正开跑前都调用本函数 fail closed，防止排队期间配置漂移造成付费。
    """

    from apps.models.models import ModelProvider
    from apps.prompts.models import PromptTemplate

    from .models import ProjectModelConfig

    provider_ids = set()
    config = ProjectModelConfig.objects.filter(project=project).first()
    if config:
        for field in (
            'rewrite_providers', 'storyboard_providers', 'image_providers',
            'camera_providers', 'video_providers',
        ):
            provider_ids.update(
                getattr(config, field).filter(
                    deployment_mode='api', is_active=True
                ).values_list('pk', flat=True)
            )
    if project.prompt_template_set_id:
        provider_ids.update(
            PromptTemplate.objects.filter(
                template_set_id=project.prompt_template_set_id,
                is_active=True,
                model_provider__deployment_mode='api',
                model_provider__is_active=True,
            ).values_list('model_provider_id', flat=True)
        )
    return ModelProvider.objects.filter(pk__in=provider_ids).order_by('name')
