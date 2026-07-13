import os
import socket
import time
import uuid
from urllib.parse import urlparse

from django.conf import settings
from django.test import SimpleTestCase

from core.redis import RedisStreamPublisher, RedisStreamSubscriber


def redis_endpoint():
    parsed = urlparse(settings.REDIS_PUBSUB_URL)
    host = parsed.hostname or getattr(settings, 'REDIS_HOST', 'localhost')
    port = parsed.port or getattr(settings, 'REDIS_PORT', 6379)
    return host, int(port)


def redis_is_available():
    host, port = redis_endpoint()
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


class RedisPubSubSmokeTests(SimpleTestCase):
    def setUp(self):
        if redis_is_available():
            return

        host, port = redis_endpoint()
        message = f'Redis unavailable at {host}:{port}'
        if os.getenv('REQUIRE_REDIS') == '1':
            self.fail(message)
        self.skipTest(message)

    def wait_for_message(self, subscriber, expected_type):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            message = subscriber.get_message(timeout=0.2)
            if not message:
                continue
            if message.get('type') == expected_type:
                return message

        self.fail(f'Timed out waiting for Redis message type: {expected_type}')

    def test_stage_pubsub_round_trip(self):
        project_id = f'redis-smoke-{uuid.uuid4().hex}'
        subscriber = RedisStreamSubscriber(project_id, 'rewrite')
        publisher = RedisStreamPublisher(project_id, 'rewrite')

        try:
            subscriber.subscribe()
            subscriber.get_message(timeout=0.2)

            self.assertTrue(
                publisher.publish_stage_update(
                    status='processing',
                    progress=25,
                    message='redis smoke',
                )
            )
            update = self.wait_for_message(subscriber, 'stage_update')
            self.assertEqual(update['project_id'], project_id)
            self.assertEqual(update['stage'], 'rewrite')
            self.assertEqual(update['status'], 'processing')
            self.assertEqual(update['progress'], 25)

            self.assertTrue(publisher.publish_done(metadata={'source': 'redis-smoke'}))
            done = self.wait_for_message(subscriber, 'done')
            self.assertEqual(done['metadata']['source'], 'redis-smoke')
            self.assertTrue(str(done['channel']).endswith(':stage:rewrite'))
        finally:
            publisher.close()
            subscriber.close()
