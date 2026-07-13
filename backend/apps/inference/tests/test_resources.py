from unittest import TestCase

from apps.inference.services.resources import (
    ResourceLeaseService,
    ResourceLeaseUnavailable,
)


class FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script, key_count, key, owner, *args):
        if self.values.get(key) != owner:
            return 0
        if 'del' in script:
            del self.values[key]
        return 1


class ResourceLeaseServiceTests(TestCase):
    def test_capacity_and_owner_safe_release(self):
        redis = FakeRedis()
        first = ResourceLeaseService.acquire('node-1', 'gpu', 'worker-1', connection=redis)
        with self.assertRaises(ResourceLeaseUnavailable):
            ResourceLeaseService.acquire('node-1', 'gpu', 'worker-2', connection=redis)

        forged = type(first)(first.node_id, first.group, first.slot, 'worker-2', first.ttl_seconds)
        self.assertFalse(ResourceLeaseService.release(forged, connection=redis))
        self.assertTrue(ResourceLeaseService.release(first, connection=redis))
        second = ResourceLeaseService.acquire('node-1', 'gpu', 'worker-2', connection=redis)
        self.assertEqual(second.owner, 'worker-2')
