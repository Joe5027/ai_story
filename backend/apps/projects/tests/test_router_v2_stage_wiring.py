from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.projects.models import Project, ProjectStage
from apps.projects.tasks import execute_llm_stage


class RouterV2StageWiringTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='v2-wiring-user', password='test'
        )
        self.project = Project.objects.create(
            name='V2 接线测试', original_topic='测试故事', user=self.user
        )
        ProjectStage.objects.create(
            project=self.project, stage_type='rewrite', status='pending'
        )

    @override_settings(AI_ROUTER_V2_ENABLED=True)
    @patch('apps.projects.tasks.LLMStageProcessor')
    @patch('apps.projects.tasks.StageWorkItemPlanner.plan_stage')
    @patch('apps.projects.tasks.is_stage_template_enabled', return_value=True)
    @patch('apps.projects.tasks.RedisStreamPublisher')
    def test_enabled_stage_plans_work_items_and_never_enters_old_processor(
        self, publisher_class, _enabled, plan_stage, old_processor
    ):
        work_item = SimpleNamespace(pk='work-item-1')
        plan_stage.return_value = SimpleNamespace(
            stage_execution_id='execution-1', work_items=(work_item,)
        )
        publisher_class.return_value = MagicMock()

        with patch('apps.projects.tasks._unregister_project_task'):
            result = execute_llm_stage.run(
                str(self.project.pk), 'rewrite', {}, self.user.pk
            )

        self.assertTrue(result['queued'])
        self.assertEqual(result['work_item_ids'], ['work-item-1'])
        plan_stage.assert_called_once()
        old_processor.assert_not_called()

    @override_settings(AI_ROUTER_V2_ENABLED=False)
    @patch('apps.projects.tasks.LLMStageProcessor')
    @patch('apps.projects.tasks.StageWorkItemPlanner.plan_stage')
    @patch('apps.projects.tasks.is_stage_template_enabled', return_value=True)
    @patch('apps.projects.tasks.RedisStreamPublisher')
    def test_disabled_stage_keeps_legacy_processor_compatibility(
        self, publisher_class, _enabled, plan_stage, old_processor
    ):
        processor = old_processor.return_value
        processor.process_stream.return_value = [{
            'type': 'done', 'full_text': '完成', 'metadata': {}
        }]
        publisher_class.return_value = MagicMock()

        with patch('apps.projects.tasks._unregister_project_task'):
            result = execute_llm_stage.run(
                str(self.project.pk), 'rewrite', {}, self.user.pk
            )

        self.assertTrue(result['success'])
        self.assertFalse(result.get('queued', False))
        plan_stage.assert_not_called()
        old_processor.assert_called_once_with(stage_type='rewrite')
