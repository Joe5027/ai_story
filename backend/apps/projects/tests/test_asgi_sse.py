import asyncio
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.projects.asgi_sse import ProjectSSEASGIApplication, match_sse_path


def decode_body(message):
    body = message.get("body", b"").decode("utf-8").strip()
    if not body.startswith("data: "):
        return None
    return json.loads(body.removeprefix("data: "))


class FakeRedisSubscriber:
    messages = []
    instances = []

    @classmethod
    def configure(cls, messages):
        cls.messages = list(messages)
        cls.instances = []

    def __init__(self, project_id, stage_name=None):
        self.project_id = project_id
        self.stage_name = stage_name
        self.pending = list(self.messages)
        self.closed = False
        self.instances.append(self)

    def get_message(self, timeout=1.0):
        if self.pending:
            return self.pending.pop(0)
        return None

    def close(self):
        self.closed = True


class ProjectSSEASGIApplicationTests(SimpleTestCase):
    def test_path_matching(self):
        self.assertEqual(
            match_sse_path("/api/v1/projects/sse/projects/p1/stages/rewrite/"),
            ("p1", "rewrite"),
        )
        self.assertEqual(
            match_sse_path("/api/v1/projects/sse/projects/p1/"),
            ("p1", None),
        )
        self.assertIsNone(match_sse_path("/api/v1/projects/projects/p1/"))

    async def test_all_stage_stream_waits_for_pipeline_terminal_event(self):
        FakeRedisSubscriber.configure([
            {"type": "done", "project_id": "p1", "stage": "rewrite"},
            {"type": "pipeline_done", "project_id": "p1", "stage": "pipeline"},
        ])
        delegated = []

        async def django_application(scope, receive, send):
            delegated.append(scope)

        receive_started = False

        async def receive():
            nonlocal receive_started
            if not receive_started:
                receive_started = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()

        sent = []

        async def send(message):
            sent.append(message)

        app = ProjectSSEASGIApplication(django_application)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/projects/sse/projects/p1/",
        }

        with patch("apps.projects.asgi_sse.RedisStreamSubscriber", FakeRedisSubscriber):
            await app(scope, receive, send)

        payloads = [payload for payload in map(decode_body, sent) if payload]
        self.assertEqual(
            [payload["type"] for payload in payloads],
            ["connected", "done", "pipeline_done"],
        )
        self.assertEqual(sent[0]["type"], "http.response.start")
        self.assertEqual(sent[0]["status"], 200)
        self.assertFalse(sent[-1]["more_body"])
        self.assertTrue(FakeRedisSubscriber.instances[0].closed)
        self.assertEqual(delegated, [])

    async def test_non_sse_request_delegates_to_django(self):
        delegated = []

        async def django_application(scope, receive, send):
            delegated.append(scope["path"])

        app = ProjectSSEASGIApplication(django_application)
        await app(
            {"type": "http", "method": "GET", "path": "/api/v1/projects/projects/"},
            None,
            None,
        )

        self.assertEqual(delegated, ["/api/v1/projects/projects/"])
