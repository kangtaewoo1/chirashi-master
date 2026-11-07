"""Bounded recovery for service failures; never retries publication jobs."""
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MAX_RESTARTS = 3
COOLDOWN = 1800


def decision(state, healthy, now, day):
    state = dict(state)
    if state.get('day') != day:
        state.update(day=day, restarts_today=0)
    state['consecutive_failures'] = 0 if healthy else state.get('consecutive_failures', 0) + 1
    restart = (not healthy and state['consecutive_failures'] >= 3
               and state.get('restarts_today', 0) < MAX_RESTARTS
               and now - state.get('last_restart', 0) >= COOLDOWN)
    state['status'] = 'healthy' if healthy else ('recovering' if restart else 'incident')
    if restart:
        state['last_restart'] = now
        state['restarts_today'] = state.get('restarts_today', 0) + 1
    state['checked_at'] = datetime.fromtimestamp(now, timezone.utc).isoformat()
    return state, restart


def probe(url, local=False):
    headers = {'User-Agent': 'Mozilla/5.0 TW-Hub-Guard/1'}
    if local:
        headers['Host'] = 'google.twseo.kr'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=12) as r:
            return r.status == 200 and 'password' in r.read(200000).decode('utf-8', errors='replace').lower()
    except Exception:
        return False


def run():
    import fcntl
    import time
    # The deployment job uses this same lock: never interrupt an in-flight deploy.
    with open('/opt/chirashi/.deploy.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        path = Path('/opt/chirashi/data/hub_guard.json')
        previous = json.loads(path.read_text()) if path.exists() else {}
        active = subprocess.run(['systemctl', 'is-active', '--quiet', 'chirashi']).returncode == 0
        local = active and probe('http://127.0.0.1:8888/login', True)
        public = probe('https://google.twseo.kr/login')
        now = time.time()
        state, restart = decision(previous, local, now, datetime.now(timezone.utc).strftime('%Y-%m-%d'))
        state.update(local_ok=local, public_ok=public)
        if local and not public:
            state['status'] = 'network_incident'
        if restart:
            result = subprocess.run(['systemctl', 'restart', 'chirashi'], timeout=60)
            time.sleep(3)
            recovered = result.returncode == 0 and probe('http://127.0.0.1:8888/login', True)
            state['status'] = 'recovered' if recovered else 'incident'
            state['local_ok'] = recovered
            state['last_action'] = 'restart_verified' if recovered else 'restart_failed'
        events = previous.get('events', [])
        if restart or previous.get('status') != state['status']:
            events.append({'at': state['checked_at'], 'status': state['status'], 'action': state.get('last_action', 'check')})
        state['events'] = events[-40:]
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state), encoding='utf-8')
        tmp.replace(path)
        print(json.dumps({k:state[k] for k in ('checked_at','status','restarts_today','local_ok','public_ok')}))


if __name__ == '__main__':
    run()
