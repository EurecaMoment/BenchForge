"""Native S2 capture budget regressions using isolated PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from database.schema import events, work_items
from database.transactions import lock_entity
from pipelines.scene_production.service import create_store
from server.http_api import request_error_payload
from service_access.access_scope import TrustedAccess, access_context
from service_access.request_context import RunCancelled
from storage.access_identity import register_session_workspace
from storage.scene_jobs.capture_budget import CaptureBudgetExhausted, LIMIT, RESERVATION_EVENT
from tests.database_fixtures import use_postgres


class CaptureBudgetTests(unittest.TestCase):
    def setUp(self):
        database_url = use_postgres(self)
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.store = create_store(self.root / 'artifacts', database_url=database_url)
        self.scopes = {sid: register_session_workspace(
            self.store.repo.engine, host_id='test', session_id=sid, owner_id='owner',
            workspace_id='workspace-test', root_path=self.root,
        ) for sid in ('one', 'two')}
        self.design = {'id': 'frozen-design', 'plan_path': '/saved/plan.json', 'plan_version': [8, 42]}
        self.enterContext(patch.object(self.store.lifecycle, 'admit', side_effect=lambda *_: self.design))

    def submit(self, key, *, scene='room', session='one', mode='production', capture_only=True, views=None):
        intent = {'name': key, 'capture_only': capture_only, 'execution_mode': mode}
        if views is not None:
            intent['capture_options'] = {'view_indices': views}
        program = {'scene_id': scene, 'cameras': [key]}
        with access_context(TrustedAccess(self.scopes[session], str(self.root))):
            run = self.store.submit(key, [intent], submitted_programs=[{'program': program}])
        return f'{run}.scene0', program

    def queue(self, task, program):
        directory = self.store.directory(task, 0)
        (directory / 'program.json').write_text(json.dumps(program), encoding='utf-8')
        (directory / 'scene_admission.json').write_text(json.dumps({
            'enforced': True, 'passed': True, 'scene_id': program['scene_id'], 'scene_brief': self.design,
        }), encoding='utf-8')
        self.store.update(task, state='READY', unit={'phase': 'capture'})

    def reserve(self, task, program, *, token=None, owner='worker', retained=False):
        self.store.reserve_capture(task, token or task, program, expected_owner=owner, retained=retained)
        return self.store.snapshot(task.split('.')[0])['tasks'][0]['unit'].get('capture_budget')

    def seed(self, number, *, legacy=False):
        pairs = [self.submit(f'initial-{i}') for i in range(number)]
        for i, (task, program) in enumerate(pairs):
            if legacy:
                review = {'passed': i == 0, 'production_acceptance': {
                    'capture_token': task, 'enforced': True, 'passed': i == 0,
                    'scene_id': program['scene_id'], 'scene_brief': self.design,
                }}
                with self.store.repo.transaction() as connection:
                    connection.execute(work_items.update().where(work_items.c.id == task).values(
                        state='SUCCEEDED' if i == 0 else 'FAILED_FINAL',
                        result={'scene_review': review} if i == 0 else None,
                        failure=None if i == 0 else {'scene_review': review},
                    ))
            else:
                self.queue(task, program)
                self.store.claim_stage(task, 'worker')
                self.reserve(task, program)
        return pairs

    def test_concurrent_attempts_claim_only_one_remaining_capture(self):
        self.seed(LIMIT - 1)
        pairs = [self.submit(f'candidate-{i}', capture_only=i == 0) for i in range(2)]
        for task, program in pairs:
            self.queue(task, program)
            self.store.claim_stage(task, 'worker')

        def capture(pair):
            try:
                return self.reserve(*pair)
            except CaptureBudgetExhausted as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(capture, pairs))
        winners = [(pair, result) for pair, result in zip(pairs, outcomes) if isinstance(result, dict)]
        self.assertEqual(len(winners), 1)
        (task, program), previous = winners[0]
        self.assertEqual(previous['used'], LIMIT)
        loser = next(result for result in outcomes if isinstance(result, CaptureBudgetExhausted))
        self.assertEqual(loser.failure()['capture_budget']['remaining'], 0)
        self.assertEqual(self.reserve(task, program)['used'], LIMIT)
        with self.assertRaises(RunCancelled):
            self.reserve(task, program, owner='expired-worker')
        with self.assertRaises(CaptureBudgetExhausted):
            self.reserve(task, program, token='new-native-attempt')
        loser_task = next(pair[0] for pair, result in zip(pairs, outcomes) if result is loser)
        self.store.update(loser_task, state='FAILED_FINAL', failure=loser.failure())
        with self.assertRaises(CaptureBudgetExhausted) as blocked:
            self.store.retry(loser_task, capture_mode='restart')
        self.assertEqual(blocked.exception.budget, loser.budget)

        self.store.update(task, state='FAILED_FINAL', failure={'stage': 'capture_transport'})
        retry = self.store.retry(task, capture_mode='resume')
        self.assertEqual(retry['capture_recovery_mode'], 'resume')
        self.store.claim_stage(task, 'recovered-worker')
        self.assertEqual(self.reserve(task, program, owner='recovered-worker')['used'], LIMIT)
        with self.store.repo.engine.connect() as connection:
            count = connection.scalar(select(func.count()).select_from(events).where(events.c.event == RESERVATION_EVENT))
        self.assertEqual(count, LIMIT)

    def test_legacy_history_blocks_recapture_and_preserves_passing_evidence(self):
        history = self.seed(81, legacy=True)
        accepted, _ = history[0]
        with self.assertRaises(CaptureBudgetExhausted) as error:
            self.submit('over-budget')
        payload = request_error_payload(error.exception, 400)
        self.assertEqual(payload['code'], 'S2_CAPTURE_BUDGET_EXHAUSTED')
        budget = payload['issues'][0]['capture_budget']
        self.assertEqual((budget['used'], budget['remaining']), (81, 0))
        self.assertEqual(budget['evidence'][0]['task_id'], accepted)
        self.assertTrue(budget['evidence'][0]['accepted'])
        self.assertEqual(self.store.snapshot(accepted.split('.')[0])['tasks'][0]['state'], 'SUCCEEDED')

        for name, options in (
            ('separate-scene', {'scene': 'another'}),
            ('separate-session', {'session': 'two'}),
            ('exploration', {'mode': 'exploration'}),
            ('export-only', {'views': []}),
        ):
            self.assertTrue(self.submit(name, **options)[0])
        self.design = {**self.design, 'plan_version': [10, 43]}
        self.assertTrue(self.submit('new-frozen-design')[0])

    def test_retrieve_retained_capture_after_limit_without_allowing_a_new_token(self):
        task, program = self.submit('already-rendering')
        self.queue(task, program)
        self.store.claim_stage(task, 'worker')
        self.seed(LIMIT, legacy=True)
        self.assertEqual(self.reserve(task, program, retained=True)['used'], LIMIT + 1)
        self.assertEqual(self.reserve(task, program)['used'], LIMIT + 1)
        with self.assertRaises(CaptureBudgetExhausted):
            self.reserve(task, program, token='different-remote-job')

    def test_legacy_native_publication_counts_before_any_review(self):
        task, program = self.submit('retained-before-review')
        self.queue(task, program)
        self.store.claim_stage(task, 'worker')
        incoming = self.store.directory(task, 0) / 'incoming'
        incoming.mkdir()
        (incoming / 'report.json').write_text(json.dumps({
            'status': 'captured', 'token': 'legacy-native', 'frames': 1,
        }), encoding='utf-8')
        self.store.publish_capture(task, 'legacy-native', incoming, expected_owner='worker')
        self.seed(LIMIT - 1, legacy=True)
        with self.assertRaises(CaptureBudgetExhausted) as error:
            self.submit('would-be-eleventh')
        budget = error.exception.budget
        self.assertEqual(budget['used'], LIMIT)
        evidence = next(row for row in budget['evidence'] if row['token'] == 'legacy-native')
        self.assertEqual(evidence, {'task_id': task, 'revision': 0, 'token': 'legacy-native',
                                    'captured': True, 'accepted': False})

    def test_rollback_releases_capacity_and_other_scenes_do_not_wait(self):
        self.seed(LIMIT - 1, legacy=True)
        blocked_scene = self.submit('rollback')
        other_scene = self.submit('other', scene='independent')
        for task, program in (blocked_scene, other_scene):
            self.queue(task, program)
            self.store.claim_stage(task, 'worker')
        with patch.object(self.store.repo, 'event', side_effect=RuntimeError('failed event')):
            with self.assertRaisesRegex(RuntimeError, 'failed event'):
                self.reserve(*blocked_scene)
        scope = self.scopes['one']
        with ThreadPoolExecutor(max_workers=1) as workers:
            with self.store.repo.transaction() as connection:
                lock_entity(connection, 's2-capture-budget', scope.workspace_id, scope.session_id,
                            'room', self.design['id'], self.design['plan_path'], self.design['plan_version'])
                independent = workers.submit(self.reserve, *other_scene).result(timeout=5)
                self.assertEqual(independent['used'], 1)
        self.assertEqual(self.reserve(*blocked_scene)['used'], LIMIT)
