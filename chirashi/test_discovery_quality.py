import ast,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import Mock
from discovery_quality import qualify_result,prepare_results,finder_queries,campaign_queries,select_discovery_query

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
        # 게시판 의도(홍보/광고 게시판 + cafe24 상품 Q&A 게시판)만 허용, 전화번호 광고 쿼리는 금지.
        self.assertTrue(all(('홍보' in x or '광고' in x or '상품' in x) for x in queries));self.assertFalse(any('010' in x for x in queries))
    def test_campaign_terms_create_deduplicated_board_searches(self):
        queries=campaign_queries('사무실 청소, 정기 청소\n서울 청소업체, 사무실 청소')
        self.assertEqual(len(queries),6)
        self.assertIn('"사무실 청소" "홍보게시판" 글쓰기',queries)
        self.assertTrue(all('게시판' in q and '글쓰기' in q for q in queries))
    def test_query_schedule_reserves_slots_for_campaign_even_at_full_finder_ratio(self):
        finder_cursor=secondary_cursor=0;selected=[]
        finder=['board search'];secondary=[('campaign search','search')]
        for index in range(10):
            query,_,finder_cursor,secondary_cursor=select_discovery_query(
                finder,secondary,index,1.0,finder_cursor,secondary_cursor)
            selected.append(query)
        self.assertEqual(selected.count('campaign search'),2)
        self.assertEqual(selected.count('board search'),8)
    def test_discovery_obeys_daily_limit_and_fifty_query_batch_cap(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='discover_once')
        def run(max_queries,query_limit,initial_queries=0):
            today='2026-01-02'
            state={'date':today,'queries':initial_queries,'found':0,'cursor':0,'fcursor':0}
            searches=[]
            env={'DISCO_FILE':'discovery.json','load_json':lambda *args:dict(state),
                 'save_json':lambda path,value:(state.clear(),state.update(value)),
                 '_kst_now':lambda:datetime(2026,1,2),'_board_finder_queries':lambda provider:['board'],
                 'campaign_queries':campaign_queries,'select_discovery_query':select_discovery_query,
                 '_is_url_pattern_query':lambda query:False,'build_queries':lambda cfg:[],
                 '_web_search_with_trace':lambda cfg,query:(searches.append(query) or [],'brave',None),
                 'add_candidates_from':lambda items,cfg,source:0,'screen_pending':lambda count:0,
                 'add_log':lambda *args:None,'time':SimpleNamespace(sleep=lambda seconds:None)}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'discover-once-test','exec'),env)
            result=env['discover_once']({'discover_daily_target':5000,'discover_query_limit':query_limit,
                                         'finder_ratio':1.0},max_queries)
            return result,state,searches
        result,state,searches=run(100,2)
        self.assertEqual(result['queries'],2)
        self.assertEqual(state['queries'],2)
        result,state,searches=run(500,100)
        self.assertEqual(result['queries'],50)
        self.assertEqual(len(searches),50)
        self.assertEqual(result['provider_results'],50)
        result,state,searches=run(50,1000000,4999)
        self.assertEqual(result['queries'],1)
        self.assertEqual(state['queries'],5000)
    def test_brave_fallback_trace_exposes_cooldown_without_hiding_provider_status(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_web_search_with_trace')
        brave_calls=[];logs=[];skip_until=[0.0]
        def brave(*args):
            brave_calls.append(args)
            raise RuntimeError('Brave API 402 usage limit')
        env={'_BRAVE_SKIP_UNTIL':skip_until,'time':SimpleNamespace(time=lambda:100.0),
             'brave_search':brave,'_free_search':lambda *args:[{'url':'https://example.com'}],
             'add_log':lambda *args:logs.append(args)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'search-trace-test','exec'),env)
        rows,source,reason=env['_web_search_with_trace']({'search_provider':'brave'},'term')
        self.assertEqual((len(rows),source,reason),(1,'free','brave_error'))
        self.assertEqual(skip_until[0],3700.0)
        rows,source,reason=env['_web_search_with_trace']({'search_provider':'brave'},'term')
        self.assertEqual(source,'free')
        self.assertEqual(reason,'brave_cooldown')
        self.assertEqual(len(brave_calls),1)
        self.assertTrue(logs)
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

class LearnedPurposeSelectionTests(unittest.TestCase):
    def test_contact_hit_is_skipped_without_rejecting_domain_or_promotional_board(self):
        from discovery_quality import qualify_result
        url='https://a.example/bbs/board.php?bo_table=online'
        for title in ['견적문의 글쓰기','후원 신청','광고/제휴문의','제품 사용후기']:
            result=qualify_result({'url':url,'title':title})
            self.assertFalse(result['accepted'])
            self.assertEqual(result['reason'],'specific_purpose_form')
        result=qualify_result({'url':'https://a.example/bbs/board.php?bo_table=promotion','title':'무료 홍보게시판 글쓰기'})
        self.assertTrue(result['accepted'])
        self.assertFalse(result['permission_proven'])
