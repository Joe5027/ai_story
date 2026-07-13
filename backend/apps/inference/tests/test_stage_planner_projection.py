import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.content.models import (
    CameraMovement,
    ContentRewrite,
    GeneratedImage,
    GeneratedVideo,
    Storyboard,
)
from apps.models.models import ModelProvider
from apps.projects.models import Project, ProjectStage
from apps.prompts.models import PromptTemplate, PromptTemplateSet
from core.ai_client.base import AIResponse

from ..models import GenerationWorkItem, MediaArtifact
from ..services.projection import WorkItemResultProjector
from ..services.scheduler import WorkItemScheduler
from ..services.stage_planner import StageWorkItemPlanner


@override_settings(AI_ROUTER_V2_ENABLED=True)
class StageWorkItemPlannerTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='planner-user', password='test'
        )
        self.project = Project.objects.create(
            name='工作项规划测试', original_topic='一段测试故事', user=self.user
        )
        self.llm = ModelProvider.objects.get(name='Mock LLM API')
        self.image_provider = ModelProvider.objects.get(name='Mock Text2Image API')
        self.video_provider = ModelProvider.objects.get(name='Mock Image2Video API')
        template_set = PromptTemplateSet.objects.filter(is_default=True).first()
        PromptTemplate.objects.update_or_create(
            template_set=template_set,
            stage_type='rewrite',
            defaults={
                'template_content': '请改写以下故事，保留人物关系。',
                'model_provider': self.llm,
                'is_active': True,
            },
        )
        self.project.prompt_template_set = template_set
        self.project.save(update_fields=['prompt_template_set'])
        for stage_type in (
            'rewrite', 'camera_movement', 'image_generation', 'video_generation'
        ):
            ProjectStage.objects.get_or_create(project=self.project, stage_type=stage_type)

    def storyboard(self, number):
        return Storyboard.objects.create(
            project=self.project,
            sequence_number=number,
            scene_description=f'场景 {number}',
            narration_text=f'旁白 {number}',
            image_prompt=f'画面 {number}',
            duration_seconds=8,
        )

    def test_rewrite_plan_is_idempotent_and_uses_automatic_route(self):
        execution_id = uuid.uuid4()

        first = StageWorkItemPlanner.plan_stage(
            project=self.project,
            stage_type='rewrite',
            stage_execution_id=execution_id,
            enqueue=False,
        )
        second = StageWorkItemPlanner.plan_stage(
            project=self.project,
            stage_type='rewrite',
            stage_execution_id=execution_id,
            enqueue=False,
        )

        self.assertEqual(first.created_count, 1)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(first.work_items[0].pk, second.work_items[0].pk)
        self.assertIsNone(first.work_items[0].provider_id)
        self.assertTrue(first.work_items[0].route_snapshot['automatic_route'])
        self.assertIsInstance(first.work_items[0].request_parameters['max_tokens'], int)

    def test_manual_provider_plan_replays_after_cost_annotations(self):
        provider = ModelProvider.objects.create(
            name='规划显式 API',
            provider_type='llm',
            deployment_mode='api',
            api_url='https://example.com/v1',
            api_key='paid-secret',
            model_name='paid-llm',
            executor_class='core.ai_client.openai_client.OpenAIClient',
        )
        execution_id = uuid.uuid4()
        first = StageWorkItemPlanner.plan_stage(
            project=self.project,
            stage_type='rewrite',
            stage_execution_id=execution_id,
            enqueue=False,
            explicit_provider=provider,
            manual_api=True,
        )
        item = first.work_items[0]
        item.request_parameters = {
            **item.request_parameters,
            'manual_api': True,
            'confirmed_max_cost_cny': '1.000000',
        }
        item.save(update_fields=['request_parameters'])

        replay = StageWorkItemPlanner.plan_stage(
            project=self.project,
            stage_type='rewrite',
            stage_execution_id=execution_id,
            enqueue=False,
            explicit_provider=provider,
            manual_api=True,
        )

        self.assertEqual(replay.created_count, 0)
        self.assertEqual(replay.work_items[0].provider, provider)
        self.assertFalse(replay.work_items[0].route_snapshot['automatic_route'])

    def test_camera_is_split_per_storyboard(self):
        first = self.storyboard(1)
        second = self.storyboard(2)

        plan = StageWorkItemPlanner.plan_stage(
            project=self.project,
            stage_type='camera_movement',
            enqueue=False,
        )

        self.assertEqual(len(plan.work_items), 2)
        self.assertEqual(
            {item.storyboard_id for item in plan.work_items}, {first.pk, second.pk}
        )

    def test_video_segments_are_strictly_chained_before_final_compose(self):
        storyboard = self.storyboard(1)
        image = GeneratedImage.objects.create(
            storyboard=storyboard,
            image_url='/storage/source.png',
            generation_params={},
            model_provider=self.image_provider,
            status='completed',
        )
        camera = CameraMovement.objects.create(
            storyboard=storyboard,
            movement_params={'description': '缓慢推进'},
            model_provider=self.llm,
        )

        plan = StageWorkItemPlanner.plan_stage(
            project=self.project,
            stage_type='video_generation',
            runtime_overrides={'duration': 8, 'max_native_duration': 5},
            enqueue=False,
        )

        segments = [item for item in plan.work_items if item.capability == 'image2video']
        compose = [item for item in plan.work_items if item.capability == 'motion_render'][0]
        self.assertGreaterEqual(len(segments), 2)
        self.assertEqual(segments[0].depends_on.count(), 0)
        self.assertEqual(list(segments[1].depends_on.all()), [segments[0]])
        self.assertEqual(set(compose.depends_on.all()), set(segments))
        self.assertEqual(
            compose.request_parameters['source']['generated_image_id'], str(image.pk)
        )
        self.assertEqual(
            compose.request_parameters['source']['camera_movement_id'], str(camera.pk)
        )

    def test_waiting_dependency_is_not_selected_until_parent_succeeds(self):
        parent = GenerationWorkItem.objects.create(
            project=self.project,
            capability='image2video',
            stage_type='video_generation',
            idempotency_key='dependency-parent',
            status='waiting',
        )
        child = GenerationWorkItem.objects.create(
            project=self.project,
            capability='motion_render',
            stage_type='video_generation',
            idempotency_key='dependency-child',
            status='waiting',
        )
        child.depends_on.add(parent)

        waiting = WorkItemScheduler._fair_waiting_ids(
            parent.created_at, limit=10
        )
        self.assertIn(parent.pk, waiting)
        self.assertNotIn(child.pk, waiting)

        GenerationWorkItem.objects.filter(pk=parent.pk).update(status='succeeded')
        waiting = WorkItemScheduler._fair_waiting_ids(
            parent.created_at, limit=10
        )
        self.assertIn(child.pk, waiting)


class WorkItemProjectionTestCase(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='projection-user', password='test')
        self.project = Project.objects.create(
            name='投影测试', original_topic='原始文本', user=user
        )
        self.llm = ModelProvider.objects.get(name='Mock LLM API')
        self.image_provider = ModelProvider.objects.get(name='Mock Text2Image API')

    def test_rewrite_projection_is_idempotent(self):
        item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='llm',
            stage_type='rewrite',
            provider=self.llm,
            idempotency_key='projection-rewrite',
            request_parameters={'prompt': '改写'},
        )

        first = WorkItemResultProjector.persist(
            item, AIResponse(success=True, text='改写后的文本'), self.llm
        )
        second = WorkItemResultProjector.persist(
            item, AIResponse(success=True, text='不会重复写入'), self.llm
        )

        rewrite = ContentRewrite.objects.get(project=self.project)
        item.refresh_from_db()
        self.assertEqual(rewrite.rewritten_text, '改写后的文本')
        self.assertTrue(first['projected'])
        self.assertTrue(second['idempotent'])
        self.assertEqual(item.projection_status, 'completed')

    def test_image_projection_links_media_artifact_without_duplicate(self):
        storyboard = Storyboard.objects.create(
            project=self.project,
            sequence_number=1,
            scene_description='场景',
            narration_text='旁白',
            image_prompt='画面',
        )
        item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='text2image',
            stage_type='image_generation',
            storyboard_id=storyboard.pk,
            provider=self.image_provider,
            idempotency_key='projection-image',
        )
        artifact = MediaArtifact.objects.create(
            work_item=item,
            project=self.project,
            kind='image',
            status='ready',
            lifecycle='final',
            uri='/storage/generated.png',
            width=1024,
            height=1024,
        )

        WorkItemResultProjector.persist(item, {'artifacts': []}, self.image_provider)
        WorkItemResultProjector.persist(item, {'artifacts': []}, self.image_provider)

        images = GeneratedImage.objects.filter(media_artifact=artifact)
        self.assertEqual(images.count(), 1)
        self.assertEqual(images.get().status, 'completed')

    def test_dependency_placeholder_resolves_to_verified_artifact(self):
        parent = GenerationWorkItem.objects.create(
            project=self.project,
            capability='image2video',
            idempotency_key='resolve-parent',
            status='succeeded',
            route_snapshot={'logical_key': 'segment-0'},
        )
        artifact = MediaArtifact.objects.create(
            work_item=parent,
            project=self.project,
            kind='video',
            status='ready',
            lifecycle='intermediate',
            uri='/storage/segment-0.mp4',
        )
        child = GenerationWorkItem.objects.create(
            project=self.project,
            capability='image2video',
            idempotency_key='resolve-child',
            request_parameters={
                'input_artifacts': [{
                    'role': 'previous_final_frame',
                    'from_dependency': 'segment-0',
                }]
            },
        )
        child.depends_on.add(parent)

        parameters = WorkItemScheduler._invocation_parameters(child)

        self.assertEqual(parameters['image_url'], artifact.uri)
        self.assertEqual(parameters['input_artifacts'][0]['artifact_id'], str(artifact.pk))
