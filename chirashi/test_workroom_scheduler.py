"""Scheduler regressions without starting Flask or submitting any posts."""
import ast
import random
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TREE=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))

def functions(names,env):
    nodes=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'scheduler','exec'),env)
    return env

class ComboSchedulerTests(unittest.TestCase):
    def env(self):
        return functions({'_workroom_combos','_take_room_combo','_pick_combo_for_room','_pick_next_combo'},
            {'random':random.Random(42),'_WR_ORDER':{},'_WR_SIGNATURE':{},'_WR_CURSOR':{},
             '_WR_RR':[0],'_WR_PICK_LOCK':threading.Lock(),'_combo_region_ok':lambda c:True})

    def test_six_slots_take_666_distinct_combos_with_real_progress(self):
        e=self.env();room={'id':'room','keyword_csv':'\n'.join('keyword'+str(i) for i in range(666))}
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:e['_pick_combo_for_room'](room),range(666)))
        self.assertEqual(len({r[1]['_main'] for r in results}),666)
        self.assertEqual(sorted(r[2] for r in results),list(range(1,667)))
        self.assertEqual({r[3] for r in results},{666})
        self.assertEqual(e['_pick_combo_for_room'](room)[2],1)

    def test_duplicate_lines_and_same_size_edits(self):
        e=self.env();room={'id':'r','keyword_csv':'a\na\nb'}
        first=e['_pick_combo_for_room'](room);second=e['_pick_combo_for_room'](room)
        self.assertNotEqual(first[1],second[1]);self.assertEqual(first[3],2)
        room['keyword_csv']='c\nc\nd'
        result=e['_pick_combo_for_room'](room)
        self.assertEqual(result[2],1);self.assertIn(result[1]['_main'],('c','d'))

    def test_invalid_large_pool_is_not_used_as_fallback(self):
        e=self.env();e['_combo_region_ok']=lambda c:False
        room={'id':'r','keyword_csv':'\n'.join(str(i) for i in range(25))}
        self.assertIsNone(e['_pick_combo_for_room'](room))

    def test_no_ready_site_does_not_consume_or_log_combo(self):
        class StopLoop(Exception):pass
        from types import SimpleNamespace
        sleeps=[];picked=[];logs=[]
        def sleep(n):sleeps.append(n);raise StopLoop()
        cfg={'publish_loop_enabled':True,'node_publish_all':True}
        env={'add_log':logs.append,'load_config':lambda:cfg,'load_json':lambda *a:[{'id':'room'}],
             'WORKROOMS_FILE':'unused','_workroom_combos':lambda r:[{}],
             '_scheduled_ready_sites':lambda *a:[],
             '_pick_combo_for_room':lambda r:picked.append(r),'time':SimpleNamespace(sleep=sleep)}
        functions({'workroom_worker'},env)
        with self.assertRaises(StopLoop):env['workroom_worker'](0)
        self.assertEqual(picked,[])
        self.assertFalse(any('발행 시작' in x for x in logs))
        self.assertEqual(sleeps[0],30)

    def test_permission_limit_queue_and_inflight_are_all_required(self):
        sites=[{'id':x} for x in ['ready','permission','daily','interval','queue','busy','login']]
        sites[-1]['login_required']=True
        locks={s['id']:threading.Lock() for s in sites};locks['busy'].acquire()
        env={'load_sites':lambda:sites,'is_publishable':lambda s:s['id']!='permission',
             'under_daily_limit':lambda s,c:s['id']!='daily',
             'under_min_interval':lambda s:(s['id']!='interval',0),
             '_manual_queue_pending':lambda sid:sid=='queue','_site_lock':lambda sid:locks[sid]}
        functions({'_scheduled_ready_sites'},env)
        self.assertEqual([s['id'] for s in env['_scheduled_ready_sites']({},True)],['ready'])

if __name__=='__main__':unittest.main()
