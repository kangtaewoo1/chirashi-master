import json
import tempfile
import unittest
from pathlib import Path
from hub_ai import apply_edits, parse_json, failure_summary, classify_failure, diagnose_recent_failures
from hub_overlay import apply
import hashlib
import sys
import os
import socket
from unittest.mock import patch


class MaintenancePolicyTests(unittest.TestCase):
    def test_incident_categories_use_evidence_not_login_guess(self):
        self.assertEqual(classify_failure('net::ERR_ADDRESS_UNREACHABLE'),'network')
        self.assertEqual(classify_failure('글쓰기 폼(wr_subject) 없음 — 로그인 필요/비표준 스킨'),'form')
        self.assertEqual(classify_failure('등록 확인 불가 — 게시판 규칙/에디터 셀렉터/승인대기 확인'),'unknown')
        self.assertEqual(classify_failure('로그인 실패 — 아이디/비번 불일치'),'credential')

    def test_reinspection_requires_permission_and_never_retries_uncertain_submission(self):
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            sites=[{'id':'a','site_url':'http://approved.example','permission':True,'platform':'gnuboard','bo_table':'free'}, {'id':'b','site_url':'http://denied.example','permission':False,'platform':'gnuboard'}]
            rows=[{'site_id':s['id'],'id':s['id'],'status':'fail','time':'2026-10-13 07:00:00','message':'등록 확인 불가'} for s in sites]
            (p/'sites.json').write_text(json.dumps(sites));(p/'history.json').write_text(json.dumps(rows))
            calls=[]
            def fetch(url):
                calls.append(url)
                return '<form action="write_update.php"><input name="wr_subject"><textarea name="wr_content"></textarea><input name="captcha_key">자동등록방지</form>'
            result=diagnose_recent_failures(p,datetime(2026,10,12,22,10,tzinfo=timezone.utc),fetch)
            self.assertEqual(len(calls),1)
            self.assertEqual(result[0]['category'],'human_verification')
            self.assertFalse(result[0]['retry_allowed'])
            self.assertTrue(result[0]['read_only'])
    @unittest.skipUnless(os.environ.get('TW_AI_ISOLATED_TESTS')=='1','Isolated server environment')
    def test_production_configuration_is_inaccessible(self):
        with self.assertRaises(OSError):
            Path('/opt/chirashi/data/config.json').read_bytes()

    @unittest.skipUnless(os.environ.get('TW_AI_ISOLATED_TESTS')=='1','Isolated server environment')
    def test_production_and_external_network_are_inaccessible(self):
        for address in (('127.0.0.1',8888),('1.1.1.1',443)):
            with socket.socket() as connection:
                connection.settimeout(0.2)
                self.assertNotEqual(connection.connect_ex(address),0)

    def test_failure_context_contains_no_identity_or_message(self):
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'history.json').write_text(json.dumps([{'status':'fail','time':'2026-10-13 07:00:00','message':'password secret 01012345678','site_name':'PRIVATE','title':'PRIVATE'}]))
            summary=failure_summary(p,datetime(2026,10,12,22,10,tzinfo=timezone.utc))
            self.assertEqual(summary['categories'],{'credential':1})
            self.assertNotIn('secret',json.dumps(summary))
            self.assertNotIn('PRIVATE',json.dumps(summary))

    def test_refuses_protected_files(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                apply_edits(Path(d),[{'file':'hub_ai.py','old':'x','new':'y'}])

    def test_requires_exact_match(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'form_schema.py').write_text('x = 1\nx = 1\n')
            with self.assertRaises(ValueError):
                apply_edits(p,[{'file':'form_schema.py','old':'x = 1','new':'x = 2'}])

    def test_rejects_new_io_and_imports(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'form_schema.py').write_text('x = 1\n')
            for new in ('import os\nx = 2', 'x = open("secret")'):
                with self.assertRaises(ValueError):
                    apply_edits(p,[{'file':'form_schema.py','old':'x = 1','new':new}])

    def test_app_functions_are_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'app.py').write_text('def login():\n    return False\n')
            with self.assertRaises(ValueError):
                apply_edits(p,[{'file':'app.py','old':'    return False','new':'    return True'}])

    def test_valid_edits_and_json(self):
        self.assertEqual(parse_json('```json\n{"ok":true}\n```'),{'ok':True})
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'form_schema.py').write_text('x = 1\n')
            original=apply_edits(p,[{'file':'form_schema.py','old':'x = 1','new':'x = 2'}])
            self.assertEqual(original['form_schema.py'],'x = 1\n')
            self.assertEqual((p/'form_schema.py').read_text(),'x = 2\n')

    def test_overlay_conflict_and_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); stage=root/'stage'; private=root/'private'
            stage.mkdir(); (private/'overrides').mkdir(parents=True)
            dest=stage/'form_schema.py'; source=private/'overrides/form_schema.py'
            dest.write_text('old'); source.write_text('new')
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            value={'files':{'form_schema.py':{'base_sha':digest(dest),'approved_sha':digest(source)}}}
            (private/'overrides.json').write_text(json.dumps(value))
            apply(stage,private); self.assertEqual(dest.read_text(),'new')
            apply(stage,private) # upstream already contains the approved correction
            dest.write_text('unrelated upstream')
            with self.assertRaises(ValueError): apply(stage,private)
            dest.write_text('old'); source.write_text('tampered')
            with self.assertRaises(ValueError): apply(stage,private)

    @unittest.skipUnless(sys.platform == 'linux','Linux deployment lock')
    def test_failed_confirmation_restores_runtime_and_overlay(self):
        import hub_ai
        import types
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); app=root/'app'; app.mkdir(); stage=root/'stage'; stage.mkdir()
            runtime=root/'ai'; (runtime/'private').mkdir(parents=True)
            (app/'form_schema.py').write_text('old\n'); (stage/'form_schema.py').write_text('new\n')
            def run(command,**kw):
                if command[0] == '/bin/bash':
                    (app/'form_schema.py').write_text('new\n')
                return types.SimpleNamespace(returncode=0)
            with patch.object(hub_ai,'APP',app),patch.object(hub_ai,'ROOT',runtime),patch.object(hub_ai.subprocess,'run',side_effect=run),patch('hub_guard.probe',return_value=False):
                with self.assertRaises(RuntimeError):
                    hub_ai.update_overlay(stage,{'form_schema.py':'old\n'})
            self.assertEqual((app/'form_schema.py').read_text(),'old\n')
            self.assertEqual(json.loads((runtime/'private/overrides.json').read_text())['files'],{})
