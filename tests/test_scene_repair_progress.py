import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipelines.scene_production.repair_progress import (
    capture_ref, choose_baseline, comparison_prompt, load_baseline, repair_decision,
)


def receipt(states, issues=()):
    return {'native_relation_checks': [
        {'source':name,'type':'supported_by','target':'ground','passed':state}
        for name,state in states.items()], 'physical_issues':list(issues)}


class SceneRepairProgressTests(unittest.TestCase):
    def setUp(self):
        self.ref = capture_ref('sf_first.scene0', 0, 'a')
        self.candidate = capture_ref('sf_next.scene0', 0, 'b')
        self.base = {'ref': self.ref, 'round': 11, 'images':[Path('view_0.png')],
                     'receipt':receipt({'a':False, 'b':False, 'c':True})}

    def decision(self, states, findings=(), new_issues=(), complete=True):
        comparison = {'baseline_token':'a', 'complete':complete, 'findings':list(findings)}
        return repair_decision(self.base, self.candidate, receipt(states,new_issues),
                               {'passed':False,'repair_comparison':comparison}, {'view_1.png'})

    def finding(self, kind, **extra):
        return {'kind':kind,'before_view':'view_0.png','after_view':'view_1.png',
                'observation':'The same obstruction is visible in both captures.', **extra}

    def test_new_regression_outweighs_two_fixes(self):
        progress = self.decision({'a':True,'b':True,'c':False})
        self.assertEqual(progress['decision'], 'rollback')
        self.assertEqual(progress['next_base'], self.ref)
        self.assertEqual(len(progress['native']['fixed']), 2)

    def test_static_support_advice_is_not_a_native_regression(self):
        current = receipt({'a': True, 'b': False, 'c': False})
        current['native_relation_checks'][2]['advisory'] = True
        comparison = {'baseline_token': 'a', 'complete': True, 'findings': []}
        result = repair_decision(self.base, self.candidate, current,
                                 {'passed': False, 'repair_comparison': comparison}, {'view_1.png'})
        self.assertEqual(result['decision'], 'advance')
        self.assertEqual(result['native']['regressed'], [])

    def test_reordering_and_round_budget_do_not_change_verdict(self):
        self.assertEqual(self.decision({'c':True,'b':False,'a':False})['decision'], 'keep_baseline')
        self.assertEqual(self.decision({'b':False,'a':True,'c':True})['decision'], 'advance')

    def test_first_reported_is_not_a_regression_or_fix_until_checked(self):
        states = {'a':True,'b':False,'c':True}
        self.assertEqual(self.decision(states,new_issues=['suspected overlap'])['decision'], 'uncompared')
        row = self.finding('preexisting', first_reported_index=0)
        self.assertEqual(self.decision(states,[row],['suspected overlap'])['decision'], 'advance')
        self.assertEqual(self.decision(states,[{**row,'before_view':'missing.png'}],['suspected overlap'])['decision'], 'uncompared')

    def test_visual_only_fix_and_visual_regression(self):
        states = {'a':False,'b':False,'c':True}
        self.assertEqual(self.decision(states,[self.finding('fixed')])['decision'], 'advance')
        self.assertEqual(self.decision(states,[self.finding('fixed'),self.finding('regressed')])['decision'], 'rollback')
        self.assertEqual(self.decision({'a':True})['decision'], 'uncompared')
        self.assertEqual(self.decision(states,complete=False)['decision'], 'uncompared')

    def test_stale_sibling_cannot_displace_promoted_or_accepted_baseline(self):
        def row(ref, decision=None, parent=None, passed=False):
            return {'task_id':ref['task_id'],'revision':ref['revision'],'token':ref['token'],
                    'progress':{'decision':decision,'baseline':parent},'passed':passed}
        sibling = capture_ref('sf_sibling.scene0',0,'c')
        chain = [row(self.ref), row(self.candidate,'advance',self.ref), row(sibling,'advance',self.ref)]
        self.assertEqual(choose_baseline(chain), self.candidate)
        chain += [row(sibling, passed=True), row(self.ref,'advance',sibling)]
        self.assertEqual(choose_baseline(chain), sibling)

    def test_resaved_same_design_preserves_repair_baseline(self):
        self.base['receipt']['scene_brief']={'id':'design','plan_path':'/saved/revision_1/plan.json','plan_version':[10,1]}
        current={**receipt({}),'scene_brief':{'id':'design','plan_path':'/saved/revision_2/plan.json','plan_version':[12,2]}}
        result=repair_decision(self.base,self.candidate,current,{'passed':False},{'view_1.png'})
        self.assertEqual(result['decision'],'uncompared')
        self.assertEqual(result['next_base'], self.ref)

    def test_different_design_identity_does_not_inherit_repair_baseline(self):
        self.base['receipt']['scene_brief']={'id':'design-a','plan_path':'/saved/a.json','plan_version':[10,1]}
        current={**receipt({}),'scene_brief':{'id':'design-b','plan_path':'/saved/a.json','plan_version':[12,2]}}
        result=repair_decision(self.base,self.candidate,current,{'passed':False},{'view_1.png'})
        self.assertEqual(result['decision'],'uncompared')
        self.assertIsNone(result['next_base'])

    def test_missing_history_and_stale_native_images_do_not_block_current_acceptance(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            store=SimpleNamespace(root=root, repo=SimpleNamespace(engine=None))
            task={'id':'sf_next.scene0','run_id':'sf_next','unit':{'revision':0}}
            current={'capture_token':'b','scene_brief':{'id':'design'}}
            with patch('pipelines.scene_production.repair_progress.repair_history', return_value=[]) as history:
                self.assertEqual(load_baseline(store,task,current), {'ref':None,'round':1})
                folder=root/self.ref['task_id']/'revision_0'
                (folder/'capture').mkdir(parents=True)
                file=folder/'capture'/'view_0.png'
                file.write_bytes(b'original')
                old={'capture_token':'a','evidence_versions':{'capture/view_0.png':[file.stat().st_size,file.stat().st_mtime_ns]}}
                (folder/'scene_acceptance.json').write_text(json.dumps(old))
                history.return_value=[{**self.ref,'progress':None,'passed':False}]
                base=load_baseline(store,task,current)
                self.assertIn('view_0.png',comparison_prompt(base,current))
                file.write_bytes(b'changed image')
                stale=load_baseline(store,task,current)
                self.assertNotIn('images',stale)
                self.assertEqual(repair_decision(stale,self.candidate,current,{'passed':True},set())['decision'],'accepted')
