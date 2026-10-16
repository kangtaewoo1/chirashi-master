"""Fail-closed admission checks for Cafe24 publishing."""
import functools
import re
import threading
from urllib.parse import parse_qs, urlsplit

_context = threading.local()

class AccessStopped(Exception):
    pass

def access_reason(text):
    compact = re.sub(r'\s+', '', str(text or ''))
    if any(s in compact for s in ('관리자에게만글쓰기권한', '관리자만글쓰기', '관리자만작성', '관리자에게만작성권한')):
        return 'admin_only'
    return ''

def stop_on_alerts(texts):
    if getattr(_context, 'active', False) and any(access_reason(t) for t in texts):
        raise AccessStopped('[admin_only] 관리자 전용 게시판 — 자동 발행 제외')

def post_identity(url):
    try:
        p = urlsplit(str(url or ''))
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return None
        host = p.hostname.lower().removeprefix('www.')
        m = re.fullmatch(r'/article/[^/]+/(\d+)/(\d+)/?', p.path)
        if m:
            return host, int(m[1]), int(m[2])
        if p.path.startswith('/board/') and p.path.endswith('/read.html'):
            q = parse_qs(p.query)
            return host, int(q['board_no'][0]), int(q['no'][0])
    except (ValueError, KeyError, IndexError):
        pass
    return None

def result_is_new(previous, result, board=None):
    new = post_identity(result)
    if not new or new[1] <= 0 or new[2] <= 0:
        return False
    try:
        host = urlsplit(str(previous or '')).hostname
        if not host or host.lower().removeprefix('www.') != new[0]:
            return False
        if board and str(board).isdigit() and int(board) != new[1]:
            return False
        old = post_identity(previous)
        return not old or (old[:2] == new[:2] and new[2] > old[2])
    except ValueError:
        return False

def publishing_block(site):
    """Explain why a Cafe24 site cannot enter unattended publishing."""
    if site.get('maintenance_hold'):
        return 'maintenance_hold'
    if site.get('platform') != 'cafe24':
        return ''
    state = site.get('write_access_state')
    if state in ('admin_only', 'human_verification_required'):
        return state
    if site.get('login_required') and not (
        str(site.get('mb_id') or '').strip()
        and (site.get('mb_pass') or site.get('mb_pass_enc'))
    ):
        return 'credentials_required'
    if site.get('write_test_status') != 'passed':
        return 'verification_required'
    if not result_is_new(site.get('site_url'), site.get('verified_post_url'), site.get('bo_table')):
        return 'invalid_post_evidence'
    return ''


def guarded_publish(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        old = getattr(_context, 'active', False)
        _context.active = True
        try:
            return fn(*args, **kwargs)
        except AccessStopped as exc:
            return False, str(exc)
        finally:
            _context.active = old
    return wrapped


def unattended_block(site):
    """Only explicitly audited, permitted guest boards enter unattended queues."""
    if site.get('_manual_review_job'):
        return ''
    if site.get('manual_review_only'):
        return 'manual_review_only'
    if site.get('has_captcha') or site.get('write_access_state') in ('admin_only', 'human_verification_required'):
        return 'human_verification_required'
    if site.get('platform') != 'gnuboard' or site.get('login_required') or site.get('mb_id'):
        return 'unattended_adapter_unverified'
    if (site.get('unattended_audit') or {}).get('state') != 'ready':
        return 'unattended_audit_required'
    return ''
