import json
from unittest.mock import patch

from django.http import StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase

from apps.projects.sse_views import ProjectAllStagesSSEView, ProjectStageSSEView


def decode_sse_data(chunk):
    line = chunk.decode('utf-8').strip()
    assert line.startswith('data: ')
    return json.loads(line.removeprefix('data: '))


class FakeRedisSubscriber:
    messages_by_key = {}
    instances = []

    @classmethod
    def configure(cls, messages_by_key):
        cls.messages_by_key = messages_by_key
        cls.instances = []

    def __init__(self, project_id, stage_name=None):
        self.project_id = project_id
        self.stage_name = stage_name
        self.messages = list(self.messages_by_key.get((project_id, stage_name), []))
        self.closed = False
        self.instances.append(self)

    def get_message(self, timeout=1.0):
        if self.messages:
            return self.messages.pop(0)
        return None

    def close(self):
        self.closed = True


class ProjectStageSSEViewTests(SimpleTestCase):
    def test_stage_view_returns_sse_headers(self):
        request = RequestFactory().get('/api/v1/projects/sse/projects/project-1/stages/rewrite/')

        with patch.object(
            ProjectStageSSEView,
            '_create_event_stream',
            return_value=iter([b'data: {"type": "connected"}\n\n']),
        ):
            response = ProjectStageSSEView.as_view()(
                request,
                project_id='project-1',
                stage_name='rewrite',
            )

        self.assertIsInstance(response, StreamingHttpResponse)
        self.assertIn('text/event-stream', response['Content-Type'])
        self.assertEqual(response['Cache-Control'], 'no-cache, no-transform')
        self.assertEqual(response['X-Accel-Buffering'], 'no')
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')

    def test_stage_stream_emits_connected_messages_and_closes_on_done(self):
        FakeRedisSubscriber.configure({
            ('project-1', 'rewrite'): [
                {
                    'type': 'stage_update',
                    'project_id': 'project-1',
                    'stage': 'rewrite',
                    'status': 'processing',
                },
                {
                    'type': 'done',
                    'project_id': 'project-1',
                    'stage': 'rewrite',
                    'full_text': 'finished',
                },
            ],
        })

        with patch('apps.projects.sse_views.RedisStreamSubscriber', FakeRedisSubscriber):
            chunks = list(ProjectStageSSEView()._create_event_stream('project-1', 'rewrite'))

        messages = [decode_sse_data(chunk) for chunk in chunks]
        self.assertEqual([message['type'] for message in messages], ['connected', 'stage_update', 'done'])
        self.assertEqual(messages[0]['project_id'], 'project-1')
        self.assertEqual(messages[0]['stage'], 'rewrite')
        self.assertTrue(FakeRedisSubscriber.instances[0].closed)

    def test_all_stages_stream_waits_for_pipeline_terminal_message(self):
        FakeRedisSubscriber.configure({
            ('project-1', None): [
                {
                    'type': 'done',
                    'project_id': 'project-1',
                    'stage': 'rewrite',
                },
                {
                    'type': 'pipeline_done',
                    'project_id': 'project-1',
                    'stage': 'pipeline',
                },
            ],
        })

        with patch('apps.projects.sse_views.RedisStreamSubscriber', FakeRedisSubscriber):
            chunks = list(ProjectAllStagesSSEView()._create_event_stream('project-1'))

        messages = [decode_sse_data(chunk) for chunk in chunks]
        self.assertEqual([message['type'] for message in messages], ['connected', 'done', 'pipeline_done'])
        self.assertTrue(FakeRedisSubscriber.instances[0].closed)
