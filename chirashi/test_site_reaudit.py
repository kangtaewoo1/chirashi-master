import tempfile,json,socket,unittest
from pathlib import Path
from unittest.mock import Mock
from site_reaudit import clean_url,safe_public,snapshot,view_page

class SiteReauditTests(unittest.TestCase):
    def test_get_routes_cannot_transmit_credentials_or_trigger_mutations(self):
        for u in ['https://a.example/logout','https://a.example/write_update.php','https://user:secret@a.example/','file:///etc/passwd','https://a.example:abc/','https://a.example/?act=procBoardInsertDocument']:
            self.assertEqual(clean_url(u),'')
        self.assertEqual(clean_url('https://a.example/bbs/write.php?bo_table=free&token=private'),'https://a.example/bbs/write.php?bo_table=free')
    def test_nonpublic_addresses_are_blocked_before_get(self):
        resolver=lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with self.assertRaises(ValueError):safe_public('https://a.example/',resolver)
    def test_snapshot_covers_rejected_only_and_deduplicates_without_secrets(self):
        with tempfile.TemporaryDirectory() as f:
            p=Path(f)
            (p/'sites.json').write_text(json.dumps([{'id':'s','site_url':'https://a.example/','mb_id':'owner','mb_pass':'secret','permission':True}]))
            (p/'candidates.json').write_text(json.dumps([{'id':'c','url':'https://www.a.example/bbs/board.php?bo_table=free','status':'rejected'}]))
            (p/'rejected_domains.json').write_text(json.dumps({'domains':['a.example','b.example'],'log':[]}))
            rows=snapshot(p);self.assertEqual(len(rows),2);self.assertNotIn('secret',json.dumps(rows));self.assertTrue(all(x['rejected'] for x in rows))
            self.assertEqual(len(next(x for x in rows if x['domain']=='a.example')['records']),2)
    def test_comment_form_is_not_a_write_form(self):
        r={'html':'<form><textarea name="wr_content"></textarea></form>','url':'https://a.example/','http':200,'state':'http_response'}
        self.assertEqual(view_page(r,'entry')['form_state'],'form_unavailable')
    def test_measurement_stores_field_definitions_not_values(self):
        r={'html':'<form action="write_update.php"><input name="wr_subject" required><input type="password" name="wr_password" value="private"><textarea name="wr_content" required></textarea></form>','url':'https://a.example/bbs/write.php','http':200,'state':'http_response'}
        out=view_page(r);self.assertEqual(out['form_state'],'measured');self.assertNotIn('private',json.dumps(out));self.assertNotIn('html',out)
