import json
from pathlib import Path
import unittest

from run_pilot import schedule


class RevisionScheduleTests(unittest.TestCase):
    def test_original_schedule_preserved(self):
        spec=json.loads(Path(__file__).with_name('study.json').read_text())
        spec.pop('trial_schedule',None)
        rows=schedule(spec)
        self.assertEqual(len(rows),16)
        self.assertEqual(sum(r['arm']=='A1' for r in rows),8)
        self.assertEqual(rows[0]['fixture'],'scale_valid')

    def test_separate_single_system_repeats(self):
        spec={'fixtures':[{'id':'valid'}], 'trial_schedule':[
            {'index':1,'wave':1,'slot':0,'arm':'A1','fixture':'valid','condition':'automatic','repeat':1},
            {'index':2,'wave':1,'slot':1,'arm':'A1','fixture':'valid','condition':'automatic','repeat':2}]}
        self.assertEqual(schedule(spec),spec['trial_schedule'])
        spec['trial_schedule'][1]['index']=1
        with self.assertRaises(ValueError):schedule(spec)


if __name__=='__main__':unittest.main()
