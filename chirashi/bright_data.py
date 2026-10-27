"""Explicit API diagnostics; connectivity never grants posting permission."""
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / '.env')


def _key(value):
    value = str(value or '').strip()
    return '' if not value or any(x in value.lower() for x in ('your_', 'placeholder')) else value


class BrightDataClient:
    def __init__(self, session=None):
        self.api_key = _key(os.getenv('BRIGHT_DATA_API_KEY'))
        self.serp_zone = os.getenv('BRIGHT_DATA_SERP_ZONE', '').strip()
        self.session = session or requests.Session()
        self.proxy_enabled = os.getenv('BRIGHT_DATA_PROXY_ENABLED', 'false').lower() == 'true'
        self.web_unlocker_enabled = os.getenv('BRIGHT_DATA_WEB_UNLOCKER_ENABLED', 'false').lower() == 'true'
        self.last_error = None
        if self.api_key:
            self.session.headers.update({'Authorization': 'Bearer ' + self.api_key})
        # Initialization and import do not send requests.

    def active_zones(self):
        if not self.api_key:
            self.last_error = 'missing_api_key'
            return None
        try:
            r = self.session.get('https://api.brightdata.com/zone/get_active_zones', timeout=20)
            if r.status_code != 200:
                self.last_error = 'http_' + str(r.status_code)
                return None
            rows = r.json()
            if not isinstance(rows, list) or not all(isinstance(x, dict) and x.get('name') for x in rows):
                self.last_error = 'unexpected_response'
                return None
            self.last_error = None
            return rows
        except (requests.RequestException, ValueError):
            self.last_error = 'connection_or_response_error'
            return None

    def is_available(self):
        return self.active_zones() is not None

    def get_proxy_list(self):
        # /proxy/list was not a valid integration endpoint.
        self.last_error = 'proxy_not_configured'
        return []

    def get_unlocker_config(self):
        zone = os.getenv('BRIGHT_DATA_UNLOCKER_ZONE', '').strip()
        return {'zone': zone} if self.web_unlocker_enabled and zone else {}

    def solve_captcha(self, captcha_data):
        self.last_error = 'human_verification_required'
        return None


def brave_search_query(query, max_results=10):
    """Documented GET request once, without retries on rejected requests."""
    brave_search_query.last_error = None
    key = _key(os.getenv('BRAVE_SEARCH_API_KEY'))
    if not key:
        brave_search_query.last_error = 'missing_api_key'
        return None
    if not isinstance(query, str) or not query.strip() or len(query) > 600:
        brave_search_query.last_error = 'invalid_query'
        return None
    try:
        r = requests.get('https://api.search.brave.com/res/v1/web/search',
                         headers={'Accept': 'application/json', 'X-Subscription-Token': key},
                         params={'q': query, 'count': max(1, min(20, int(max_results))),
                                 'safesearch': 'moderate'}, timeout=20)
        if r.status_code != 200:
            brave_search_query.last_error = {402:'usage_limit_exceeded',401:'authentication_failed',
                                             403:'access_denied',422:'invalid_request',429:'rate_limited'}.get(
                                                 r.status_code, 'http_' + str(r.status_code))
            return None
        return r.json()
    except (requests.RequestException, ValueError, TypeError):
        brave_search_query.last_error = 'connection_or_response_error'
        return None


bright_data_client = BrightDataClient()


def get_bright_data_proxy():
    return None


def enable_web_unlocker():
    return bool(bright_data_client.get_unlocker_config())


def solve_captcha_with_bright_data(captcha_info):
    return bright_data_client.solve_captcha(captcha_info)
