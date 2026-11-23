"""Review-only retries validate outside the queue's locked transaction."""
from contextlib import contextmanager
from copy import deepcopy
import unittest
from unittest.mock import Mock

from storage.scene_jobs.store import Store


class SceneReviewRetryTests(unittest.TestCase):
    def setUp(self):
        self.task = {'id': 'sf_test.scene0', 'run_id': 'sf_test',
                     'state': 'FAILED_FINAL', 'attempt': 1, 'failure': {'scene_review': {'passed': False}},
                     'unit': {'phase': 'review', 'revision': 3, 'capture_token': 'native-receipt'}}
        self.run = {'id': 'sf_test', 'state': 'FAILED_FINAL', 'code_revision': 'benchforge-0.2.0'}
        self.writes = []
        self.write_transaction = False
        self.read_connection = False
        self.store = Store.__new__(Store)
        self.store.repo = Mock()
        self.store.repo.engine.connect.side_effect = self.connect
        self.store.repo.transaction.side_effect = self.transaction
        self.store.lifecycle = Mock()
        self.store.lifecycle.review_only_requested.side_effect = lambda unit: unit.get('scene_review_policy_version') == 2

    @contextmanager
    def connect(self):
        self.read_connection = True
        try:
            yield self.connection(locked=False)
        finally:
            self.read_connection = False

    @contextmanager
    def transaction(self):
        self.write_transaction = True
        try:
            yield self.connection(locked=True)
        finally:
            self.write_transaction = False

    def connection(self, *, locked):
        rows = iter((deepcopy(self.task), deepcopy(self.run)))

        def execute(statement):
            if statement.is_select:
                self.assertEqual(statement._for_update_arg is not None, locked)
                return Mock(**{'mappings.return_value.one.return_value': next(rows)})
            self.writes.append(statement.table.name)

        return Mock(**{'execute.side_effect': execute})

    def retry(self):
        return self.store.retry(self.task['id'], capture_mode='review_only')

    def prepared_unit(self, task):
        self.assertFalse(self.read_connection)
        self.assertFalse(self.write_transaction)
        return {**task['unit'], 'scene_review_policy_version': 2}

    def test_validates_before_taking_write_locks(self):
        self.store.lifecycle.review_only.side_effect = self.prepared_unit
        result = self.retry()
        self.assertEqual((result['state'], result['revision'], result['phase']), ('READY', 3, 'review'))
        self.assertEqual(self.writes, ['runs', 'work_items'])
        self.store.repo.event.assert_called_once()

    def test_fences_changes_to_task_run_and_policy_during_validation(self):
        for name, target, key, value, error in (
            ('capture revision', self.task['unit'], 'revision', 4, 'task changed'),
            ('capture token', self.task['unit'], 'capture_token', 'replacement', 'task changed'),
            ('task attempt', self.task, 'attempt', 2, 'task changed'),
            ('task state', self.task, 'state', 'READY', 'only failed tasks'),
            ('run version', self.run, 'code_revision', 'changed', 'task changed'),
            ('run cancellation', self.run, 'state', 'CANCELED', 'canceled or successful'),
        ):
            with self.subTest(name=name):
                previous = target[key]

                def validate(task):
                    target[key] = value
                    return self.prepared_unit(task)

                self.store.lifecycle.review_only.side_effect = validate
                try:
                    with self.assertRaisesRegex(ValueError, error):
                        self.retry()
                finally:
                    target[key] = previous
                self.assertEqual(self.writes, [])
        self.store.repo.event.assert_not_called()

    def test_parallel_winner_is_returned_with_no_duplicate_enqueue_or_event(self):
        for replaced_receipt in (False, True):
            with self.subTest(replaced_receipt=replaced_receipt):
                self.task.update(state='FAILED_FINAL')
                self.task['unit'].pop('scene_review_policy_version', None)

                def validate(task):
                    unit = self.prepared_unit(task)
                    self.task.update(state='RUNNING', unit=unit)
                    if replaced_receipt:
                        raise ValueError('The winning request already wrote a newer receipt')
                    return unit

                self.store.lifecycle.review_only.side_effect = validate
                result = self.retry()
                self.assertEqual((result['state'], result['revision']), ('RUNNING', 3))
                self.store.lifecycle.review_only.reset_mock()
                self.assertEqual(self.retry(), result)
                self.store.lifecycle.review_only.assert_not_called()
                self.assertEqual(self.writes, [])
        self.store.repo.event.assert_not_called()

    def test_evidence_validation_error_is_preserved_without_locking(self):
        self.store.lifecycle.review_only.side_effect = ValueError('Captured evidence changed')
        with self.assertRaisesRegex(ValueError, 'Captured evidence changed'):
            self.retry()
        self.store.repo.transaction.assert_not_called()
