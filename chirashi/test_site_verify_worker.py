import json
from pathlib import Path
import tempfile
import unittest
from flask import Flask
from site_verify_worker import add_job,database,inspect,menu_links,register,write_status


class NavigationWorkerTests(unittest.TestCase):
    def test_follows_visible_menu_to_writing_and_login_profiles_without_submitting(self):
        calls=[]
        pages={
            'https://a.example/':'<a href="/community">자유게시판</a>',
            'https://a.example/community':'<a href="/write">글쓰기</a><a href="/login">로그인</a>',
            'https://a.example/write':'<form action="/write_update"><input name="wr_subject" required><textarea name="wr_content" required></textarea></form>',
            'https://a.example/login':'<form action="/login"><input name="mb_id" value="PRIVATE"><input type="password" name="mb_password" value="SECRET"></form>'}
        def reader(url,session,domain):
            calls.append(url);return {'url':url,'http':200,'state':'http_response','html':pages[url]}
        result=inspect({'domain':'a.example','urls':['https://a.example/']},reader=reader)
        self.assertIn('https://a.example/write',calls)
        self.assertIn('https://a.example/login',calls)
        self.assertEqual(result['state'],'write_form_observed')
        self.assertFalse(result['publication_verified']);self.assertFalse(result['auto_ready'])
        self.assertEqual(result['new_posts_submitted'],0)
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('SECRET',json.dumps(result))
    def test_challenge_stops_navigation_without_reading_other_paths(self):
        calls=[]
        def reader(url,*args):
            calls.append(url);return {'url':url,'http':200,'state':'http_response','html':'<input name="captcha_key"><a href="/write">글쓰기</a>'}
        result=inspect({'domain':'a.example','urls':['https://a.example/']},reader=reader)
        self.assertEqual(len(calls),1);self.assertEqual(result['state'],'human_review_required')
    def test_navigation_rejects_mutation_and_external_links(self):
        links=menu_links('<a href="/write_update.php">글쓰기</a><a href="https://b.example/write">글쓰기</a><a href="/bbs/write.php?bo_table=free">글쓰기</a>', 'https://a.example/','a.example')
        self.assertEqual([x[1] for x in links],['https://a.example/bbs/write.php?bo_table=free'])
    def test_page_budget_is_bounded(self):
        def reader(url,*args):return {'url':url,'http':200,'state':'http_response','html':''.join('<a href="/bbs/'+str(i)+'">게시판</a>' for i in range(20))}
        result=inspect({'domain':'a.example','urls':['https://a.example/']},reader=reader,max_pages=3)
        self.assertEqual(len(result['pages']),3)
    def test_queue_deduplicates_and_does_not_store_account_credentials(self):
        with tempfile.TemporaryDirectory() as f:
            c=database(Path(f));entry={'domain':'a.example','urls':['https://a.example/'],'mb_password':'SECRET'}
            add_job(c,entry);add_job(c,entry,100);c.commit()
            self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT priority FROM jobs').fetchone()[0],100)
            self.assertNotIn('SECRET',c.execute('SELECT input FROM jobs').fetchone()[0]);c.close()
    def test_status_and_routes_are_readonly(self):
        with tempfile.TemporaryDirectory() as f:
            folder=Path(f)/'site_verification_worker';c=database(folder)
            write_status(c,folder,phase='waiting_for_candidates');c.close()
            app=Flask('verification-test');register(app,{'DATA_DIR':f});client=app.test_client()
            self.assertTrue(client.get('/api/site-verification-worker').json['alive'])
            self.assertEqual(client.get('/site-verification-worker').status_code,200)
            self.assertEqual(client.post('/api/site-verification-worker').status_code,405)
    def test_new_board_on_known_domain_is_queued_and_old_evidence_kept(self):
        with tempfile.TemporaryDirectory() as f:
            c=database(Path(f));add_job(c,{'domain':'a.example','urls':['https://a.example/board/old']})
            c.execute("UPDATE jobs SET state='done',result=?",(json.dumps({'state':'navigation_review_required'}),))
            add_job(c,{'domain':'a.example','urls':['https://a.example/board/new']},100)
            raw,state=c.execute('SELECT input,state FROM jobs').fetchone()
            self.assertEqual(state,'pending');self.assertEqual(json.loads(raw)['urls'][0],'https://a.example/board/new')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1);c.close()
