"""跨 Celery worker 的 Redis 资源槽租约。"""

from dataclasses import dataclass
from typing import Optional

from django_redis import get_redis_connection


class ResourceLeaseUnavailable(RuntimeError):
    """指定 GPU/CPU 资源组当前没有可用槽。"""

    # 队列拥堵不是模型执行失败，绝不能据此自动切到付费 API。
    code = 'RESOURCE_BUSY'


@dataclass(frozen=True)
class ResourceLease:
    node_id: str
    group: str
    slot: int
    owner: str
    ttl_seconds: int

    @property
    def key(self):
        return f'ai:resource:{self.node_id}:{self.group}:{self.slot}'


class ResourceLeaseService:
    """使用带 TTL 的 Redis 键防止多个 worker 同时占用同一张 GPU。

    Django 租约负责全局调度公平性；Runtime Agent 自身仍必须再持有本机锁。
    两层锁的原因是 Redis 断连或 worker 崩溃时，Agent 不能因此失去最后一道
    显存保护。所有释放与续租都校验 owner，旧 worker 无法删除新租约。
    """

    RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """
    RENEW_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('expire', KEYS[1], ARGV[2])
    end
    return 0
    """

    @classmethod
    def acquire(
        cls,
        node_id,
        group: str,
        owner: str,
        capacity: int = 1,
        ttl_seconds: int = 900,
        connection=None,
    ) -> ResourceLease:
        if not owner:
            raise ValueError('资源租约必须包含 owner')
        if capacity < 1 or ttl_seconds < 1:
            raise ValueError('资源容量和 TTL 必须大于 0')
        redis = connection or get_redis_connection('default')
        for slot in range(capacity):
            lease = ResourceLease(str(node_id), group, slot, owner, ttl_seconds)
            if redis.set(lease.key, owner, nx=True, ex=ttl_seconds):
                return lease
        raise ResourceLeaseUnavailable(f'{node_id}/{group} 当前没有可用槽')

    @classmethod
    def renew(cls, lease: ResourceLease, ttl_seconds: Optional[int] = None, connection=None) -> bool:
        redis = connection or get_redis_connection('default')
        ttl = int(ttl_seconds or lease.ttl_seconds)
        return bool(redis.eval(cls.RENEW_SCRIPT, 1, lease.key, lease.owner, ttl))

    @classmethod
    def release(cls, lease: ResourceLease, connection=None) -> bool:
        redis = connection or get_redis_connection('default')
        return bool(redis.eval(cls.RELEASE_SCRIPT, 1, lease.key, lease.owner))
