import json
from pathlib import Path
import tempfile
import unittest
from verification_checkpoint import plan,save
from site_verify_worker import database,seed


class CheckpointTests(unittest.TestCase):
    def test_keeps_original_write_url_for_authenticated_return_without_claiming_login(self):
        entry={'domain':'a.example','urls':['https://a.example/bbs/write.php?bo_table=free'],'password':'SECRET'}
        r={'state':'account_verification_pending','flow_profile':{'write':{'boards':[
            {'url':entry['urls'][0],'write_mode':'new_post_requested'},
            {'url':'https://a.example/bbs/write.php?bo_table=free&wr_id=4','write_mode':'existing_item_related'}]}}}
        p=plan(entry,r)
        self.assertEqual(p['return_to_write']['urls'],entry['urls'])
        self.assertFalse(p['return_to_write']['authenticated_recheck_done'])
        self.assertFalse(p['automatic_rejection']);self.assertNotIn('SECRET',json.dumps(p))

    def test_old_human_and_network_failures_remain_in_coverage_and_review_queue(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as f:
            root=Path(f);audit=root/'site_reaudit_20261030';audit.mkdir()
            old=sqlite3.connect(audit/'audit.sqlite3');old.execute('CREATE TABLE jobs(input TEXT,result TEXT,state TEXT)')
            for domain,category,urls in [('human.example','human_verification_required',['https://human.example/']),('tls.example','tls_validation_failed',['https://tls.example/']),('invalid.example','saved_address_requires_review',[])]:
                old.execute('INSERT INTO jobs VALUES(?,?,?)',(json.dumps({'domain':domain,'urls':urls}),json.dumps({'category':category}), 'done'))
            old.commit();old.close();c=database(root/'site_verification_worker');seed(c,root)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM review_checkpoints').fetchone()[0],3)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],2)
            network=json.loads(c.execute("SELECT input FROM jobs WHERE domain='tls.example'").fetchone()[0])
            self.assertEqual(network['probe_budget'],1)
            save(c,{'domain':'human.example','urls':['https://human.example/']},{'state':'signup_route_observed'})
            seed(c,root)
            latest=json.loads(c.execute("SELECT record FROM review_checkpoints WHERE domain='human.example'").fetchone()[0])
            self.assertEqual(latest['state'],'signup_route_observed');c.close()
