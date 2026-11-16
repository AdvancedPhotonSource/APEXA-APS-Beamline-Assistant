"""Admission tests: malformed evidence cannot silently become valid science."""
import json
from pathlib import Path
import tempfile
import unittest

import assess
from check_contract import check, parameters, read_json


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.task={'facts':{'pixel_pitch_um':150,'nominal_distance_mm':900},
                   'pipeline':'four-stage','requested_model':'none','zero_tolerance':1e-12,
                   'distance_policy_fraction':0.05}
        self.run={'gate':{'passed':True,'threshold_uE':100,'stage4_strain_uE_test':2}}
        self.execution={'effective_pixel_pitch_um':150,'stages':[{'call':2,
                         'before_refine_flags':{k:False for k in ['iso_R2','iso_R4','iso_R6',
                             *[f'a{i}' for i in range(1,7)],*[f'phi{i}' for i in range(1,7)]]}}]}
        self.param='Lsd 900000\npx 150.000000\npx 150.00\n'+''.join(f'p{i} 0\n' for i in range(15))
        self.save()

    def save(self):
        for name,data in [('task.json',self.task),('candidate_run.json',self.run),('execution_evidence.json',self.execution)]:
            (self.root/name).write_text(json.dumps(data))
        (self.root/'candidate.params').write_text(self.param)

    def check(self):
        return check(*(self.root/n for n in ('task.json','candidate.params','candidate_run.json','execution_evidence.json')))

    def tearDown(self):self.tmp.cleanup()

    def test_identical_duplicate_pitch_is_valid(self):
        self.assertTrue(self.check()['all_declared_checks_satisfied'])
        self.assertTrue(all(assess.source_contract(self.root,self.root/'candidate.params').values()))

    def test_wrong_scale_fails_both_independent_implementations(self):
        self.param=self.param.replace('900000','720000').replace('150.000000','120').replace('150.00','120')
        self.execution['effective_pixel_pitch_um']=120;self.save()
        self.assertFalse(self.check()['checks']['input']['satisfied'])
        self.assertFalse(assess.source_contract(self.root,self.root/'candidate.params')['nominal_distance'])

    def test_free_flags_detected_even_with_zero_exports(self):
        self.execution['stages'][0]['before_refine_flags']['a3']=True;self.save()
        self.assertFalse(self.check()['checks']['restriction']['satisfied'])
        self.assertFalse(assess.source_contract(self.root,self.root/'candidate.params')['restriction'])

    def test_missing_coefficient_is_not_zero(self):
        self.param=self.param.replace('p14 0\n','');self.save()
        self.assertFalse(self.check()['evidence_complete'])
        with self.assertRaises(ValueError):assess.source_contract(self.root,self.root/'candidate.params')

    def test_wrong_coefficient_names_are_not_complete_evidence(self):
        self.execution['stages'][0]['before_refine_flags']={f'unknown{i}':False for i in range(15)};self.save()
        self.assertFalse(self.check()['evidence_complete'])
        with self.assertRaises(ValueError):assess.source_contract(self.root,self.root/'candidate.params')

    def test_nan_export_rejected(self):
        self.param=self.param.replace('p14 0','p14 nan');self.save()
        self.assertFalse(self.check()['evidence_complete'])
        with self.assertRaises(ValueError):assess.read_params(self.root/'candidate.params')

    def test_conflicting_duplicate_pitch_rejected(self):
        self.param += 'px 100\n';self.save()
        self.assertFalse(self.check()['evidence_complete'])
        with self.assertRaises(ValueError):assess.read_params(self.root/'candidate.params')

    def test_nonfinite_gate_rejected(self):
        self.run['gate']['stage4_strain_uE_test']=float('nan');self.save()
        self.assertFalse(self.check()['evidence_complete'])
        with self.assertRaises(ValueError):assess.source_contract(self.root,self.root/'candidate.params')

    def test_duplicate_json_key_rejected(self):
        p=self.root/'duplicate.json';p.write_text('{"a":1,"a":2}')
        for reader in (read_json,assess.load):
            with self.assertRaises(ValueError):reader(p)

    def test_missing_manifest_is_preserved(self):
        r=assess.assess(self.root,self.root)
        self.assertEqual(r['manifest_status'],'missing');self.assertIsNone(r['incorrect_release'])

    def test_link_cannot_escape_workspace(self):
        (self.root/'link').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):assess.file_in(self.root,'link')
        with self.assertRaises(ValueError):assess.file_in(self.root,'../outside')


if __name__=='__main__':unittest.main()
