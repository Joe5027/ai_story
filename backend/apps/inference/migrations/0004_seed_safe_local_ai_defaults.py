from decimal import Decimal

from django.db import migrations


TECHNICAL_FALLBACK_CODES = [
    'NODE_UNAVAILABLE',
    'RUNTIME_NOT_READY',
    'EXECUTION_TIMEOUT',
    'GPU_OOM',
    'RUNTIME_CRASH',
    'EMPTY_OUTPUT',
    'OUTPUT_SCHEMA_INVALID',
    'ARTIFACT_DOWNLOAD_FAILED',
    'CHECKSUM_MISMATCH',
]


PROFILE_PARAMETERS = {
    'llm': {
        'draft': {'max_tokens': 4096, 'temperature': 0.3, 'response_format': 'json'},
        'balanced': {'max_tokens': 8192, 'temperature': 0.25, 'response_format': 'json'},
        'final': {'max_tokens': 12288, 'temperature': 0.2, 'response_format': 'json_schema'},
    },
    'text2image': {
        'draft': {'width': 768, 'height': 768, 'sample_count': 1, 'steps': 12},
        'balanced': {'width': 1024, 'height': 1024, 'sample_count': 1, 'steps': 20},
        'final': {'width': 1280, 'height': 720, 'sample_count': 1, 'steps': 28},
    },
    'image_edit': {
        'draft': {'width': 768, 'height': 768, 'sample_count': 1, 'steps': 12},
        'balanced': {'width': 1024, 'height': 1024, 'sample_count': 1, 'steps': 20},
        'final': {'width': 1280, 'height': 720, 'sample_count': 1, 'steps': 28},
    },
    'image2video': {
        'draft': {'width': 1280, 'height': 720, 'fps': 24, 'duration_seconds': 10},
        'balanced': {'width': 854, 'height': 480, 'fps': 24, 'duration_seconds': 10, 'steps': 4},
        'final': {'width': 1280, 'height': 720, 'fps': 24, 'duration_seconds': 10, 'steps': 4},
    },
    'motion_render': {
        'draft': {'width': 1280, 'height': 720, 'fps': 24, 'duration_seconds': 10},
        'balanced': {'width': 1280, 'height': 720, 'fps': 24, 'duration_seconds': 10},
        'final': {'width': 1280, 'height': 720, 'fps': 24, 'duration_seconds': 10, 'interpolate': True},
    },
}


PROVIDER_DEFAULTS = {
    'llm': {
        'name': 'Local Qwen3.5 9B',
        'model_name': 'Qwen3.5-9B 4-bit',
        'runtime_model_id': 'qwen3.5:9b-q4',
        'runtime_adapter': 'ollama',
        'executor_class': 'core.ai_client.runtime_agent_client.RuntimeAgentLLMClient',
        'node': 'local-gpu-0',
    },
    'text2image': {
        'name': 'Local FLUX.2 Klein 4B',
        'model_name': 'FLUX.2 Klein 4B',
        'runtime_model_id': 'flux2-klein-4b',
        'runtime_adapter': 'comfyui',
        'executor_class': 'core.ai_client.runtime_agent_client.RuntimeAgentText2ImageClient',
        'node': 'local-gpu-0',
    },
    'image_edit': {
        'name': 'Local FLUX.2 Klein 4B Edit',
        'model_name': 'FLUX.2 Klein 4B',
        'runtime_model_id': 'flux2-klein-4b-edit',
        'runtime_adapter': 'comfyui',
        'executor_class': 'core.ai_client.runtime_agent_client.RuntimeAgentImageEditClient',
        'node': 'local-gpu-0',
    },
    'image2video': {
        'name': 'Local LightX2V Wan2.2',
        'model_name': 'Wan2.2 I2V A14B 4-step quantized',
        'runtime_model_id': 'wan2.2-i2v-a14b-4step-quant',
        'runtime_adapter': 'lightx2v',
        'executor_class': 'core.ai_client.runtime_agent_client.RuntimeAgentImage2VideoClient',
        'node': 'local-gpu-0',
    },
    'motion_render': {
        'name': 'Local FFmpeg Motion',
        'model_name': 'FFmpeg Motion Renderer',
        'runtime_model_id': 'ffmpeg-motion-v1',
        'runtime_adapter': 'ffmpeg',
        'executor_class': 'core.ai_client.runtime_agent_client.RuntimeAgentMotionRenderClient',
        'node': 'local-cpu-motion',
    },
}


def seed_safe_defaults(apps, schema_editor):
    """只建立禁付费、未启用模型的安全初始控制面。"""

    RuntimeNode = apps.get_model('inference', 'RuntimeNode')
    GenerationProfile = apps.get_model('inference', 'GenerationProfile')
    GenerationRoute = apps.get_model('inference', 'GenerationRoute')
    GenerationTarget = apps.get_model('inference', 'GenerationTarget')
    AIBudgetPolicy = apps.get_model('inference', 'AIBudgetPolicy')
    ModelProvider = apps.get_model('models', 'ModelProvider')

    gpu_node, _ = RuntimeNode.objects.update_or_create(
        name='local-gpu-0',
        defaults={
            'node_type': 'local_gpu',
            'agent_url': 'http://127.0.0.1:9100',
            'is_local': True,
            'slot_count': 1,
            'concurrency_limit': 1,
            'resource_groups': {'gpu': 1},
            'slot_config': {'gpu': {'capacity': 1, 'exclusive': True}},
            'health_status': 'unknown',
            'is_active': True,
        },
    )
    cpu_node, _ = RuntimeNode.objects.update_or_create(
        name='local-cpu-motion',
        defaults={
            'node_type': 'cpu',
            'agent_url': 'http://127.0.0.1:9100',
            'is_local': True,
            'slot_count': 2,
            'concurrency_limit': 2,
            'resource_groups': {'cpu_motion': 2},
            'slot_config': {'cpu_motion': {'capacity': 2, 'exclusive': False}},
            'health_status': 'unknown',
            'is_active': True,
        },
    )
    nodes = {'local-gpu-0': gpu_node, 'local-cpu-motion': cpu_node}

    AIBudgetPolicy.objects.get_or_create(
        name='Default zero-cost safety policy',
        defaults={
            'currency': 'CNY',
            'period_type': 'lifetime',
            'hard_limit': Decimal('0'),
            'daily_limit_cny': Decimal('0'),
            'monthly_limit_cny': Decimal('0'),
            'tooling_limit_cny': Decimal('0'),
            'exhausted_action': 'block',
            'is_active': True,
        },
    )

    profiles = {}
    for capability, modes in PROFILE_PARAMETERS.items():
        for code, parameters in modes.items():
            profile, _ = GenerationProfile.objects.update_or_create(
                capability=capability,
                key=code,
                defaults={
                    'name': f'{capability} / {code}',
                    'default_parameters': parameters,
                    'hard_limits': {
                        'sample_count': {'min': 1, 'max': 1},
                        'fps': {'min': 1, 'max': 24},
                        'duration_seconds': {'min': 1, 'max': 10},
                    },
                    'allowed_parameters': sorted(parameters.keys()),
                    'description': '固定版本质量档；安装并通过基准后再启用对应 Provider。',
                    'is_active': True,
                },
            )
            profiles[(capability, code)] = profile

    providers = {}
    for capability, config in PROVIDER_DEFAULTS.items():
        provider, _ = ModelProvider.objects.update_or_create(
            name=config['name'],
            provider_type=capability,
            defaults={
                'executor_class': config['executor_class'],
                'api_url': '',
                'api_key': '',
                'model_name': config['model_name'],
                'deployment_mode': 'local',
                'runtime_node': nodes[config['node']],
                'runtime_model_id': config['runtime_model_id'],
                'runtime_adapter': config['runtime_adapter'],
                'supports_cloud_fallback': False,
                'health_status': 'unavailable',
                # 模型尚未下载、digest 尚未固定，必须由安装向导验证后显式启用。
                'is_active': False,
                'priority': 100,
                'timeout': 1800 if capability in {'image2video', 'text2image', 'image_edit'} else 300,
                'extra_config': {
                    'installed': False,
                    'workflow_version': 'v1',
                    'model_digest': '',
                },
            },
        )
        providers[capability] = provider

    for capability in PROVIDER_DEFAULTS:
        for code in ('draft', 'balanced', 'final'):
            route, _ = GenerationRoute.objects.update_or_create(
                name=f'default-{capability}-{code}',
                defaults={
                    'capability': capability,
                    'scope': 'global',
                    'project': None,
                    'stage_type': '',
                    'profile_code': code,
                    'profile': profiles[(capability, code)],
                    'priority': 100,
                    'local_first': True,
                    'fallback_error_classes': TECHNICAL_FALLBACK_CODES,
                    'is_active': True,
                },
            )
            GenerationTarget.objects.update_or_create(
                route=route,
                name='local-primary',
                defaults={
                    'role': 'local_primary',
                    'position': 0,
                    'provider': providers[capability],
                    'runtime_node': nodes[PROVIDER_DEFAULTS[capability]['node']],
                    'profile': profiles[(capability, code)],
                    'priority': 100,
                    'max_concurrency': 1 if capability != 'motion_render' else 2,
                    'timeout_seconds': 1800 if capability == 'image2video' else 600,
                    'max_attempts': 2,
                    'is_active': True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('models', '0007_classify_existing_provider_modes'),
        ('inference', '0003_auto_20260713_1455'),
    ]

    operations = [
        migrations.RunPython(seed_safe_defaults, migrations.RunPython.noop),
    ]
