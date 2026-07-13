"""持久化调度、恢复、取消和安全清理的定向回归测试。"""

import hashlib
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.models.models import ModelProvider
from apps.projects.models import Project, ProjectStage
from core.ai_client.base import AIResponse

from .. import tasks as inference_tasks
from ..models import (
    AIBudgetPolicy,
    BudgetReservation,
    GenerationWorkItem,
    MediaArtifact,
    ProjectAISettings,
    ProviderPriceRate,
    RuntimeNode,
)
from ..services.gates import PaidCallDenied, PaidCallGate
from ..services.hybrid import HybridExecutionFailed, HybridInferenceService
from ..services.scheduler import (
    ArtifactRetentionService,
    RuntimeAgentControl,
    WorkItemScheduler,
    queue_for_capability,
)
from ..services.resources import ResourceLeaseUnavailable
from ..services.work_items import WorkItemService


class FakeHybridService:
    calls = 0

    @classmethod
    def execute(cls, **kwargs):
        cls.calls += 1
        provider = kwargs['explicit_provider']
        response = AIResponse(
            success=True,
            text='完成',
            data=[{'uri': 'storage/generated/mock.txt', 'content_type': 'text/plain'}],
        )
        log = SimpleNamespace(
            input_tokens=12,
            output_tokens=8,
            image_count=0,
            video_seconds=0,
            settled_cost=Decimal('0'),
        )
        return SimpleNamespace(
            value=response,
            provider=provider,
            target=SimpleNamespace(pk=None),
            usage_log=log,
        )


class FakeAgentControl:
    def __init__(self, job=None):
        self.job = job or {'status': 'succeeded', 'artifacts': []}
        self.cancelled = []

    def get_job(self, node, job_id):
        return self.job

    def cancel_job(self, node, job_id):
        self.cancelled.append((node.pk, job_id))
        return {'status': 'cancel_requested'}

    def materialize_artifacts(self, node, job, work_item):
        return job.get('artifacts') or []


class SchedulerTestCase(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='scheduler-user', password='test')
        self.user = user
        self.project = Project.objects.create(
            name='调度测试项目', original_topic='测试', user=user
        )
        self.provider = ModelProvider.objects.create(
            name='Mock LLM',
            provider_type='llm',
            deployment_mode='mock',
            executor_class='core.ai_client.mock_llm_client.MockLLMClient',
            model_name='mock-llm',
        )

    def create_item(self, **overrides):
        values = {
            'project': self.project,
            'capability': 'llm',
            'stage_type': 'rewrite',
            'provider': self.provider,
            'idempotency_key': f'item-{GenerationWorkItem.objects.count()}',
            'request_parameters': {'prompt': '请生成结构化结果'},
            'max_attempts': 2,
        }
        values.update(overrides)
        return GenerationWorkItem.objects.create(**values)

    def create_paid_setup(self, suffix='default'):
        provider = ModelProvider.objects.create(
            name=f'付费防重放模型-{suffix}',
            provider_type='llm',
            deployment_mode='api',
            executor_class='core.ai_client.openai_client.OpenAIClient',
            api_url='https://example.invalid/v1',
            api_key='test-only',
            model_name=f'paid-replay-{suffix}',
        )
        policy = AIBudgetPolicy.objects.create(
            name=f'付费防重放预算-{suffix}',
            hard_limit=Decimal('100'),
            daily_limit_cny=Decimal('100'),
            monthly_limit_cny=Decimal('100'),
        )
        ProjectAISettings.objects.create(
            project=self.project,
            budget_policy=policy,
            allow_paid_fallback=True,
            allow_cloud_data_transfer=True,
            cloud_authorized_by=self.user,
            cloud_authorized_at=timezone.now(),
            project_budget_cny=Decimal('100'),
        )
        ProviderPriceRate.objects.create(
            provider=provider,
            capability='llm',
            model_pattern=provider.model_name,
            billing_unit='request',
            unit_price=Decimal('1'),
        )
        return provider, policy

    def make_expired_running(self, item):
        past = timezone.now() - timedelta(minutes=10)
        leased = WorkItemService.claim(item, 'worker-old', lease_seconds=10, now=past)
        return WorkItemService.start(leased, 'worker-old', now=past)

    def test_dispatch_uses_lease_and_terminal_redelivery_is_idempotent(self):
        FakeHybridService.calls = 0
        stage = ProjectStage.objects.create(
            project=self.project, stage_type='rewrite', status='processing'
        )
        item = self.create_item(request_parameters={
            'prompt': '测试', 'artifact_lifecycle': 'final'
        })
        scheduler = WorkItemScheduler(hybrid_service=FakeHybridService)

        first = scheduler.dispatch(item.pk, 'worker-1')
        second = scheduler.dispatch(item.pk, 'worker-redelivery')

        item.refresh_from_db()
        stage.refresh_from_db()
        self.assertEqual(first['status'], 'succeeded')
        self.assertEqual(second['status'], 'succeeded')
        self.assertTrue(second['idempotent'])
        self.assertEqual(FakeHybridService.calls, 1)
        self.assertEqual(item.attempt_count, 1)
        self.assertEqual(item.usage['input_tokens'], 12)
        self.assertEqual(item.artifacts.count(), 1)
        self.assertEqual(item.artifacts.get().lifecycle, 'final')
        self.assertEqual(stage.status, 'completed')

    def test_reconciler_releases_expired_lease_and_due_retry(self):
        past = timezone.now() - timedelta(minutes=10)
        leased_item = self.create_item(idempotency_key='leased')
        WorkItemService.claim(leased_item, 'dead-worker', lease_seconds=1, now=past)
        retry_item = self.create_item(idempotency_key='retry')
        GenerationWorkItem.objects.filter(pk=retry_item.pk).update(
            status='retry_wait', next_retry_at=past
        )
        enqueued = []

        stats = WorkItemScheduler().reconcile(enqueue=enqueued.append)

        leased_item.refresh_from_db()
        retry_item.refresh_from_db()
        self.assertEqual(leased_item.status, 'waiting')
        self.assertEqual(retry_item.status, 'waiting')
        self.assertEqual(set(enqueued), {str(leased_item.pk), str(retry_item.pk)})
        self.assertEqual(stats['released_leases'], 1)
        self.assertEqual(stats['retry_released'], 1)

    def test_waiting_queue_round_robins_projects_at_same_priority(self):
        other_project = Project.objects.create(
            name='另一个项目', original_topic='测试', user=self.user
        )
        for index in range(3):
            self.create_item(idempotency_key=f'fair-a-{index}', priority=5)
            GenerationWorkItem.objects.create(
                project=other_project,
                capability='llm',
                stage_type='rewrite',
                provider=self.provider,
                idempotency_key=f'fair-b-{index}',
                priority=5,
            )
        enqueued = []

        WorkItemScheduler().reconcile(limit=4, enqueue=enqueued.append)

        actual_projects = [
            GenerationWorkItem.objects.get(pk=item_id).project_id for item_id in enqueued
        ]
        self.assertEqual(actual_projects, [
            self.project.pk, other_project.pk, self.project.pk, other_project.pk,
        ])

    def test_reconciler_never_resubmits_ambiguous_paid_work(self):
        paid = ModelProvider.objects.create(
            name='付费 LLM',
            provider_type='llm',
            deployment_mode='api',
            executor_class='core.ai_client.openai_client.OpenAIClient',
            api_url='https://example.invalid/v1',
            api_key='test-only',
            model_name='paid-model',
        )
        item = self.create_item(provider=paid, idempotency_key='paid-unknown')
        running = self.make_expired_running(item)
        policy = AIBudgetPolicy.objects.create(name='预算', hard_limit=Decimal('10'))
        reservation = BudgetReservation.objects.create(
            policy=policy,
            project=self.project,
            work_item=running,
            idempotency_key='paid-reservation',
            estimated_amount=Decimal('1'),
            reserved_amount=Decimal('1'),
        )
        enqueued = []

        stats = WorkItemScheduler().reconcile(enqueue=enqueued.append)

        running.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(running.status, 'failed')
        self.assertEqual(running.error_code, 'PAID_RESULT_AMBIGUOUS')
        self.assertEqual(reservation.status, 'manual_review')
        self.assertEqual(enqueued, [])
        self.assertEqual(stats['paid_manual_review'], 1)

    def test_reconciler_uses_agent_job_as_recovery_truth(self):
        node = RuntimeNode.objects.create(
            name='scheduler-local-gpu',
            node_type='local_gpu',
            agent_url='http://127.0.0.1:9100',
        )
        local = ModelProvider.objects.create(
            name='本地 LLM',
            provider_type='llm',
            deployment_mode='local',
            runtime_node=node,
            executor_class='core.ai_client.runtime_agent_client.RuntimeAgentLLMClient',
            model_name='qwen-local',
        )
        item = self.create_item(provider=local, runtime_node=node, idempotency_key='agent-job')
        running = self.make_expired_running(item)
        GenerationWorkItem.objects.filter(pk=running.pk).update(agent_job_id='job-1')
        scheduler = WorkItemScheduler(agent_control=FakeAgentControl({
            'status': 'succeeded',
            'result': {'text': '恢复后的本地结果'},
        }))

        stats = scheduler.reconcile(enqueue=lambda _: None)

        running.refresh_from_db()
        self.assertEqual(running.status, 'succeeded')
        self.assertEqual(stats['agent_recovered'], 1)

    def test_verified_agent_lease_renewal_keeps_worker_cas_version(self):
        node = RuntimeNode.objects.create(
            name='lease-renew-node', node_type='local_gpu', agent_url='http://127.0.0.1:9100'
        )
        local = ModelProvider.objects.create(
            name='租约续期本地模型',
            provider_type='llm',
            deployment_mode='local',
            runtime_node=node,
            executor_class='core.ai_client.runtime_agent_client.RuntimeAgentLLMClient',
            model_name='qwen-local-renew',
        )
        item = self.create_item(provider=local, runtime_node=node, idempotency_key='lease-renew')
        running = self.make_expired_running(item)
        GenerationWorkItem.objects.filter(pk=running.pk).update(agent_job_id='job-running')
        original_version = running.version
        scheduler = WorkItemScheduler(agent_control=FakeAgentControl({'status': 'running'}))

        stats = scheduler.reconcile(enqueue=lambda _: None)

        running.refresh_from_db()
        self.assertEqual(stats['agent_recovered'], 1)
        self.assertEqual(running.version, original_version)
        self.assertGreater(running.lease_expires_at, timezone.now())
        completed = WorkItemService.succeed(
            running,
            lease_owner='worker-old',
            expected_version=original_version,
        )
        self.assertEqual(completed.status, 'succeeded')

    @override_settings(AI_DISTRIBUTED_RESOURCE_LEASES_ENABLED=False)
    def test_local_agent_job_id_is_persisted_before_result_polling_finishes(self):
        node = RuntimeNode.objects.create(
            name='early-job-node', node_type='local_gpu', agent_url='http://127.0.0.1:9100'
        )
        local = ModelProvider.objects.create(
            name='本地早期持久化 LLM',
            provider_type='llm',
            deployment_mode='local',
            runtime_node=node,
            executor_class='core.ai_client.runtime_agent_client.RuntimeAgentLLMClient',
            model_name='qwen-local',
        )
        item = self.create_item(provider=local, runtime_node=node, idempotency_key='early-job')
        leased = WorkItemService.claim(item, 'worker-early', lease_seconds=120)
        running = WorkItemService.start(leased, 'worker-early')

        class FakeTransport:
            on_job_submitted = None

        class FakeLocalClient:
            def __init__(self):
                self.transport = FakeTransport()

            def _generate_text(self, *_args, **_kwargs):
                self.transport.on_job_submitted('agent-job-early', {'status': 'queued'})
                return AIResponse(success=True, text='完成')

        scheduler = WorkItemScheduler(client_factory=lambda _provider: FakeLocalClient())
        scheduler._invoke_provider(
            local,
            'llm',
            {'prompt': '测试'},
            work_item_id=running.pk,
            lease_owner='worker-early',
        )

        running.refresh_from_db()
        self.assertEqual(running.agent_job_id, 'agent-job-early')
        self.assertGreater(running.lease_expires_at, timezone.now())

    def test_cancel_is_idempotent_and_propagates_to_agent(self):
        node = RuntimeNode.objects.create(
            name='cancel-node', node_type='local_gpu', agent_url='http://127.0.0.1:9100'
        )
        item = self.create_item(runtime_node=node, idempotency_key='cancel')
        running = self.make_expired_running(item)
        GenerationWorkItem.objects.filter(pk=running.pk).update(agent_job_id='job-cancel')
        agent = FakeAgentControl()
        scheduler = WorkItemScheduler(agent_control=agent)

        first = scheduler.cancel(running.pk)
        second = scheduler.cancel(running.pk)

        running.refresh_from_db()
        self.assertEqual(running.status, 'cancelled')
        self.assertTrue(first['agent_cancelled'])
        self.assertTrue(second['idempotent'])
        self.assertEqual(agent.cancelled, [(node.pk, 'job-cancel')])

    def test_stage_aggregation_waits_for_every_terminal_item(self):
        stage = ProjectStage.objects.create(
            project=self.project, stage_type='rewrite', status='pending'
        )
        first = self.create_item(idempotency_key='aggregate-1', status='succeeded')
        second = self.create_item(idempotency_key='aggregate-2', status='waiting')

        summary = WorkItemScheduler.aggregate_stage(self.project.pk, 'rewrite')
        stage.refresh_from_db()
        self.assertEqual(summary['nonterminal'], 1)
        self.assertEqual(stage.status, 'processing')

        GenerationWorkItem.objects.filter(pk=second.pk).update(status='succeeded')
        summary = WorkItemScheduler.aggregate_stage(self.project.pk, 'rewrite')
        stage.refresh_from_db()
        self.assertEqual(summary['nonterminal'], 0)
        self.assertEqual(stage.status, 'completed')
        self.assertEqual(first.status, 'succeeded')

    def test_capability_queue_names_are_stable(self):
        self.assertEqual(queue_for_capability('llm'), 'llm')
        self.assertEqual(queue_for_capability('text2image'), 'image')
        self.assertEqual(queue_for_capability('image_edit'), 'image')
        self.assertEqual(queue_for_capability('image2video'), 'video')
        self.assertEqual(queue_for_capability('motion_render'), 'video')
        self.assertEqual(queue_for_capability('unknown'), 'orchestration')

    def test_resource_queue_wait_does_not_consume_attempt_or_trigger_paid_fallback(self):
        class BusyHybridService:
            @classmethod
            def execute(cls, **_kwargs):
                raise ResourceLeaseUnavailable('GPU 槽正在使用')

        item = self.create_item(idempotency_key='resource-busy', max_attempts=1)
        result = WorkItemScheduler(hybrid_service=BusyHybridService).dispatch(
            item.pk, 'worker-busy'
        )

        item.refresh_from_db()
        self.assertEqual(result['status'], 'retry_wait')
        self.assertEqual(item.status, 'retry_wait')
        self.assertEqual(item.attempt_count, 0)
        self.assertEqual(item.error_code, 'RESOURCE_BUSY')

    def test_enqueue_persists_celery_message_and_capability_queue(self):
        item = self.create_item(idempotency_key='celery-enqueue')
        with patch.object(
            inference_tasks.dispatch_work_item,
            'apply_async',
            return_value=SimpleNamespace(id='celery-task-1'),
        ) as apply_async:
            task_id = inference_tasks._enqueue_dispatch(item.pk)

        item.refresh_from_db()
        self.assertEqual(task_id, 'celery-task-1')
        self.assertEqual(item.route_snapshot['celery_task_id'], 'celery-task-1')
        self.assertEqual(item.route_snapshot['celery_queue'], 'llm')
        apply_async.assert_called_once_with(args=[str(item.pk)], queue='llm')

    def test_cancel_task_revokes_waiting_celery_message_without_terminating_worker(self):
        item = self.create_item(
            idempotency_key='celery-cancel',
            route_snapshot={'celery_task_id': 'celery-task-cancel'},
        )
        with patch(
            'apps.inference.tasks.current_app.control.revoke'
        ) as revoke:
            result = inference_tasks.cancel_work_item.run(str(item.pk))

        item.refresh_from_db()
        self.assertEqual(item.status, 'cancelled')
        self.assertTrue(result['celery_revoked'])
        revoke.assert_called_once_with('celery-task-cancel', terminate=False)

    def test_agent_recovery_downloads_and_verifies_artifact_without_network(self):
        node = RuntimeNode.objects.create(
            name='artifact-recovery-node',
            node_type='local_gpu',
            agent_url='http://127.0.0.1:9100',
            access_token='runtime-test-token',
        )
        item = self.create_item(idempotency_key='artifact-recovery')
        content = b'validated-artifact'
        sha256 = hashlib.sha256(content).hexdigest()

        class Response:
            headers = {'X-Artifact-SHA256': sha256}

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_content(chunk_size):
                return [content]

        job = {'artifacts': [{
            'artifact_id': 'artifact-1',
            'filename': 'result.bin',
            'sha256': sha256,
            'download_url': '/v1/artifacts/artifact-1',
        }]}
        with tempfile.TemporaryDirectory() as root_dir, override_settings(
            STORAGE_ROOT=Path(root_dir)
        ), patch(
            'apps.inference.services.scheduler.requests.get', return_value=Response()
        ) as request_get:
            artifacts = RuntimeAgentControl().materialize_artifacts(node, job, item)
            recovered_content = Path(artifacts[0]['storage_path']).read_bytes()

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(recovered_content, content)
        request_get.assert_called_once()

    def test_hybrid_dict_response_helpers_keep_old_video_clients_compatible(self):
        success = {'success': True, 'data': [{'url': 'storage/video.mp4'}], 'metadata': {
            'usage': {'video_seconds': 8}
        }}
        HybridInferenceService._assert_success(success)
        self.assertEqual(
            HybridInferenceService._extract_usage(success, {'video_seconds': 5})['video_seconds'],
            8,
        )
        self.assertEqual(HybridInferenceService._response_summary(success)['artifact_count'], 1)
        with self.assertRaises(Exception):
            HybridInferenceService._assert_success({'success': False, 'error': 'GPU_OOM: 显存不足'})

    def test_paid_structure_repair_success_settles_reservation_and_log(self):
        paid = ModelProvider.objects.create(
            name='结构修复 API',
            provider_type='llm',
            deployment_mode='api',
            executor_class='core.ai_client.openai_client.OpenAIClient',
            api_url='https://example.invalid/v1',
            api_key='test-only',
            model_name='repair-model',
        )
        policy = AIBudgetPolicy.objects.create(
            name='结构修复预算',
            hard_limit=Decimal('100'),
            daily_limit_cny=Decimal('100'),
            monthly_limit_cny=Decimal('100'),
        )
        ProjectAISettings.objects.create(
            project=self.project,
            budget_policy=policy,
            allow_paid_fallback=True,
            allow_cloud_data_transfer=True,
            cloud_authorized_by=self.user,
            cloud_authorized_at=timezone.now(),
            project_budget_cny=Decimal('100'),
        )
        ProviderPriceRate.objects.create(
            provider=paid,
            capability='llm',
            model_pattern='repair-model',
            billing_unit='request',
            unit_price=Decimal('1'),
        )

        schema_error = RuntimeError('首次输出不是合法 JSON')
        schema_error.code = 'OUTPUT_SCHEMA_INVALID'

        def invalid_invoke(*_args):
            raise schema_error

        result = HybridInferenceService.execute(
            project=self.project,
            capability='llm',
            stage_type='storyboard',
            invoke=invalid_invoke,
            repair_invoke=lambda *_args: {
                'success': True,
                'text': '{"scenes": []}',
                'metadata': {'usage': {'request_count': 1, 'output_tokens': 8}},
            },
            request_parameters={
                'prompt': '生成分镜 JSON',
                'confirmed_max_cost_cny': '1.000000',
            },
            usage_estimate={'request_count': 1},
            explicit_provider=paid,
            manual_api=True,
        )

        reservation = BudgetReservation.objects.get()
        result.usage_log.refresh_from_db()
        policy.refresh_from_db()
        self.assertEqual(result.usage_log.status, 'success')
        self.assertEqual(result.usage_log.error_code, '')
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'settled')
        self.assertEqual(policy.reserved_amount, Decimal('0'))
        self.assertEqual(policy.spent_amount, Decimal('1'))

    def test_paid_gate_locks_nullable_settings_and_policy_separately(self):
        provider, _policy = self.create_paid_setup('lock-shape')
        locked_query_shapes = []

        def capture_lock(queryset):
            locked_query_shapes.append((queryset.model, bool(queryset.query.select_related)))
            return queryset

        with patch('apps.inference.services.gates._locked', side_effect=capture_lock):
            reservation = PaidCallGate.reserve(
                self.project,
                provider,
                'llm',
                {'request_count': 1},
                'separate-locks',
            )

        self.assertEqual(reservation.status, 'active')
        self.assertEqual(locked_query_shapes[0], (ProjectAISettings, False))
        self.assertIn((AIBudgetPolicy, False), locked_query_shapes)

    def test_paid_gate_rejects_price_above_per_call_confirmation_cap(self):
        provider, _policy = self.create_paid_setup('confirmation-cap')

        with self.assertRaises(PaidCallDenied) as raised:
            PaidCallGate.reserve(
                self.project,
                provider,
                'llm',
                {'request_count': 1},
                'confirmed-cap-too-low',
                automatic_fallback=False,
                confirmed_max_cost_cny='0.500000',
            )

        self.assertEqual(raised.exception.code, 'BUDGET_DENIED')
        self.assertFalse(BudgetReservation.objects.filter(
            idempotency_key='confirmed-cap-too-low'
        ).exists())

    def test_explicit_paid_provider_requires_manual_confirmation_before_reservation(self):
        provider, _policy = self.create_paid_setup('explicit-confirmation')
        invoked = []

        with self.assertRaises(HybridExecutionFailed) as raised:
            HybridInferenceService.execute(
                project=self.project,
                capability='llm',
                stage_type='rewrite',
                invoke=lambda *_args: invoked.append(True),
                request_parameters={'prompt': '不得直接付费'},
                usage_estimate={'request_count': 1},
                explicit_provider=provider,
                manual_api=False,
            )

        self.assertEqual(raised.exception.code, 'PAID_CONFIRMATION_REQUIRED')
        self.assertEqual(invoked, [])
        self.assertFalse(BudgetReservation.objects.exists())

    def test_explicit_paid_provider_requires_confirmed_cost_cap(self):
        provider, _policy = self.create_paid_setup('explicit-cap')

        with self.assertRaises(HybridExecutionFailed) as raised:
            HybridInferenceService.execute(
                project=self.project,
                capability='llm',
                stage_type='rewrite',
                invoke=lambda *_args: None,
                request_parameters={'prompt': '缺少费用上限'},
                usage_estimate={'request_count': 1},
                explicit_provider=provider,
                manual_api=True,
            )

        self.assertEqual(raised.exception.code, 'BUDGET_DENIED')
        self.assertFalse(BudgetReservation.objects.exists())

    def test_existing_paid_reservation_states_never_invoke_again(self):
        provider, policy = self.create_paid_setup('state-replay')
        expected_codes = {
            'active': 'PAID_REQUEST_IN_PROGRESS',
            'settled': 'PAID_REQUEST_ALREADY_SETTLED',
            'ambiguous': 'PAID_RESULT_AMBIGUOUS',
            'manual_review': 'PAID_RESULT_AMBIGUOUS',
            'released': 'PAID_RESERVATION_CLOSED',
            'expired': 'PAID_RESERVATION_CLOSED',
        }
        invoke_calls = []

        for status_name, expected_code in expected_codes.items():
            with self.subTest(status=status_name):
                item = self.create_item(
                    provider=provider,
                    idempotency_key=f'paid-replay-{status_name}',
                )
                BudgetReservation.objects.create(
                    policy=policy,
                    project=self.project,
                    work_item=item,
                    idempotency_key=f'{item.idempotency_key}:paid:1',
                    status=status_name,
                    estimated_amount=Decimal('1'),
                    reserved_amount=Decimal('1'),
                    settled_amount=Decimal('1') if status_name == 'settled' else Decimal('0'),
                    details={
                        'capability': 'llm',
                        'provider_id': str(provider.id),
                    },
                )

                with self.assertRaises(HybridExecutionFailed) as raised:
                    HybridInferenceService.execute(
                        project=self.project,
                        capability='llm',
                        stage_type='rewrite',
                        invoke=lambda *_args: invoke_calls.append(status_name),
                        request_parameters={
                            'prompt': '不得重复提交',
                            'confirmed_max_cost_cny': '1.000000',
                        },
                        usage_estimate={'request_count': 1},
                        work_item=item,
                        explicit_provider=provider,
                        manual_api=True,
                    )

                self.assertEqual(raised.exception.code, expected_code)

        self.assertEqual(invoke_calls, [])


class ArtifactCleanupTestCase(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='cleanup-user', password='test')
        self.project = Project.objects.create(name='清理测试', original_topic='测试', user=user)
        self.item = GenerationWorkItem.objects.create(
            project=self.project,
            capability='text2image',
            idempotency_key='cleanup-item',
        )

    def artifact(self, uri, lifecycle='intermediate', protected=False):
        return MediaArtifact.objects.create(
            work_item=self.item,
            project=self.project,
            kind='image',
            status='ready',
            lifecycle=lifecycle,
            uri=str(uri),
            protected=protected,
            expires_at=timezone.now() - timedelta(days=1),
        )

    def test_cleanup_requires_switch_and_never_touches_unsafe_or_protected_files(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(root_dir)
            removable = root / 'intermediate.bin'
            failed_file = root / 'failed.bin'
            source_file = root / 'source.bin'
            final_file = root / 'final.bin'
            protected_file = root / 'protected.bin'
            outside_file = Path(outside_dir) / 'outside.bin'
            for path in (
                removable, failed_file, source_file, final_file, protected_file, outside_file
            ):
                path.write_bytes(b'test')

            removable_artifact = self.artifact(removable)
            failed_artifact = self.artifact(failed_file, lifecycle='failed')
            self.artifact(source_file, lifecycle='source')
            self.artifact(final_file, lifecycle='final')
            self.artifact(protected_file, protected=True)
            unsafe_artifact = self.artifact(outside_file)

            with override_settings(
                STORAGE_ROOT=root,
                STORAGE_URL='storage/',
                AI_ARTIFACT_CLEANUP_ENABLED=False,
            ):
                disabled = ArtifactRetentionService.cleanup()
            self.assertEqual(disabled['enabled'], 0)
            self.assertTrue(removable.exists())

            with override_settings(
                STORAGE_ROOT=root,
                STORAGE_URL='storage/',
                AI_ARTIFACT_CLEANUP_ENABLED=True,
            ):
                result = ArtifactRetentionService.cleanup()

            removable_artifact.refresh_from_db()
            failed_artifact.refresh_from_db()
            unsafe_artifact.refresh_from_db()
            self.assertEqual(result['deleted'], 2)
            self.assertEqual(result['unsafe'], 1)
            self.assertEqual(removable_artifact.status, 'deleted')
            self.assertEqual(failed_artifact.status, 'deleted')
            self.assertEqual(unsafe_artifact.status, 'ready')
            self.assertFalse(removable.exists())
            self.assertFalse(failed_file.exists())
            self.assertTrue(source_file.exists())
            self.assertTrue(final_file.exists())
            self.assertTrue(protected_file.exists())
            self.assertTrue(outside_file.exists())
