"""推理领域路由、计价、预算和状态机的定向回归测试。"""

from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.models.models import ModelProvider
from apps.projects.models import Project

from ..models import (
    AIBudgetPolicy,
    GenerationProfile,
    GenerationRoute,
    GenerationTarget,
    GenerationWorkItem,
    ProviderPriceRate,
    RuntimeNode,
)
from ..services.budget import BudgetExceeded, BudgetService, InvalidReservationState
from ..services.errors import classify_inference_error
from ..services.parameters import merge_generation_parameters
from ..services.pricing import PricingService
from ..services.routing import NoRouteAvailable, RoutingService
from ..services.security import REDACTED, mask_sensitive_data
from ..services.segments import plan_video_segments
from ..services.work_items import InvalidWorkItemTransition, WorkItemService


class InferenceServiceTestCase(TestCase):
    """使用 SQLite 验证领域服务的稳定行为。"""

    def setUp(self):
        user = get_user_model().objects.create_user(username='inference-user', password='test')
        self.project = Project.objects.create(
            name='推理测试项目',
            original_topic='测试主题',
            user=user,
        )

    def create_provider(self, name='远端提供商', model_name='qwen-test', **overrides):
        values = {
            'name': name,
            'provider_type': 'llm',
            'api_url': 'https://example.invalid/v1',
            'api_key': 'test-only-key',
            'model_name': model_name,
            'is_active': True,
        }
        values.update(overrides)
        return ModelProvider.objects.create(**values)

    def test_route_priority_and_local_first_are_deterministic(self):
        provider = self.create_provider()
        node = RuntimeNode.objects.create(
            name='本地 GPU',
            node_type='local_gpu',
            health_status='healthy',
            capabilities=['llm'],
        )
        lower = GenerationRoute.objects.create(name='普通路由', capability='llm', priority=10)
        preferred = GenerationRoute.objects.create(
            name='项目路由',
            capability='llm',
            priority=20,
            match_rules={'stage': 'rewrite'},
        )
        GenerationTarget.objects.create(
            route=lower,
            name='无关目标',
            provider=provider,
            priority=999,
        )
        remote = GenerationTarget.objects.create(
            route=preferred,
            name='远端目标',
            provider=provider,
            role='paid_fallback',
            priority=100,
        )
        local = GenerationTarget.objects.create(
            route=preferred,
            name='本地目标',
            runtime_node=node,
            priority=1,
        )
        settings = SimpleNamespace(
            prefer_local=True,
            allow_paid_fallback=True,
            allow_cloud_data_transfer=True,
            cloud_authorized_by_id='authorized-user',
            cloud_authorized_at=timezone.now(),
            project_budget_cny=Decimal('10'),
            project_id=self.project.id,
            is_active=True,
            default_profile_code='balanced',
            default_route_id=None,
            capability_routes={'llm': str(preferred.id)},
        )

        selection = RoutingService.select('llm', {'stage': 'rewrite'}, settings)

        self.assertEqual(selection.route, preferred)
        self.assertEqual(selection.targets, (local, remote))

        settings.allow_cloud_data_transfer = False
        local_only = RoutingService.select('llm', {'stage': 'rewrite'}, settings)
        self.assertEqual(local_only.targets, (local,))

    def test_paid_only_route_cannot_become_automatic_primary(self):
        provider = self.create_provider(deployment_mode='api')
        route = GenerationRoute.objects.create(
            name='错误的纯付费路由', capability='llm', priority=100
        )
        GenerationTarget.objects.create(
            route=route,
            name='仅有付费目标',
            provider=provider,
            role='paid_fallback',
        )
        settings = SimpleNamespace(
            prefer_local=True,
            allow_paid_fallback=True,
            allow_cloud_data_transfer=True,
            cloud_authorized_by_id='authorized-user',
            cloud_authorized_at=timezone.now(),
            project_budget_cny=Decimal('10'),
            project_id=self.project.id,
            is_active=True,
            default_profile_code='balanced',
            default_route_id=None,
            capability_routes={'llm': str(route.id)},
        )

        with self.assertRaises(NoRouteAvailable):
            RoutingService.select('llm', {}, settings)

    def test_parameter_merge_filters_nested_controls_and_clamps_limits(self):
        profile = SimpleNamespace(
            default_parameters={'steps': 20, 'width': 512},
            hard_limits={'steps': {'min': 1, 'max': 50}, 'width': {'max': 1024}},
            allowed_parameters=['steps', 'width', 'metadata'],
        )

        result = merge_generation_parameters(
            profile=profile,
            project_overrides={'steps': 30},
            request_overrides={
                'steps': 200,
                'width': 4096,
                'endpoint': 'https://attacker.invalid',
                'metadata': {'api_key': 'secret', 'style': 'cinematic'},
                'unknown': True,
            },
        )

        self.assertEqual(result.parameters['steps'], 50)
        self.assertEqual(result.parameters['width'], 1024)
        self.assertEqual(result.parameters['metadata'], {'style': 'cinematic'})
        self.assertEqual(set(result.clamped), {'steps', 'width'})
        self.assertIn('endpoint', result.dropped)
        self.assertIn('metadata.api_key', result.dropped)
        self.assertIn('unknown', result.dropped)

    def test_price_matching_prefers_specific_pattern_and_estimates_tokens(self):
        provider = self.create_provider(model_name='qwen-plus')
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='llm',
            model_pattern='*',
            billing_unit='input_token',
            unit_size=Decimal('1000000'),
            unit_price=Decimal('9'),
            priority=1,
        )
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='llm',
            model_pattern='qwen-*',
            billing_unit='input_token',
            unit_size=Decimal('1000000'),
            unit_price=Decimal('2'),
            priority=10,
        )
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='llm',
            model_pattern='qwen-*',
            billing_unit='output_token',
            unit_size=Decimal('1000000'),
            unit_price=Decimal('4'),
            priority=10,
        )

        estimate = PricingService.estimate(
            capability='llm',
            model_name='qwen-plus',
            provider=provider,
            usage={'input_tokens': 500000, 'output_tokens': 250000},
        )

        self.assertEqual(estimate.amount, Decimal('2.000000'))
        self.assertEqual(estimate.amount_cny, Decimal('2.000000'))
        self.assertEqual(estimate.currency, 'CNY')
        self.assertEqual(len(estimate.lines), 2)

    def test_budget_reserve_is_idempotent_and_settlement_updates_ledger(self):
        policy = AIBudgetPolicy.objects.create(
            name='项目预算',
            hard_limit=Decimal('10'),
            currency='CNY',
        )

        first = BudgetService.reserve(policy, self.project, Decimal('3'), 'same-request')
        second = BudgetService.reserve(policy, self.project, Decimal('3'), 'same-request')
        self.assertEqual(first.pk, second.pk)
        policy.refresh_from_db()
        self.assertEqual(policy.reserved_amount, Decimal('3'))

        settled = BudgetService.settle(first, Decimal('2'), usage={'input_tokens': 100})
        policy.refresh_from_db()
        self.assertEqual(settled.status, 'settled')
        self.assertEqual(policy.reserved_amount, Decimal('0'))
        self.assertEqual(policy.spent_amount, Decimal('2'))

        # 已终态的旧预留只能作为账本事实读取，不能再次被当作一张新的调用许可。
        with self.assertRaises(InvalidReservationState):
            BudgetService.reserve(policy, self.project, Decimal('3'), 'same-request')

    def test_budget_hard_limit_blocks_over_reservation(self):
        policy = AIBudgetPolicy.objects.create(name='严格预算', hard_limit=Decimal('1'))

        with self.assertRaises(BudgetExceeded):
            BudgetService.reserve(policy, self.project, Decimal('1.1'), 'too-expensive')

    def test_work_item_allows_multiple_fallback_reservations_and_accumulates_cost(self):
        policy = AIBudgetPolicy.objects.create(name='多回退预算', hard_limit=Decimal('10'))
        item = GenerationWorkItem.objects.create(project=self.project, capability='image2video')
        first = BudgetService.reserve(
            policy, self.project, Decimal('2'), 'attempt-1', work_item=item
        )
        second = BudgetService.reserve(
            policy, self.project, Decimal('2'), 'attempt-2', work_item=item
        )

        BudgetService.settle(first, Decimal('0.5'))
        BudgetService.settle(second, Decimal('0.75'))

        self.assertEqual(item.budget_reservations.count(), 2)
        item.refresh_from_db()
        self.assertEqual(item.actual_cost, Decimal('1.25'))

    def test_work_item_state_machine_tracks_attempts_and_masks_errors(self):
        item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='llm',
            max_attempts=2,
        )
        item = WorkItemService.claim(item, lease_owner='worker-1', lease_seconds=60)
        item = WorkItemService.heartbeat(item, lease_owner='worker-1')
        item = WorkItemService.start(item, lease_owner='worker-1')
        self.assertEqual(item.attempt_count, 1)
        item = WorkItemService.schedule_retry(
            item,
            delay_seconds=0,
            lease_owner='worker-1',
            error={
                'category': 'network',
                'code': 'E_CONN',
                'message': 'Authorization: Bearer abcdefghijklmnop',
            },
        )
        self.assertIn(REDACTED, item.error_message)
        item = WorkItemService.retry(item)
        item = WorkItemService.claim(item, lease_owner='worker-2', lease_seconds=60)
        item = WorkItemService.start(item, lease_owner='worker-2')
        item = WorkItemService.fail(
            item,
            lease_owner='worker-2',
            error={'category': 'timeout', 'message': '超时'},
        )

        with self.assertRaises(InvalidWorkItemTransition):
            WorkItemService.retry(item)

    def test_segment_planner_uses_native_limit_overlap_and_exact_trim(self):
        segments = plan_video_segments(9, max_native_duration=5, fps=24, overlap_frames=8)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]['requested_duration_seconds'], Decimal('5'))
        self.assertEqual(segments[1]['requested_duration_seconds'], Decimal('4.333'))
        self.assertTrue(
            all(item['requested_duration_seconds'] <= Decimal('5') for item in segments)
        )
        self.assertEqual(
            sum(item['effective_duration_seconds'] for item in segments), Decimal('9')
        )
        self.assertEqual(segments[1]['previous_final_frame']['segment_index'], 0)
        self.assertEqual(segments[1]['crossfade']['frames'], 8)
        self.assertTrue(segments[-1]['trim_exact_duration']['enabled'])
        self.assertEqual(segments[-1]['end_seconds'], Decimal('9'))

    def test_error_classification_and_sensitive_masking(self):
        classification = classify_inference_error(status_code=429, message='too many requests')
        masked = mask_sensitive_data(
            {'api_key': 'secret', 'nested': {'url': 'https://x.invalid?a=1&token=abc'}}
        )

        self.assertEqual(classification.category, 'rate_limit')
        self.assertTrue(classification.retryable)
        self.assertEqual(masked['api_key'], REDACTED)
        self.assertIn(REDACTED, masked['nested']['url'])

    def test_runtime_node_and_ambiguous_reservation_contract(self):
        node = RuntimeNode.objects.create(
            name='Runtime Agent',
            node_type='local_gpu',
            agent_url='http://127.0.0.1:9100',
            access_token='plain-test-token',
            agent_version='0.1.0',
            hardware_snapshot={'gpu': 'test'},
            resource_groups={'gpu': 1},
            slot_count=2,
            reserved_slot_count=1,
            is_local=True,
        )
        policy = AIBudgetPolicy.objects.create(name='复核预算', hard_limit=Decimal('5'))
        reservation = BudgetService.reserve(policy, self.project, Decimal('1'), 'ambiguous')

        reservation = BudgetService.mark_ambiguous(reservation, 'upstream token=secret')
        reservation = BudgetService.mark_manual_review(reservation, '等待账单')

        self.assertEqual(node.agent_url, 'http://127.0.0.1:9100')
        self.assertEqual(reservation.status, 'manual_review')
        policy.refresh_from_db()
        self.assertEqual(policy.reserved_amount, Decimal('1'))
