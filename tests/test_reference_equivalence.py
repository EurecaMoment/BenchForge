"""S1 identity is invariant under a proven rename, not a change of viewpoint."""
from copy import deepcopy
import unittest
from pipelines.benchmark.design.plan_review import revision_review


def scene(names=('table', 'chair', 'marker'), scene_id='workshop'):
    origin, observer, target = names
    frame = {'kind': 'user', 'id': 'user', 'origin_object_id': origin, 'observer_object_id': observer,
             'facing_target_object_id': target, 'front': 'toward_observer'}
    return {'id': scene_id, 'object_roles': [{'object_id': i, 'role': r} for i, r in zip(names, ('surface','seat','landmark'))],
            'relations': [{'source': observer, 'target': origin, 'type':'near'}],
            'spatial_reference': {'schema': 'benchforge.review-reference/v1', 'world_up':[0,0,1], 'frames':[frame]}}


def classify(a,b):
    wrap=lambda d: {'stage_review_key':'active-s1','assessment':{'scenes':{'designs':[d]}}}
    prior, nxt=wrap(a),wrap(b)
    original=deepcopy((prior,nxt))
    key, receipt=revision_review(nxt,'next',prior,'baseline')
    assert (prior,nxt)==original
    assert (key=='active-s1')==receipt['review_reused']
    return receipt['kind']


class ReferenceEquivalentTests(unittest.TestCase):
    def test_registered_slots_and_relations_follow_rename_in_distinct_scenes(self):
        for title,old,new in [('workshop',('table','seat','marker'),('desk','stool','target')),
                              ('gallery',('pedestal','viewer','portrait'),('plinth','visitor','painting'))]:
            with self.subTest(scene=title): self.assertEqual(classify(scene(old,title),scene(new,title)),'execution_repair')

    def test_equivalent_definitions_in_two_languages(self):
        for old,new in [('Front means toward the observer.', 'Front is the direction toward the observer.'),
                        ('左方是指观察者的左侧。','左方定义为观察者的左侧。')]:
            a,b=scene(),scene();a['spatial_reference']=old;b['spatial_reference']=new
            with self.subTest(old=old): self.assertEqual(classify(a,b),'execution_repair')

    def test_new_observer_swap_and_axes_remain_observable(self):
        for patch in ({'observer_object_id':'table'}, {'observer_object_id':'marker','facing_target_object_id':'chair'},
                      {'front':'observer_forward'}, {'id':'another-frame'}):
            a,b=scene(),scene();b['spatial_reference']['frames'][0].update(patch)
            with self.subTest(patch=patch): self.assertEqual(classify(a,b),'contract_change')
        a,b=scene(),scene();b['spatial_reference']['world_up']=[0,0,-1];self.assertEqual(classify(a,b),'contract_change')

    def test_unrecognized_prose_and_qualifiers_remain_literal(self):
        for old,new in [('Front means toward the door.','Front is the direction away from the door.'),
                        ('Left means toward A.','Left means not toward A.'), ('Look at A.','Look at B.'),
                        ('前方是指朝向甲。','前方定义为背向甲。')]:
            a,b=scene(),scene();a['spatial_reference']=old;b['spatial_reference']=new
            with self.subTest(old=old): self.assertEqual(classify(a,b),'contract_change')

    def test_partial_rename_and_ambiguous_bijection_require_review(self):
        a,b=scene(),scene(('table','seat','marker'));b['spatial_reference']['frames'][0]['observer_object_id']='chair';self.assertEqual(classify(a,b),'contract_change')
        a,b=scene(),scene(('chair','table','marker'));self.assertEqual(classify(a,b),'contract_change')
        a,b=scene(),scene(('alpha','beta','marker'))
        for d in (a,b): d['object_roles'][1]['role']=d['object_roles'][0]['role']
        self.assertEqual(classify(a,b),'contract_change')

    def test_rename_never_overrules_hard_requirement_or_new_task(self):
        a,b=scene(),scene(('x','y','z'));a['hard_requirements']=['a retained asset'];b['hard_requirements']=[]
        with self.assertRaises(ValueError): classify(a,b)
        p={'assessment':{'scenes':{'designs':[scene()]}}};self.assertFalse(revision_review(p,'next',p,'current',new_task=True)[1]['review_reused'])
