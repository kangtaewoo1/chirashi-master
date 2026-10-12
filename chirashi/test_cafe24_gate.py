import unittest
import ast
from pathlib import Path
from cafe24_gate import guarded_publish, stop_on_alerts, result_is_new, publishing_block

class GateTests(unittest.TestCase):
    def test_queue_requires_credentials_and_verified_board(self):
        site={'platform':'cafe24','site_url':'https://example.com/board/product/list.html?board_no=6',
              'bo_table':'6','login_required':True,'mb_id':'member',
              'write_test_status':'passed','verified_post_url':'https://example.com/article/test/6/42/'}
        self.assertEqual(publishing_block(site),'credentials_required')
        site['mb_pass_enc']='encrypted-credential'
        self.assertEqual(publishing_block(site),'')
        site['verified_post_url']='https://example.com/article/test/5/42/'
        self.assertEqual(publishing_block(site),'invalid_post_evidence')
        site['write_access_state']='human_verification_required'
        self.assertEqual(publishing_block(site),'human_verification_required')

    def test_router_does_not_fallback_after_access_denial(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        router=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='do_post')
        scope={'_strip_non_bmp':lambda x:x,'resolve_platform':lambda s:'cafe24',
               'cafe24_post':lambda *a,**k:(False,'[admin_only] denied')}
        exec(compile(ast.Module(body=[router],type_ignores=[]),'<router-test>','exec'),scope)
        result=scope['do_post']({'learned':{'write_url':'ignored','subject_sel':'ignored','content_sel':'ignored'}},'test','test')
        self.assertEqual(result,(False,'[admin_only] denied'))

    def test_admin_denial_stops_publish(self):
        reached=[]
        @guarded_publish
        def publish():
            stop_on_alerts(['관리자에게만 글쓰기 권한이 있습니다.'])
            reached.append('submit')
            return True, 'posted'
        ok, message=publish()
        self.assertFalse(ok)
        self.assertIn('[admin_only]',message)
        self.assertEqual(reached,[])

    def test_comment_denial_does_not_deny_new_posts(self):
        @guarded_publish
        def publish():
            stop_on_alerts(['관리자에게만 댓글 작성 권한이 있습니다.'])
            return True, 'allowed'
        self.assertTrue(publish()[0])

    def test_guard_context_is_restored(self):
        @guarded_publish
        def publish():
            stop_on_alerts(['관리자만 글쓰기'])
        publish()
        stop_on_alerts(['관리자만 글쓰기'])

    def test_result_rejects_list_login_and_other_host(self):
        base='https://www.brandl.co.kr/'
        for url in ('https://www.brandl.co.kr/board/product/list.html?board_no=6',
                    'https://www.brandl.co.kr/member/login.html',
                    'https://other.test/article/qa/6/99/', '등록됨', ''):
            with self.subTest(url=url):
                self.assertFalse(result_is_new(base,url,6))

    def test_same_or_older_article_rejected(self):
        prev='https://www.brandl.co.kr/article/qa/6/8837/'
        for n in (8836,8837):
            self.assertFalse(result_is_new(prev,f'https://brandl.co.kr/article/qa/6/{n}/'))
        self.assertTrue(result_is_new(prev,'https://brandl.co.kr/article/qa/6/8838/'))

    def test_wrong_board_rejected(self):
        self.assertFalse(result_is_new('https://brandl.co.kr/','https://brandl.co.kr/article/qa/4/9999/',6))

    def test_read_url_supported(self):
        self.assertTrue(result_is_new('https://brandl.co.kr/','https://brandl.co.kr/board/product/read.html?board_no=6&no=9999',6))

if __name__=='__main__':
    unittest.main()
