import importlib
import os
import unittest
from unittest.mock import Mock, patch
import bright_data as b


class ConnectionTests(unittest.TestCase):
    def client(self, response):
        session = Mock(); session.get.return_value = response
        with patch.dict(os.environ, {'BRIGHT_DATA_API_KEY':'test-only'}):
            return b.BrightDataClient(session), session

    def test_import_never_contacts_network(self):
        with patch.object(b.requests.Session, 'get', side_effect=AssertionError('unexpected call')):
            importlib.reload(b); b.BrightDataClient()

    def test_failure_never_counts_as_available_and_never_retries(self):
        for status in (401,403,404,429,500):
            client, session = self.client(Mock(status_code=status))
            self.assertFalse(client.is_available())
            session.get.assert_called_once_with('https://api.brightdata.com/zone/get_active_zones', timeout=20)
            session.headers.update.assert_called_once_with({'Authorization':'Bearer test-only'})

    def test_valid_zones_and_invalid_success_body_differ(self):
        for body, expected in [([{'name':'site_discovery','type':'serp'}],True), ({'error':'bad'},False)]:
            response=Mock(status_code=200); response.json.return_value=body
            client,_=self.client(response); self.assertEqual(client.is_available(),expected)

    def test_unsupported_helpers_do_not_fake_capabilities(self):
        client,session=self.client(Mock(status_code=200))
        self.assertEqual(client.get_proxy_list(),[]); self.assertIsNone(client.solve_captcha({}))
        session.get.assert_not_called()

    def test_brave_get_preserves_quota_error_without_retry(self):
        with patch.dict(os.environ,{'BRAVE_SEARCH_API_KEY':'test-only'}),patch.object(b.requests,'get') as get:
            get.return_value=Mock(status_code=402)
            self.assertIsNone(b.brave_search_query('query',50))
            self.assertEqual(b.brave_search_query.last_error,'usage_limit_exceeded')
            get.assert_called_once(); self.assertEqual(get.call_args.kwargs['params']['count'],20)

    def test_placeholder_does_not_send_request(self):
        with patch.dict(os.environ,{'BRAVE_SEARCH_API_KEY':'your_brave_search_api_key_here'}),patch.object(b.requests,'get') as get:
            self.assertIsNone(b.brave_search_query('query')); get.assert_not_called()


if __name__=='__main__':unittest.main()
