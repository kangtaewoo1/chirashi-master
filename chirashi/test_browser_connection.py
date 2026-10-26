import ast
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from cafe24_gate import normalize_browser_endpoint, browser_connection_error, unattended_block


class BrowserConnectionTest(unittest.TestCase):
    endpoint='https://brd-customer-test-zone-browser:secret@brd.superproxy.io:9515'

    def test_selenium_endpoint_validation_and_password_encoding(self):
        self.assertEqual(normalize_browser_endpoint(self.endpoint),self.endpoint)
        self.assertTrue(normalize_browser_endpoint(self.endpoint.removeprefix('https://').replace(':9515','')).endswith(':9515'))
        self.assertIn(':a%40b%3Ac@',normalize_browser_endpoint(self.endpoint.replace(':secret@',':a%40b%3Ac@')))

    def test_invalid_endpoint_never_echoes_credentials(self):
        for endpoint in ('', '***설정됨***', self.endpoint.replace('https:','http:'),
                         self.endpoint.replace('9515','9222'), self.endpoint.replace('brd.superproxy.io','elsewhere.example'),
                         self.endpoint+'?token=secret', self.endpoint+'/session'):
            with self.subTest(endpoint=endpoint.split('@')[-1]):
                with self.assertRaises(ValueError) as error: normalize_browser_endpoint(endpoint)
                self.assertNotIn('secret',str(error.exception))

    def probe(self, config=None, method='POST', error=None, title='Example Domain'):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_sbr_test')
        fn.decorator_list=[]
        driver=Mock();driver.title=title
        remote=Mock(return_value=driver)
        if error: driver.get.side_effect=error
        connection=Mock()
        selenium=ModuleType('selenium')
        selenium.webdriver=SimpleNamespace(Remote=remote,ChromeOptions=lambda:SimpleNamespace())
        remote_module=ModuleType('selenium.webdriver.chromium.remote_connection')
        remote_module.ChromiumRemoteConnection=connection
        env={'request':SimpleNamespace(method=method),'load_config':lambda:config or {},'jsonify':lambda value:value,
             'normalize_browser_endpoint':normalize_browser_endpoint,'browser_connection_error':browser_connection_error,'time':time}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'browser-probe-test','exec'),env)
        with patch.dict(sys.modules,{'selenium':selenium,'selenium.webdriver.chromium.remote_connection':remote_module}):
            result=env['api_sbr_test']()
        return result,driver,remote,connection

    def test_missing_config_and_get_never_start_browser(self):
        result,_,remote,_=self.probe()
        self.assertFalse(result['ok']);remote.assert_not_called()
        result,_,remote,_=self.probe(method='GET')
        self.assertEqual(result[1],405);remote.assert_not_called()

    def test_probe_uses_only_example_page_and_closes_session(self):
        result,driver,remote,connection=self.probe({'sbr_endpoint':self.endpoint,'sbr_enabled':False})
        self.assertTrue(result['ok']);self.assertFalse(result['publishing_enabled'])
        remote.assert_called_once();driver.get.assert_called_once_with('https://example.com/')
        driver.quit.assert_called_once();connection.return_value.set_timeout.assert_called_once_with(45)
        self.assertNotIn('secret',str(result));self.assertNotIn('endpoint',result)

    def test_failed_probe_sanitizes_secrets_and_closes_session(self):
        result,driver,_,_=self.probe({'sbr_endpoint':self.endpoint},error=RuntimeError('407 '+self.endpoint))
        self.assertFalse(result['ok']);self.assertNotIn('secret',str(result));self.assertNotIn('brd-customer',str(result))
        driver.quit.assert_called_once()

    def test_unexpected_page_is_not_success(self):
        result,driver,_,_=self.probe({'sbr_endpoint':self.endpoint},title='Login')
        self.assertFalse(result['ok']);driver.quit.assert_called_once()

    def test_gate_has_no_network_dependency_or_brightdata_availability_override(self):
        with patch('socket.create_connection',side_effect=AssertionError('gate must be offline')):
            self.assertEqual(unattended_block({'has_captcha':True}),'human_verification_required')
            self.assertEqual(unattended_block({'write_access_state':'admin_only'}),'human_verification_required')
            self.assertEqual(unattended_block({'platform':'gnuboard','unattended_audit':{'state':'ready'}}),'')
        tree=ast.parse(Path(__file__).with_name('cafe24_gate.py').read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(n,ast.ImportFrom) and n.module and 'bright_data' in n.module for n in ast.walk(tree)))

    def test_error_categories_are_useful_and_never_raw(self):
        self.assertIn('인증',browser_connection_error(RuntimeError('407 secret')))
        self.assertIn('잔액',browser_connection_error(RuntimeError('billing secret')))
        self.assertIn('시간',browser_connection_error(RuntimeError('timeout secret')))
        self.assertNotIn('secret',browser_connection_error(RuntimeError('secret')))


if __name__=='__main__': unittest.main()
