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
