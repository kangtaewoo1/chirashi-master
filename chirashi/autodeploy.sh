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
for file in app.py cafe24_gate.py; do
 curl -fsSL "https://raw.githubusercontent.com/kangtaewoo1/chirashi-master/$sha/chirashi/$file" -o "$stage/$file"
 test -s "$stage/$file"
done
venv/bin/python -m py_compile "$stage/app.py" "$stage/cafe24_gate.py"
if cmp -s app.py "$stage/app.py" && cmp -s cafe24_gate.py "$stage/cafe24_gate.py"; then exit 0; fi
backup="$DIR/backups/deploy-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
for file in app.py cafe24_gate.py; do
 if test -f "$file"; then cp -a "$file" "$backup/$file"; fi
done
systemctl stop chirashi
for file in app.py cafe24_gate.py; do cp "$stage/$file" "$file"; done
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
 for file in app.py cafe24_gate.py; do
  if test -f "$backup/$file"; then cp -a "$backup/$file" "$file"; fi
 done
 systemctl start chirashi
 printf '%s commit=%s unhealthy; restored backup\n' "$(date -u)" "$sha" >> /tmp/chirashi-autodeploy.log
 exit 1
fi
