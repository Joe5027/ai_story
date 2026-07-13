"""把统一工作项结果幂等投影到现有画布领域模型。"""

from __future__ import annotations

from typing import Any, Dict

from django.db import transaction
from django.utils import timezone

from apps.content.models import (
    CameraMovement,
    ContentRewrite,
    EditedImage,
    GeneratedImage,
    GeneratedVideo,
    MultiGridImageTask,
    MultiGridTile,
    Storyboard,
)
from apps.content.processors.asset_extraction_stage import AssetExtractionStageProcessor
from apps.projects.models import ProjectStage
from apps.projects.utils import parse_storyboard_json

from ..models import GenerationWorkItem, MediaArtifact
from .security import mask_sensitive_data


class WorkItemProjectionError(RuntimeError):
    """模型执行成功，但结果无法安全写回业务领域模型。"""

    code = 'OUTPUT_SCHEMA_INVALID'


class WorkItemResultProjector:
    """让新工作项与旧画布模型在迁移期保持一致。

    MediaArtifact 是统一产物事实源；GeneratedImage/EditedImage/GeneratedVideo
    是当前前端仍在读取的兼容投影。投影必须在工作项进入 succeeded 之前完成，
    并且正常 worker 与 Agent 恢复路径共用本服务，避免“阶段完成但画布为空”。
    """

    @classmethod
    def persist(cls, work_item, value, provider) -> Dict[str, Any]:
        try:
            with transaction.atomic():
                item = GenerationWorkItem.objects.select_for_update().select_related(
                    'project'
                ).get(pk=work_item.pk)
                if item.projection_status == 'completed':
                    return {'projected': True, 'idempotent': True}

                result = cls._project(item, value, provider)
                item.projection_status = 'completed'
                item.projection_error = ''
                item.projected_at = timezone.now()
                item.save(update_fields=[
                    'projection_status', 'projection_error', 'projected_at', 'updated_at',
                ])
                return {'projected': True, **result}
        except Exception as error:
            safe_message = str(mask_sensitive_data(str(error)))[:2000]
            GenerationWorkItem.objects.filter(pk=work_item.pk).update(
                projection_status='failed',
                projection_error=safe_message,
            )
            if isinstance(error, WorkItemProjectionError):
                raise
            raise WorkItemProjectionError(safe_message) from error

    @classmethod
    def _project(cls, item, value, provider):
        if item.capability == 'llm':
            return cls._project_llm(item, value, provider)
        if item.capability == 'text2image':
            return cls._project_image(item, provider)
        if item.capability == 'image_edit':
            return cls._project_edited_image(item, provider)
        if item.capability == 'motion_render':
            return cls._project_final_video(item, provider)
        if item.capability == 'image2video':
            # 分段视频只进入 MediaArtifact，最终合成项才投影 GeneratedVideo。
            cls._require_artifact(item, kind='video')
            return {'legacy_model': None, 'segment': item.segment_index}
        raise WorkItemProjectionError(f'不支持的业务投影能力：{item.capability}')

    @classmethod
    def _project_llm(cls, item, value, provider):
        text = cls._extract_text(value).strip()
        if not text:
            raise WorkItemProjectionError('LLM 结果没有可投影文本。')
        prompt = str((item.request_parameters or {}).get('prompt') or '')
        metadata = {
            'work_item_id': str(item.pk),
            'stage_execution_id': str(item.stage_execution_id),
        }

        if item.stage_type == 'rewrite':
            result, _ = ContentRewrite.objects.update_or_create(
                project=item.project,
                defaults={
                    'original_text': item.project.original_topic,
                    'rewritten_text': text,
                    'model_provider': provider,
                    'prompt_used': prompt,
                    'generation_metadata': metadata,
                },
            )
            return {'legacy_model': 'ContentRewrite', 'legacy_id': str(result.pk)}

        if item.stage_type == 'storyboard':
            parsed = parse_storyboard_json(text)
            scenes = parsed.get('scenes') or []
            if not scenes:
                raise WorkItemProjectionError('分镜 JSON 不包含 scenes。')
            ids = []
            for scene in scenes:
                if 'scene_number' not in scene:
                    raise WorkItemProjectionError('分镜 JSON 缺少 scene_number。')
                storyboard, _ = Storyboard.objects.update_or_create(
                    project=item.project,
                    sequence_number=scene['scene_number'],
                    defaults={
                        'scene_description': scene.get('shot_type', ''),
                        'narration_text': scene.get('narration', ''),
                        'image_prompt': scene.get('visual_prompt', ''),
                        'duration_seconds': scene.get('duration', 3.0),
                        'model_provider': provider,
                        'prompt_used': prompt,
                        'generation_metadata': {
                            **metadata,
                            'raw_scene_data': mask_sensitive_data(scene),
                        },
                    },
                )
                ids.append(str(storyboard.pk))
            return {'legacy_model': 'Storyboard', 'legacy_ids': ids}

        if item.stage_type == 'camera_movement':
            storyboard = cls._storyboard(item)
            camera, _ = CameraMovement.objects.update_or_create(
                storyboard=storyboard,
                defaults={
                    'movement_type': '',
                    'movement_params': {'description': text},
                    'model_provider': provider,
                    'prompt_used': prompt,
                    'generation_metadata': metadata,
                },
            )
            return {'legacy_model': 'CameraMovement', 'legacy_id': str(camera.pk)}

        if item.stage_type == 'asset_extraction':
            processor = AssetExtractionStageProcessor()
            normalized = processor._normalize_output(item.project, text)
            stage = ProjectStage.objects.select_for_update().get(
                project=item.project, stage_type=item.stage_type
            )
            stage.output_data = {
                **(stage.output_data or {}),
                'summary': normalized.get('summary', ''),
                'items': normalized.get('items', []),
                'raw_text': text,
                'prompt_used': prompt,
                'generation_metadata': metadata,
                'updated_at': timezone.now().isoformat(),
            }
            stage.save(update_fields=['output_data'])
            return {'legacy_model': 'ProjectStage', 'legacy_id': str(stage.pk)}

        raise WorkItemProjectionError(f'不支持的 LLM 阶段：{item.stage_type}')

    @classmethod
    def _project_image(cls, item, provider):
        storyboard = cls._storyboard(item)
        artifact = cls._require_artifact(item, kind='image')
        image = GeneratedImage.objects.filter(media_artifact=artifact).first()
        if image is None:
            image = GeneratedImage.objects.create(
                storyboard=storyboard,
                image_url=artifact.uri,
                generation_params=cls._generation_params(item),
                model_provider=provider,
                media_artifact=artifact,
                status='completed',
                file_size=artifact.file_size,
                width=artifact.width,
                height=artifact.height,
            )
        return {'legacy_model': 'GeneratedImage', 'legacy_id': str(image.pk)}

    @classmethod
    def _project_edited_image(cls, item, provider):
        storyboard = cls._storyboard(item)
        artifact = cls._require_artifact(item, kind='image')
        source = (item.request_parameters or {}).get('source') or {}
        tile = MultiGridTile.objects.filter(pk=source.get('multi_grid_tile_id')).first()
        task = MultiGridImageTask.objects.filter(pk=source.get('multi_grid_task_id')).first()
        image = EditedImage.objects.filter(media_artifact=artifact).first()
        if image is None:
            image = EditedImage.objects.create(
                storyboard=storyboard,
                multi_grid_task=task,
                multi_grid_tile=tile,
                source_stage_type=source.get('source_stage_type') or 'multi_grid_image',
                source_image_url=source.get('source_image_url') or '',
                edited_image_url=artifact.uri,
                prompt_used=str((item.request_parameters or {}).get('prompt') or ''),
                generation_params=cls._generation_params(item),
                generation_metadata={'work_item_id': str(item.pk)},
                model_provider=provider,
                media_artifact=artifact,
                status='completed',
                width=artifact.width,
                height=artifact.height,
            )
        return {'legacy_model': 'EditedImage', 'legacy_id': str(image.pk)}

    @classmethod
    def _project_final_video(cls, item, provider):
        storyboard = cls._storyboard(item)
        artifact = cls._require_artifact(item, kind='video')
        source = (item.request_parameters or {}).get('source') or {}
        image = GeneratedImage.objects.filter(pk=source.get('generated_image_id')).first()
        camera = CameraMovement.objects.filter(pk=source.get('camera_movement_id')).first()
        if image is None or camera is None:
            raise WorkItemProjectionError('最终视频缺少有效的源图片或运镜记录。')
        video = GeneratedVideo.objects.filter(media_artifact=artifact).first()
        if video is None:
            video = GeneratedVideo.objects.create(
                storyboard=storyboard,
                image=image,
                camera_movement=camera,
                video_url=artifact.uri,
                duration=float(artifact.duration_seconds or 0),
                width=artifact.width,
                height=artifact.height,
                fps=int(artifact.fps or 24),
                file_size=artifact.file_size,
                model_provider=provider,
                media_artifact=artifact,
                generation_params=cls._generation_params(item),
                status='completed',
            )
        return {'legacy_model': 'GeneratedVideo', 'legacy_id': str(video.pk)}

    @staticmethod
    def _storyboard(item):
        storyboard = Storyboard.objects.filter(
            pk=item.storyboard_id, project=item.project
        ).first()
        if storyboard is None:
            raise WorkItemProjectionError('工作项分镜不存在或不属于当前项目。')
        return storyboard

    @staticmethod
    def _require_artifact(item, kind):
        artifact = MediaArtifact.objects.filter(
            work_item=item, kind=kind, status='ready'
        ).order_by('segment_index', 'created_at').first()
        if artifact is None:
            raise WorkItemProjectionError(f'{kind} 工作项没有已校验 MediaArtifact。')
        return artifact

    @staticmethod
    def _extract_text(value):
        if isinstance(value, dict):
            result = value.get('result') or {}
            return value.get('text') or result.get('text') or ''
        return getattr(value, 'text', '') or ''

    @staticmethod
    def _generation_params(item):
        params = item.effective_parameters or item.request_parameters or {}
        return mask_sensitive_data({
            key: params.get(key)
            for key in ('seed', 'profile', 'output_spec', 'width', 'height', 'duration', 'fps')
            if params.get(key) is not None
        })
