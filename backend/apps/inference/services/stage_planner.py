"""把项目阶段拆成可恢复、可依赖的细粒度生成工作项。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.content.models import (
    EditedImage,
    GeneratedImage,
    GeneratedVideo,
    MultiGridTile,
    Storyboard,
)
from apps.content.processors.asset_extraction_stage import AssetExtractionStageProcessor
from apps.content.processors.image_edit_stage import ImageEditStageProcessor
from apps.content.processors.llm_stage import LLMStageProcessor
from apps.content.processors.text2image_stage import Text2ImageStageProcessor
from apps.projects.models import ProjectStage

from ..models import GenerationProfile, GenerationWorkItem, ProjectAISettings
from .segments import plan_video_segments
from .work_items import WorkItemService


class StagePlanningError(RuntimeError):
    """阶段输入或 V2 开关不允许创建工作项。"""


@dataclass(frozen=True)
class StagePlan:
    stage_execution_id: uuid.UUID
    work_items: tuple
    created_count: int


class StageWorkItemPlanner:
    """按阶段建立细粒度工作项和视频依赖图。

    规划器只写数据库，不执行模型。这样 Web/Celery 进程即使在派发消息前退出，
    reconciler 仍可从 waiting 工作项恢复。自动路由工作项故意不预填 Provider，
    让 Hybrid 路由保留本地次级目标与受控付费 fallback；只有用户明确选择模型的
    手工任务才应设置 ``provider``。
    """

    LLM_STAGES = {'rewrite', 'asset_extraction', 'storyboard', 'camera_movement'}
    SUPPORTED_STAGES = LLM_STAGES | {'image_generation', 'image_edit', 'video_generation'}

    @classmethod
    def plan_stage(
        cls,
        *,
        project,
        stage_type: str,
        storyboard_ids: Optional[Iterable[str]] = None,
        force_regenerate: bool = False,
        runtime_overrides: Optional[Dict[str, Any]] = None,
        stage_execution_id=None,
        enqueue: bool = True,
        explicit_provider=None,
        manual_api: bool = False,
    ) -> StagePlan:
        if not getattr(settings, 'AI_ROUTER_V2_ENABLED', False):
            raise StagePlanningError('AI_ROUTER_V2_ENABLED 未开启，禁止创建 V2 工作项。')
        if stage_type not in cls.SUPPORTED_STAGES:
            raise StagePlanningError(f'阶段 {stage_type} 尚未支持工作项规划。')

        execution_id = cls._execution_uuid(stage_execution_id)
        settings_obj = ProjectAISettings.objects.filter(project=project, is_active=True).first()
        profile_code = getattr(settings_obj, 'default_profile_code', 'balanced')
        overrides = dict(runtime_overrides or {})

        with transaction.atomic():
            stage, _ = ProjectStage.objects.select_for_update().get_or_create(
                project=project,
                stage_type=stage_type,
            )
            specs = cls._build_specs(
                project=project,
                stage=stage,
                stage_type=stage_type,
                storyboard_ids=storyboard_ids,
                force_regenerate=force_regenerate,
                runtime_overrides=overrides,
            )
            if not specs:
                raise StagePlanningError(f'阶段 {stage_type} 没有可规划的输入项。')

            created_count = 0
            items: List[GenerationWorkItem] = []
            logical_items = {}
            for spec in specs:
                profile = cls._profile(spec['capability'], profile_code)
                spec_provider = (
                    explicit_provider
                    if explicit_provider is not None
                    and explicit_provider.provider_type == spec['capability']
                    else None
                )
                key = cls._idempotency_key(project.pk, stage_type, execution_id, spec['logical_key'])
                defaults = {
                    'stage_execution_id': execution_id,
                    'stage_type': stage_type,
                    'storyboard_id': spec.get('storyboard_id'),
                    'tile_index': spec.get('tile_index'),
                    'segment_index': spec.get('segment_index'),
                    'profile': profile,
                    'provider': spec_provider,
                    'runtime_node': getattr(spec_provider, 'runtime_node', None),
                    'priority': int(spec.get('priority', 0)),
                    'max_attempts': int(spec.get('max_attempts', 2)),
                    'request_parameters': cls._json_safe({
                        **spec['request_parameters'],
                        'profile': profile_code,
                    }),
                    'effective_parameters': profile.default_parameters if profile else {},
                    'route_snapshot': {
                        'automatic_route': spec_provider is None,
                        'manual_api': bool(manual_api and spec_provider is not None),
                        'explicit_provider_id': (
                            str(spec_provider.pk) if spec_provider else None
                        ),
                        'logical_key': spec['logical_key'],
                        'profile_code': profile_code,
                        'stage_execution_id': str(execution_id),
                    },
                }
                existed = GenerationWorkItem.objects.filter(
                    project=project, idempotency_key=key
                ).exists()
                if existed:
                    item = GenerationWorkItem.objects.get(
                        project=project, idempotency_key=key
                    )
                    persisted_request = dict(item.request_parameters or {})
                    # 费用确认值是计划完成估价后写入的系统审计注解，不属于原始
                    # 业务请求体；幂等重放比较时剥离，但 Provider 身份仍严格校验。
                    persisted_request.pop('manual_api', None)
                    persisted_request.pop('confirmed_max_cost_cny', None)
                    if (
                        item.capability != spec['capability']
                        or item.provider_id != getattr(spec_provider, 'pk', None)
                        or persisted_request != defaults['request_parameters']
                    ):
                        raise StagePlanningError('相同阶段幂等键对应了不同请求或 Provider。')
                else:
                    item = WorkItemService.create(
                        project,
                        spec['capability'],
                        idempotency_key=key,
                        **defaults,
                    )
                    created_count += 1
                items.append(item)
                logical_items[spec['logical_key']] = item

            # 依赖只在所有工作项持久化后建立，避免中途退出形成指向不存在任务的边。
            for spec in specs:
                dependency_keys = spec.get('depends_on') or []
                if dependency_keys:
                    logical_items[spec['logical_key']].depends_on.set(
                        [logical_items[key] for key in dependency_keys]
                    )

            stage.status = 'processing'
            stage.started_at = stage.started_at or timezone.now()
            stage.completed_at = None
            stage.error_message = ''
            stage.output_data = {
                **(stage.output_data or {}),
                'generation_work_items': {
                    'stage_execution_id': str(execution_id),
                    'total': len(items),
                    'waiting': len(items),
                    'nonterminal': len(items),
                },
            }
            stage.save(update_fields=[
                'status', 'started_at', 'completed_at', 'error_message', 'output_data',
            ])

            if enqueue:
                item_ids = [str(item.pk) for item in items if not item.depends_on.exists()]
                transaction.on_commit(lambda: cls._enqueue(item_ids))

        return StagePlan(execution_id, tuple(items), created_count)

    @classmethod
    def _build_specs(cls, **context):
        stage_type = context['stage_type']
        if stage_type in cls.LLM_STAGES:
            return cls._llm_specs(**context)
        if stage_type == 'image_generation':
            return cls._image_specs(**context)
        if stage_type == 'image_edit':
            return cls._image_edit_specs(**context)
        return cls._video_specs(**context)

    @classmethod
    def _llm_specs(cls, *, project, stage, stage_type, storyboard_ids, **_kwargs):
        processor = (
            AssetExtractionStageProcessor()
            if stage_type == 'asset_extraction'
            else LLMStageProcessor(stage_type=stage_type)
        )
        input_data = processor._get_input_data(project, stage)
        if storyboard_ids and stage_type == 'camera_movement':
            input_data['storyboard_ids'] = list(storyboard_ids)
        system_prompt = processor._build_prompt(project, input_data)
        specs = []
        for index, task in enumerate(processor._build_tasks(project, input_data), 1):
            scene_number = task.get('scene_number')
            storyboard = None
            if scene_number is not None:
                storyboard = Storyboard.objects.filter(
                    project=project, sequence_number=scene_number
                ).first()
            logical = f'llm:{storyboard.pk if storyboard else index}'
            prompt = f'{system_prompt}\n\n## 用户输入\n{task.get("user_prompt", "")}'
            specs.append({
                'logical_key': logical,
                'capability': 'llm',
                'storyboard_id': getattr(storyboard, 'pk', None),
                'request_parameters': {
                    'prompt': prompt,
                    'max_tokens': processor._get_max_tokens(),
                    'temperature': processor._get_temperature(),
                    'output_contract': stage_type,
                    'usage_estimate': {
                        'input_tokens': max(1, len(prompt) // 2),
                        'output_tokens': processor._get_max_tokens(),
                    },
                    'artifact_lifecycle': 'final',
                },
            })
        return specs

    @classmethod
    def _image_specs(
        cls, *, project, storyboard_ids, force_regenerate, runtime_overrides, **_kwargs
    ):
        processor = Text2ImageStageProcessor()
        query = Storyboard.objects.filter(project=project).order_by('sequence_number')
        if storyboard_ids:
            query = query.filter(pk__in=storyboard_ids)
        if not force_regenerate:
            completed = GeneratedImage.objects.filter(
                storyboard__project=project, status='completed'
            ).values_list('storyboard_id', flat=True)
            query = query.exclude(pk__in=completed)
        specs = []
        for storyboard in query:
            payload = processor._build_generation_prompt_payload(project, {
                'scene_number': storyboard.sequence_number,
                'narration': storyboard.narration_text,
                'visual_prompt': storyboard.image_prompt,
                'shot_type': storyboard.scene_description,
            })
            if any(str(item).startswith('data:') for item in payload.get('image') or []):
                raise StagePlanningError(
                    'V2 工作项不把图片 data URI 写入数据库，请先把参考图登记为 MediaArtifact。'
                )
            width = int(runtime_overrides.get('width') or 1024)
            height = int(runtime_overrides.get('height') or 1024)
            seed = cls._stable_seed(project.pk, storyboard.pk, 'image')
            specs.append({
                'logical_key': f'image:{storyboard.pk}',
                'capability': 'text2image',
                'storyboard_id': storyboard.pk,
                'request_parameters': {
                    'prompt': payload['prompt'],
                    'input_artifacts': payload.get('image') or [],
                    'output_spec': {'width': width, 'height': height, 'sample_count': 1},
                    'width': width,
                    'height': height,
                    'seed': seed,
                    'usage_estimate': {'image_count': 1},
                    'artifact_lifecycle': 'final',
                },
            })
        return specs

    @classmethod
    def _image_edit_specs(
        cls, *, project, storyboard_ids, force_regenerate, runtime_overrides, **_kwargs
    ):
        processor = ImageEditStageProcessor()
        tiles = processor._get_target_tiles(project, list(storyboard_ids or []))
        if not force_regenerate:
            completed = set(EditedImage.objects.filter(
                storyboard__project=project,
                status='completed',
                multi_grid_tile_id__isnull=False,
            ).values_list('multi_grid_tile_id', flat=True))
            tiles = [tile for tile in tiles if tile.pk not in completed]
        specs = []
        for tile in tiles:
            storyboard = tile.task.storyboard
            prompt = processor._build_prompt(project, {
                'scene_number': storyboard.sequence_number,
                'narration': storyboard.narration_text,
                'visual_prompt': storyboard.image_prompt,
                'shot_type': storyboard.scene_description,
                'tile_index': tile.tile_index,
                'tile_image_url': tile.tile_image_url,
            })
            width = int(runtime_overrides.get('width') or tile.width or 1024)
            height = int(runtime_overrides.get('height') or tile.height or 1024)
            specs.append({
                'logical_key': f'edit:{tile.pk}',
                'capability': 'image_edit',
                'storyboard_id': storyboard.pk,
                'tile_index': tile.tile_index,
                'request_parameters': {
                    'prompt': prompt,
                    'image_url': tile.tile_image_url,
                    'input_artifacts': [{'role': 'source', 'uri': tile.tile_image_url}],
                    'output_spec': {
                        'width': width,
                        'height': height,
                        'strength': float(runtime_overrides.get('strength') or 0.35),
                    },
                    'width': width,
                    'height': height,
                    'strength': float(runtime_overrides.get('strength') or 0.35),
                    'seed': cls._stable_seed(project.pk, tile.pk, 'edit'),
                    'source': {
                        'multi_grid_task_id': str(tile.task_id),
                        'multi_grid_tile_id': str(tile.pk),
                        'source_stage_type': 'multi_grid_image',
                        'source_image_url': tile.tile_image_url,
                    },
                    'usage_estimate': {'image_count': 1},
                    'artifact_lifecycle': 'final',
                },
            })
        return specs

    @classmethod
    def _video_specs(
        cls, *, project, storyboard_ids, force_regenerate, runtime_overrides, **_kwargs
    ):
        query = Storyboard.objects.filter(project=project).order_by('sequence_number')
        if storyboard_ids:
            query = query.filter(pk__in=storyboard_ids)
        if not force_regenerate:
            completed = GeneratedVideo.objects.filter(
                storyboard__project=project, status='completed'
            ).values_list('storyboard_id', flat=True)
            query = query.exclude(pk__in=completed)
        specs = []
        for storyboard in query:
            image = GeneratedImage.objects.filter(
                storyboard=storyboard, status='completed'
            ).order_by('-created_at').first()
            camera = getattr(storyboard, 'camera_movement', None)
            if not image or not camera:
                raise StagePlanningError(
                    f'分镜 {storyboard.sequence_number} 缺少已完成图片或运镜。'
                )
            duration = float(runtime_overrides.get('duration') or storyboard.duration_seconds or 8)
            duration = max(8.0, min(duration, 10.0))
            fps = int(runtime_overrides.get('fps') or 24)
            segments = plan_video_segments(
                duration,
                max_native_duration=runtime_overrides.get('max_native_duration') or 5,
                fps=fps,
                overlap_frames=8,
            )
            segment_keys = []
            previous_key = None
            for segment in segments:
                key = f'video:{storyboard.pk}:segment:{segment["index"]}'
                segment_keys.append(key)
                inputs = [{'role': 'source', 'uri': image.image_url}]
                if previous_key:
                    inputs = [{
                        'role': 'previous_final_frame',
                        'from_dependency': previous_key,
                        'frame_role': 'final',
                    }]
                specs.append({
                    'logical_key': key,
                    'capability': 'image2video',
                    'storyboard_id': storyboard.pk,
                    'segment_index': segment['index'],
                    'depends_on': [previous_key] if previous_key else [],
                    'request_parameters': {
                        'prompt': camera.get_movement_description(),
                        'image_url': image.image_url if not previous_key else '',
                        'input_artifacts': inputs,
                        'output_spec': {
                            'duration': float(segment['requested_duration_seconds']),
                            'fps': fps,
                            'resolution': runtime_overrides.get('resolution') or '1280x720',
                            'crossfade': segment['crossfade'],
                        },
                        'duration': float(segment['requested_duration_seconds']),
                        'fps': fps,
                        'seed': cls._stable_seed(project.pk, storyboard.pk, segment['index']),
                        'usage_estimate': {
                            'video_seconds': float(segment['requested_duration_seconds'])
                        },
                        'artifact_lifecycle': 'intermediate',
                    },
                })
                previous_key = key

            final_key = f'video:{storyboard.pk}:compose'
            specs.append({
                'logical_key': final_key,
                'capability': 'motion_render',
                'storyboard_id': storyboard.pk,
                'depends_on': segment_keys,
                'request_parameters': {
                    'prompt': camera.get_movement_description(),
                    'input_artifacts': [
                        {'role': 'segment', 'from_dependency': key} for key in segment_keys
                    ],
                    'output_spec': {
                        'motion': 'compose',
                        'duration': duration,
                        'fps': fps,
                        'resolution': runtime_overrides.get('resolution') or '1280x720',
                        'overlap_frames': 8,
                        'trim_exact_duration': True,
                    },
                    'duration': duration,
                    'fps': fps,
                    'usage_estimate': {'video_seconds': duration},
                    'artifact_lifecycle': 'final',
                    'source': {
                        'generated_image_id': str(image.pk),
                        'camera_movement_id': str(camera.pk),
                    },
                },
            })
        return specs

    @staticmethod
    def _profile(capability: str, profile_code: str):
        return GenerationProfile.objects.filter(
            capability=capability, key=profile_code, is_active=True
        ).first()

    @staticmethod
    def _execution_uuid(value):
        if not value:
            return uuid.uuid4()
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return uuid.uuid5(uuid.NAMESPACE_URL, str(value))

    @staticmethod
    def _idempotency_key(project_id, stage_type, execution_id, logical_key):
        raw = f'{project_id}:{stage_type}:{execution_id}:{logical_key}'
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    @staticmethod
    def _stable_seed(*parts):
        digest = hashlib.sha256(':'.join(str(part) for part in parts).encode('utf-8')).digest()
        return int.from_bytes(digest[:4], 'big') & 0x7FFFFFFF

    @classmethod
    def _json_safe(cls, value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

    @staticmethod
    def _enqueue(item_ids):
        from ..tasks import enqueue_work_item

        for item_id in item_ids:
            enqueue_work_item.apply_async(args=[item_id], queue='orchestration')
