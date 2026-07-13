"""只在 PostgreSQL 等支持行锁的数据库上执行的并发安全测试。"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.models.models import ModelProvider
from apps.projects.models import Project

from ..models import (
    AIBudgetPolicy,
    BudgetReservation,
    GenerationWorkItem,
    ProjectAISettings,
    ProviderPriceRate,
)
from ..services.gates import PaidCallDenied, PaidCallGate
from ..services.work_items import InvalidWorkItemTransition, WorkItemService


@skipUnlessDBFeature('has_select_for_update')
class PostgreSQLConcurrencyTestCase(TransactionTestCase):
    """验证生产行锁能够阻止并发超预算和重复领取。

    SQLite 不提供与生产 PostgreSQL 等价的 ``SELECT ... FOR UPDATE`` 语义，
    因此这些测试只在支持行锁的数据库运行，避免把 SQLite 的全库写锁误当成
    生产并发证据。
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='postgres-concurrency-user',
            password='test-only',
        )
        self.project = Project.objects.create(
            name='PostgreSQL 并发测试项目',
            original_topic='仅用于一次性测试数据库',
            user=self.user,
        )

    @staticmethod
    def _run_concurrently(worker, count=20):
        """让全部 worker 在创建完成后同时进入数据库临界区。"""

        start = Event()

        def synchronized(index):
            close_old_connections()
            try:
                if not start.wait(timeout=10):
                    raise RuntimeError('并发测试启动信号超时')
                return worker(index)
            finally:
                # Django 连接是线程本地对象；close_old_connections 只关闭失效连接，
                # 对刚完成查询的健康 PostgreSQL 会话并不生效，必须主动全部关闭，
                # 否则测试结束时临时数据库仍被占用而无法删除。
                connections.close_all()

        with ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(synchronized, index) for index in range(count)]
            start.set()
            return [future.result(timeout=30) for future in futures]

    def test_twenty_paid_reservations_cannot_cross_project_daily_or_monthly_limit(self):
        """20 个并发付费预留最多只能消费共同上限内的 5 个名额。"""

        provider = ModelProvider.objects.create(
            name='并发测试 API',
            provider_type='llm',
            deployment_mode='api',
            api_url='https://example.invalid/v1',
            api_key='test-only-key',
            model_name='concurrency-model',
            is_active=True,
        )
        policy = AIBudgetPolicy.objects.create(
            name='并发硬上限',
            hard_limit=Decimal('5'),
            daily_limit_cny=Decimal('5'),
            monthly_limit_cny=Decimal('5'),
        )
        ProjectAISettings.objects.create(
            project=self.project,
            budget_policy=policy,
            allow_cloud_data_transfer=True,
            allow_paid_fallback=True,
            cloud_authorized_by=self.user,
            cloud_authorized_at=timezone.now(),
            project_budget_cny=Decimal('5'),
        )
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='llm',
            model_pattern='concurrency-model',
            billing_unit='request',
            unit_size=Decimal('1'),
            unit_price=Decimal('1'),
            currency='CNY',
            exchange_rate_to_cny=Decimal('1'),
        )

        project_id = self.project.pk
        provider_id = provider.pk

        def reserve(index):
            project = Project.objects.get(pk=project_id)
            current_provider = ModelProvider.objects.get(pk=provider_id)
            try:
                PaidCallGate.reserve(
                    project=project,
                    provider=current_provider,
                    capability='llm',
                    usage={'request_count': 1},
                    idempotency_key=f'postgres-budget-{index}',
                    automatic_fallback=True,
                )
                return 'reserved'
            except PaidCallDenied:
                return 'denied'

        results = self._run_concurrently(reserve)

        self.assertEqual(results.count('reserved'), 5)
        self.assertEqual(results.count('denied'), 15)
        policy.refresh_from_db()
        self.assertEqual(policy.reserved_amount, Decimal('5'))
        self.assertEqual(
            BudgetReservation.objects.filter(policy=policy, status='active').count(),
            5,
        )

    def test_twenty_workers_can_only_claim_one_work_item_once(self):
        """同一 waiting 工作项在 20 个竞争者中只能产生一个租约。"""

        item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='llm',
            idempotency_key='postgres-single-claim',
        )
        item_id = item.pk

        def claim(index):
            current = GenerationWorkItem.objects.get(pk=item_id)
            try:
                WorkItemService.claim(
                    current,
                    lease_owner=f'postgres-worker-{index}',
                    lease_seconds=60,
                )
                return 'claimed'
            except InvalidWorkItemTransition:
                return 'rejected'

        results = self._run_concurrently(claim)

        self.assertEqual(results.count('claimed'), 1)
        self.assertEqual(results.count('rejected'), 19)
        item.refresh_from_db()
        self.assertEqual(item.status, 'leased')
        self.assertTrue(item.lease_owner.startswith('postgres-worker-'))
