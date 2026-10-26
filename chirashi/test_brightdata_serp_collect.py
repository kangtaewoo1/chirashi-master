import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from brightdata_serp_collect import parse_results,search,collect,board_quality,rank_board_candidates


class SerpCollectorTest(unittest.TestCase):
    def test_tutorial_or_google_challenge_is_not_a_board(self):
        for url in ('https://blog.example/post/bo_table','https://www.google.com/sorry/index?bo_table=free'):
            self.assertFalse(board_quality({'url':url,'title':'홍보게시판'})['board_candidate'])

    def test_board_rank_never_grants_permission(self):
        promotion={'url':'https://a.example/bbs/board.php?bo_table=ad','title':'홍보게시판'}
        general={'url':'https://b.example/bbs/board.php?bo_table=free','title':'자유게시판'}
        rows,excluded=rank_board_candidates([general,promotion])
        self.assertEqual(rows[0]['url'],promotion['url']);self.assertFalse(rows[0]['quality']['permission_proven'])

    def test_better_board_link_on_same_domain_is_kept_without_more_requests(self):
        request=Mock(side_effect=[self.response([{'link':'https://new.example/help'}]),
                                  self.response([{'link':'https://new.example/bbs/board.php?bo_table=ad','title':'홍보게시판'}])])
        with tempfile.TemporaryDirectory() as folder:
            r=collect({'brightdata_serp_api_key':'secret','brightdata_serp_zone':'site_discovery'},['one','two'],set(),Path(folder)/'result.json',request=request)
        self.assertEqual(r['new_candidate_domains'],1);self.assertEqual(r['supported_board_url_candidates'],1)
        self.assertEqual(request.call_count,2);self.assertFalse(r['candidates'][0]['permission'])
    def response(self,rows,status=200):return SimpleNamespace(status_code=status,json=lambda:{'organic':rows})
    def test_results_are_deduplicated_and_unapproved(self):
        rows=parse_results({'organic':[{'link':'https://new.example/bbs/board.php#x'},{'link':'https://new.example/bbs/board.php#y'},{'link':'javascript:bad'},{'link':'https://user:pass@bad.example'}]})
        self.assertEqual(len(rows),1);self.assertFalse(rows[0]['permission']);self.assertEqual(rows[0]['status'],'pending_review')
    def test_non_json_schema_is_not_success(self):
        for value in ({},[],{'organic':'wrong'}):
            with self.assertRaises(RuntimeError):parse_results(value)
    def test_request_uses_official_rest_and_secret_header(self):
        request=Mock(return_value=self.response([]))
        search({'brightdata_serp_api_key':'secret','brightdata_serp_zone':'site_discovery'},'board',request=request)
        args,kwargs=request.call_args;self.assertEqual(args[0],'https://api.brightdata.com/request')
        self.assertIn('brd_json=1',kwargs['json']['url']);self.assertEqual(kwargs['json']['format'],'raw')
    def test_error_response_is_not_retried_or_leaked(self):
        request=Mock(return_value=self.response([],402))
        with self.assertRaisesRegex(RuntimeError,'402') as error:
            search({'brightdata_serp_api_key':'secret','brightdata_serp_zone':'site_discovery'},'board',request=request)
        request.assert_called_once();self.assertNotIn('secret',str(error.exception))
    def test_missing_key_does_not_call_api(self):
        request=Mock()
        with self.assertRaises(RuntimeError):search({},'board',request=request)
        request.assert_not_called()
    def test_saved_candidates_exclude_known_domains_and_never_promote(self):
        request=Mock(return_value=self.response([{'link':'https://old.example/a'},{'link':'https://new.example/bbs/board.php'}]))
        with tempfile.TemporaryDirectory() as folder:
            result=collect({'brightdata_serp_api_key':'secret','brightdata_serp_zone':'site_discovery'},['board'],{'old.example'},Path(folder)/'result.json',request=request)
            self.assertEqual(result['new_candidate_domains'],1);self.assertEqual(result['publishable_sites_added'],0)
            self.assertEqual(result['estimated_usd_at_public_payg'],0.0015)
    def test_proof_cannot_run_unbounded_query_loop(self):
        request=Mock()
        with self.assertRaises(ValueError):collect({},['q']*4,set(),'unused.json',request=request)
        request.assert_not_called()


if __name__=='__main__':unittest.main()
