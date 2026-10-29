import ast,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import Mock
from discovery_quality import qualify_result,prepare_results,finder_queries

class DiscoveryQualityTests(unittest.TestCase):
    def test_nonboard_results_are_dropped_without_any_request(self):
        for url in ['https://example.com/','https://blog.example.com/news/123','https://example.com/products/item','https://google.com/search?bo_table=ad']:
            self.assertFalse(qualify_result({'url':url,'title':'홍보게시판 글쓰기 안내'})['accepted'])
    def test_actual_board_and_login_only_board_urls_remain_candidates(self):
        for url in ['https://example.com/bbs/board.php?bo_table=ad','https://example.com/boards/promotion','https://example.com/index.php?mid=promo']:
            result=qualify_result({'url':url,'snippet':'로그인 후 홍보게시판 글쓰기'})
            self.assertTrue(result['accepted']);self.assertFalse(result['permission_proven'])
    def test_articles_become_board_urls_and_keep_board_number(self):
        cases={
          'https://x.example/article/name/6/123/':'https://x.example/board/list.html?board_no=6',
          'https://x.example/board/read.html?board_no=4&no=123':'https://x.example/board/list.html?board_no=4',
          'https://x.example/bbs/board.php?bo_table=ad&wr_id=123&page=2':'https://x.example/bbs/board.php?bo_table=ad'}
        for url,want in cases.items():self.assertEqual(qualify_result({'url':url})['url'],want)
    def test_deduplicate_board_and_rank_explicit_promotion_first(self):
        rows,skipped=prepare_results([{'url':'https://x.example/bbs/board.php?bo_table=free&wr_id=1'},
            {'url':'https://x.example/bbs/board.php?bo_table=free&wr_id=2','title':'홍보게시판'},
            {'url':'https://y.example/bbs/board.php?bo_table=free'}, {'url':'https://z.example/about'}])
        self.assertEqual(len(rows),2);self.assertEqual(skipped,1);self.assertEqual(rows[0]['title'],'홍보게시판')
    def test_queries_use_board_intent_instead_of_phone_ads(self):
        queries=finder_queries();self.assertGreaterEqual(len(queries),10)
        self.assertTrue(all('홍보' in x or '광고' in x for x in queries));self.assertFalse(any('010' in x for x in queries))
    def test_no_form_cache_prevents_repeat_network_but_not_manual_or_login_board(self):
        import time,secrets
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='add_candidates_from')
        cache={};cands=[];screen=Mock(return_value={'reachable':True,'write_form':False})
        env={'DATA_DIR':Path('/fake'),'_cand_lock':threading.Lock(),'time':time,'secrets':secrets,
             'load_cands':lambda:cands,'save_cands':lambda rows:None,'load_sites':lambda:[],
             'load_rejected_domains':lambda:[],'_domain_of':lambda u:u.split('/')[2],
             '_is_blacklisted':lambda u:False,'load_json':lambda *a:dict(cache),
             'save_json':lambda path,v:(cache.clear(),cache.update(v)),
             'precheck_search_result':lambda u:{'reachable':True,'title':'게시판'},'screen_candidate':screen,
             'ERROR_PAGE_HINTS':[],'add_log':lambda *a:None,'_kst_now':datetime.now,'score_candidate':lambda r:10,
             're':__import__('re')}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'candidate-test','exec'),env)
        item={'url':'https://x.example/bbs/board.php?bo_table=ad'}
        self.assertEqual(env['add_candidates_from']([item],{}),0)
        self.assertEqual(env['add_candidates_from']([item],{}),0);screen.assert_called_once()
        cache.clear();screen.return_value={'reachable':True,'write_form':False,'login_required':True,'platform':'gnuboard'}
        self.assertEqual(env['add_candidates_from']([item],{}),1);self.assertEqual(cache,{})
