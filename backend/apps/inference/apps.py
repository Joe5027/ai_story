from django.apps import AppConfig


class InferenceConfig(AppConfig):
    """统一推理领域应用配置。"""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.inference'
    verbose_name = 'AI 推理控制'
