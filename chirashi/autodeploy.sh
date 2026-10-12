#!/bin/bash
set -euo pipefail
DIR=/opt/chirashi
cd "$DIR"
exec 9>"$DIR/.deploy.lock"
flock -n 9 || exit 0
stage=$(mktemp -d "$DIR/.deploy-stage.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
sha=$(curl -fsSL https://api.github.com/repos/kangtaewoo1/chirashi-master/commits/master | venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || exit 1
for file in app.py cafe24_gate.py form_schema.py hub_guard.py hub_report.py; do
 curl -fsSL "https://raw.githubusercontent.com/kangtaewoo1/chirashi-master/$sha/chirashi/$file" -o "$stage/$file"
 test -s "$stage/$file"
done
if ! venv/bin/python /opt/chirashi/hub_overlay.py "$stage"; then
 printf '%s AI override conflict; keeping current version\n' "$(date -u)" >> /tmp/chirashi-autodeploy.log
 exit 1
fi
venv/bin/python -m py_compile "$stage/app.py" "$stage/cafe24_gate.py" "$stage/form_schema.py" "$stage/hub_guard.py" "$stage/hub_report.py"
if cmp -s app.py "$stage/app.py" && cmp -s cafe24_gate.py "$stage/cafe24_gate.py" && cmp -s form_schema.py "$stage/form_schema.py" && cmp -s hub_guard.py "$stage/hub_guard.py" && cmp -s hub_report.py "$stage/hub_report.py"; then exit 0; fi
if test -f /opt/chirashi-ai/private/overrides.json; then
 /usr/bin/python3 /opt/chirashi-ai/hub_ai.py --validate-stage "$stage"
fi
backup="$DIR/backups/deploy-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
for file in app.py cafe24_gate.py form_schema.py hub_guard.py hub_report.py; do
 if test -f "$file"; then cp -a "$file" "$backup/$file"; fi
done
systemctl stop chirashi
for file in app.py cafe24_gate.py form_schema.py hub_guard.py hub_report.py; do cp "$stage/$file" "$file"; done
systemctl start chirashi
healthy=0
for attempt in $(seq 1 15); do
 code=$(curl -s -A Mozilla/5.0 -H 'Host: google.twseo.kr' -o /dev/null -w '%{http_code}' http://127.0.0.1:8888/login || true)
 if test "$code" = 200 && systemctl is-active --quiet chirashi; then healthy=1; break; fi
 sleep 2
done
if test "$healthy" = 1; then
 printf '%s commit=%s HTTP 200 deployed\n' "$(date -u)" "$sha" >> /tmp/chirashi-autodeploy.log
else
 systemctl stop chirashi
 for file in app.py cafe24_gate.py form_schema.py hub_guard.py hub_report.py; do
  if test -f "$backup/$file"; then cp -a "$backup/$file" "$file"; fi
 done
 systemctl start chirashi
 printf '%s commit=%s unhealthy; restored backup\n' "$(date -u)" "$sha" >> /tmp/chirashi-autodeploy.log
 exit 1
fi
