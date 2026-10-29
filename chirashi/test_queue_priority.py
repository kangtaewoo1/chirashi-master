"""Exercise queue priority without starting Flask or a browser."""
import ast
import threading
import unittest
from pathlib import Path

class QueuePriorityTest(unittest.TestCase):
    def run_scheduled(self, pending):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_publish_combo_to_site')
        lock=threading.Lock(); generated=[]
        def generate(*args,**kwargs):
            generated.append(True)
            raise RuntimeError('stop before browser')
        env={'load_sites':lambda:[{'id':'target'}], 'is_publishable':lambda s:True,
             '_site_lock':lambda sid:lock, '_manual_queue_pending':lambda sid:pending,
             'under_daily_limit':lambda *a:True, 'under_min_interval':lambda *a:(True,0),
             '_fix_kw_dong':lambda kw:kw, '_global_gen_sem':threading.Lock,
             'generate_article':generate, 'add_log':lambda *a:None}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),env)
        env['_publish_combo_to_site']({'id':'target'},{},'test','',{'publish_loop_enabled':True})
        self.assertFalse(lock.locked())
        return generated
    def test_pending_queue_prevents_scheduled_generation(self):
        self.assertEqual(self.run_scheduled(True),[])
    def test_no_pending_queue_allows_scheduled_generation(self):
        self.assertEqual(self.run_scheduled(False),[True])

if __name__=='__main__': unittest.main()


class ScheduledUnattendedQueueTest(unittest.TestCase):
    def test_scheduled_job_is_persisted_through_queue_without_direct_submission(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_publish_combo_to_site')
        lock=threading.Lock();queued=[];started=[]
        site={'id':'target','unattended_audit':{'state':'ready'}}
        env={'load_sites':lambda:[site],'is_publishable':lambda s:True,'_site_lock':lambda sid:lock,
             '_manual_queue_pending':lambda sid:False,'under_daily_limit':lambda *a:True,
             'under_min_interval':lambda *a:(True,0),'_fix_kw_dong':lambda kw:kw,
             '_global_gen_sem':threading.Lock,'generate_article':lambda *a,**k:('<p>body</p>','title'),
             'enqueue':lambda *a:(queued.append(a) or (1,0)),'wk_active':False,
             'start_workers':lambda n:started.append(n)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'scheduled-test','exec'),env)
        env['_publish_combo_to_site'](site,{},'room','room-id',{'publish_loop_enabled':True,'workers':1})
        self.assertEqual(len(queued),1)
        self.assertEqual(queued[0][3]['queue_source'],'scheduled_unattended')
        self.assertEqual(started,[1])
        self.assertFalse(lock.locked())


class PcQueueLeaseTest(unittest.TestCase):
    def environment(self):
        import copy,queue,time,secrets
        from types import SimpleNamespace
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        names={'_pc_queue_authorized','api_pc_queue_claim','api_pc_queue_start','api_pc_queue_report'}
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        for fn in functions:fn.decorator_list=[]
        store={};history=[];removed=[];finalized=[];payload={}
        q=queue.Queue()
        site={'id':'s','site_url':'https://example.com','bo_table':'free','platform':'gnuboard','mb_pass':'private','permission':True,'unattended_audit':{'state':'ready'}}
        env={'threading':threading,'time':time,'secrets':secrets,'queue':queue,'post_queue':q,'DATA_DIR':Path('/fake'),
             '_server_queue_site':lambda s:False,'_PC_QUEUE_LOCK':threading.RLock(),'_PC_SITE_LOCKS':{},'jsonify':lambda x:x,
             'request':SimpleNamespace(get_json=lambda **k:payload),'load_sites':lambda:[site],
             'load_json':lambda p,d:copy.deepcopy(store.get(str(p),d)),
             'save_json':lambda p,d:store.update({str(p):copy.deepcopy(d)}),
             '_node_beat':lambda *a,**k:None,'is_publishable':lambda s:True,'load_config':lambda:{},
             'under_daily_limit':lambda *a:True,'under_min_interval':lambda *a:(True,0),
             '_site_lock':lambda sid:threading.Lock(),'history_update':lambda *a,**k:history.append(k),
             '_persist_remove':lambda jid:removed.append(jid),'set_site_flag':lambda *a,**k:None,
             '_pc_finalize':lambda *a:finalized.append(a),'datetime':__import__('datetime').datetime,
             'hashlib':__import__('hashlib'),'urllib':__import__('urllib')}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'pc-queue-test','exec'),env)
        job={'job_id':'job','hist_id':'job','site':site,'title':'Exact title','content':'<p>Exact body</p>'}
        q.put(job);payload.update(protocol=1,node_id='pc')
        return env,payload,store,history,removed,finalized

    def test_claim_honors_limits_and_does_not_send_saved_passwords(self):
        env,d,store,h,removed,finalized=self.environment()
        env['under_daily_limit']=lambda *a:False
        self.assertIsNone(env['api_pc_queue_claim']()['job'])
        self.assertEqual(removed,['job'])
        env,d,store,h,removed,finalized=self.environment()
        job=env['api_pc_queue_claim']()['job']
        self.assertNotIn('mb_pass',job['site'])
        self.assertEqual(job['job_id'],'job')

    def test_start_once_and_report_retries_cannot_submit_or_count_twice(self):
        from types import SimpleNamespace
        import sys
        from unittest.mock import patch
        env,d,store,h,removed,finalized=self.environment()
        job=env['api_pc_queue_claim']()['job']
        d.update(job_id=job['job_id'],lease_token=job['lease_token'])
        self.assertTrue(env['api_pc_queue_start']()['ok'])
        self.assertEqual(env['api_pc_queue_start']()[1],409)
        d.update(ok=True,result_url='https://example.com/bbs/board.php?bo_table=free&wr_id=42')
        response=SimpleNamespace(text='<h1>Exact title</h1><p>Exact body</p>',url=d['result_url'],apparent_encoding='utf-8',raise_for_status=lambda:None)
        with patch.dict(sys.modules,requests=SimpleNamespace(get=lambda *a,**k:response)):
            self.assertTrue(env['api_pc_queue_report']()['verified'])
            self.assertTrue(env['api_pc_queue_report']()['already_recorded'])
        self.assertEqual(len(finalized),1)
        self.assertTrue(h[-1]['verification_receipt']['body_match'])
        self.assertEqual(removed,['job'])
