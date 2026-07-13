from django.conf import settings
from django.test import SimpleTestCase

from config.celery_app import app


class CeleryContractTests(SimpleTestCase):
    def test_celery_uses_configured_redis_urls(self):
        self.assertEqual(app.conf.broker_url, settings.CELERY_BROKER_URL)
        self.assertEqual(app.conf.result_backend, settings.CELERY_RESULT_BACKEND)
        self.assertEqual(app.conf.accept_content, ['json'])
        self.assertEqual(app.conf.task_serializer, 'json')
        self.assertEqual(app.conf.result_serializer, 'json')

    def test_project_workflow_tasks_are_registered(self):
        import apps.projects.tasks  # noqa: F401

        expected_tasks = {
            'apps.projects.tasks.execute_llm_stage',
            'apps.projects.tasks.execute_text2image_stage',
            'apps.projects.tasks.execute_multi_grid_image_stage',
            'apps.projects.tasks.execute_image_edit_stage',
            'apps.projects.tasks.execute_image2video_stage',
            'apps.projects.tasks.generate_jianying_draft',
            'apps.projects.tasks.run_full_pipeline_task',
        }

        missing_tasks = expected_tasks.difference(app.tasks.keys())
        self.assertFalse(missing_tasks, f'Missing Celery tasks: {sorted(missing_tasks)}')
