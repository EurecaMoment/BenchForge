"""采集期间数据库短暂不可用时保留原生作业，仍执行取消和租约隔离。"""
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch
from sqlalchemy.exc import OperationalError, TimeoutError as DatabaseTimeout

from pipelines.scene_production.stage_control import StageControl
from server.http_api import request_error_status
from storage.scene_jobs.active_capture import capture_identity
from storage.access_identity import register_session_workspace
from service_access.access_scope import TrustedAccess, access_context
from pipelines.scene_production.service import create_store
from tests.database_fixtures import use_postgres


class StageControlTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.signal = StageControl(self.store, {'id':'run.scene0','run_id':'run',
                                  'lease_expires_at': time.time()+60}, 'owner', Event())

    def test_disconnect_during_poll_keeps_job_and_later_honors_cancel(self):
        for error in (OperationalError(None,None,TimeoutError()), DatabaseTimeout()):
            self.store.run_state.side_effect = [error, 'CANCELED']
            self.signal.canceled = False
            self.signal.next_check = 0
            self.assertFalse(self.signal.is_set())
            self.assertFalse(self.signal.is_set())
            self.signal.next_check = 0
            self.assertTrue(self.signal.is_set())
            self.assertTrue(self.signal.canceled)
            self.assertEqual(request_error_status('POST',error),503)
        self.store.snapshot.assert_not_called()

    def test_lost_lease_detaches_without_asking_isaac_to_cancel(self):
        self.signal.deadline = time.monotonic() - 1
        self.assertTrue(self.signal.is_set())
        self.assertFalse(self.signal.canceled)
        self.store.run_state.assert_not_called()

    def test_renew_retries_only_until_lease_is_lost(self):
        self.store.renew_stage.side_effect = [DatabaseTimeout(), True, False]
        with patch.object(self.signal.done,'wait',return_value=False) as wait:
            self.signal.renew()
        self.assertEqual([call.args[0] for call in wait.call_args_list], [15,2,15])
        self.assertTrue(self.signal.is_set())
        self.assertFalse(self.signal.canceled)

    def test_only_identical_frozen_captures_can_be_reused(self):
        intent={'name':'first','scene_program_path':'tasks/v53.json','capture_only':True,'execution_mode':'production'}
        submitted=[{'program':{'scene_id':'room','cameras':[1,2]}}]
        design={'id':'design','plan_version':[100,123]}
        original=capture_identity([intent],submitted,design)
        self.assertTrue(original)
        self.assertEqual(original,capture_identity([{**intent,'name':'next','scene_program_path':'tasks/v54.json'}],submitted,design))
        self.assertNotEqual(original,capture_identity([intent],submitted,{**design,'plan_version':[150,124]}))
        self.assertNotEqual(original,capture_identity([intent],[{'program':{'scene_id':'room','cameras':[2,1]}}],design))
        self.assertNotEqual(original,capture_identity([{**intent,'capture_options':{'view_indices':[1]}}],submitted,design))
        self.assertIsNone(capture_identity([{**intent,'layout':{'mode':'generate'}}],submitted,design))

    def test_concurrent_duplicate_submissions_share_a_run_only_while_active(self):
        """在独立 PostgreSQL 库验证真实锁、别名重试、终态重采及会话隔离。"""
        database_url=use_postgres(self)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            store=create_store(root/'artifacts',database_url=database_url)
            def submit(key,name,scope):
                with access_context(TrustedAccess(scope,str(root))):
                    return store.submit(key,[{'name':name,'capture_only':True,'execution_mode':'production'}],
                                        submitted_programs=[{'program':{'scene_id':'sample'}}])
            def scope(sid):
                return register_session_workspace(store.repo.engine,host_id='test',session_id=sid,owner_id='owner',
                                                  workspace_id='workspace-test',root_path=root)
            parent,other=scope('one'),scope('two')
            with patch.object(store.lifecycle,'admit',return_value={'id':'design','plan_version':[20,1]}), ThreadPoolExecutor(2) as pool:
                futures=[pool.submit(submit,key,key,parent) for key in ('first','second')]
                a,b=[job.result(timeout=10) for job in futures]
                self.assertEqual(a,b)
                self.assertNotEqual(a,submit('independent','first',other))
                store.update(a+'.scene0',state='FAILED_FINAL')
                self.assertEqual(a,submit('second','second',parent))
                self.assertNotEqual(a,submit('recapture','second',parent))
