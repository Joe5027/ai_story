from django.db import migrations


def classify_provider_modes(apps, schema_editor):
    """保守迁移旧 Provider：仅明确的 Mock 执行器归类为 mock，其余为 API。"""

    ModelProvider = apps.get_model('models', 'ModelProvider')
    ModelUsageLog = apps.get_model('models', 'ModelUsageLog')
    for provider in ModelProvider.objects.all().iterator():
        executor = (provider.executor_class or '').lower()
        mode = 'mock' if 'mock' in executor else 'api'
        ModelProvider.objects.filter(pk=provider.pk).update(deployment_mode=mode)
        ModelUsageLog.objects.filter(model_provider_id=provider.pk).update(
            deployment_mode=mode
        )


class Migration(migrations.Migration):

    dependencies = [
        ('models', '0006_auto_20260713_1455'),
    ]

    operations = [
        migrations.RunPython(classify_provider_modes, migrations.RunPython.noop),
    ]
