"""Send only aggregate publishing metrics to TW AI headquarters."""
import json
import subprocess
import urllib.request
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from cafe24_gate import publishing_block, unattended_block


def snapshot(base):
    def read(name):
        value = json.loads((base / name).read_text(encoding='utf-8'))
        if not isinstance(value, list):
            raise ValueError('Expected list: ' + name)
        return value
    sites, history, queue = read('sites.json'), read('history.json'), read('queue.json')
    today = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')
    statuses = Counter(str(x.get('status', 'unknown')) for x in history)
    today_status = Counter(str(x.get('status', 'unknown')) for x in history if str(x.get('time', '')).startswith(today))
    ready = sum(bool(s.get('permission') and s.get('status') != 'rejected'
                     and s.get('write_test_status') == 'passed' and s.get('verified_post_url')
                     and not publishing_block(s) and not unattended_block(s)) for s in sites)
    guard = json.loads((base / 'hub_guard.json').read_text()) if (base / 'hub_guard.json').exists() else {}
    maintenance = json.loads((base / 'hub_maintenance.json').read_text()) if (base / 'hub_maintenance.json').exists() else {}
    import urllib.parse
    incidents_path=Path('/opt/chirashi-ai/private/incidents.json')
    incidents=json.loads(incidents_path.read_text()) if incidents_path.exists() else []
    cases_path=base/'maintenance_cases.json'
    pinned=json.loads(cases_path.read_text()) if cases_path.exists() else []
    current={str(x.get('site_id')):x for x in incidents}
    incidents=[current.get(str(x.get('site_id')),x) for x in pinned] if pinned else incidents
    by_id={str(x.get('id')):x for x in sites}; actions=[]
    tasks_path=base/'manual_action_states.json'
    tasks=json.loads(tasks_path.read_text()) if tasks_path.exists() else []
    for item in incidents:
        site=by_id.get(str(item.get('site_id')), {})
        if not site.get('permission'): continue
        host=urllib.parse.urlsplit(str(site.get('site_url',''))).hostname or ''
        if not host: continue
        live=next((t for t in reversed(tasks) if str(t.get('site_id'))==str(item.get('site_id')) and t.get('job_id') and t.get('status')=='waiting_input' and str(t.get('expires_at',''))>datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')),None)
        actions.append({'site_id':str(item.get('site_id')),'domain':host,
                        'kind':'access_hold' if site.get('maintenance_hold') else 'captcha' if item.get('category')=='human_verification' else 'review',
                        'status':'held' if site.get('maintenance_hold') else 'waiting_input' if live else 'no_active_session',
                        'checked_at':item.get('checked_at','')})
    return {'action_cases':actions[:4], 'schema': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'guard': {k: guard.get(k) for k in ('status','checked_at','local_ok','public_ok','restarts_today','last_action')},
            'maintenance': {k: maintenance.get(k) for k in ('status','checked_at','tested','deployed','commit','runtime','stage','cycles','ai_calls_total','ai_tokens_total','last_deployed_at','last_fix_revision','failure_categories','ai_wait_reason','ai_next_retry_at','maintenance_result','incident_summary','incident_checked_at')},
            'service_active': subprocess.run(['systemctl', 'is-active', '--quiet', 'chirashi']).returncode == 0,
            'sites_total': len(sites), 'sites_verified_ready': ready,
            'sites_rejected': sum(s.get('status') == 'rejected' for s in sites),
            'queue_pending': len(queue), 'history_total': len(history),
            'history_done': statuses['done'], 'history_failed': statuses['fail'] + statuses['failed'],
            'today_done': today_status['done'], 'today_failed': today_status['fail'] + today_status['failed']}


if __name__ == '__main__':
    data = snapshot(Path('/opt/chirashi/data'))
    token = Path('/opt/chirashi/hub-report.token').read_text().strip()
    req = urllib.request.Request('https://ai.twseo.kr/api/factory/chirashi-report',
        data=json.dumps(data).encode(), headers={'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json', 'User-Agent': 'TW-Hub-Reporter/1'})
    with urllib.request.urlopen(req, timeout=20) as response:
        if not json.load(response).get('ok'):
            raise RuntimeError('Report was not accepted')
    print('TW AI headquarters report accepted')
