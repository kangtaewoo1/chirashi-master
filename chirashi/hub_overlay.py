"""Apply root-owned, tested AI overrides only to matching upstream content."""
import hashlib
import json
import shutil
import sys
from pathlib import Path


def apply(stage, private):
    manifest = private / 'overrides.json'
    if not manifest.exists():
        return
    value = json.loads(manifest.read_text())
    pending = []
    for name, entry in value.get('files', {}).items():
        if name not in {'form_schema.py','cafe24_gate.py','app.py'}:
            raise ValueError('Unknown override')
        source, dest = private / 'overrides' / name, stage / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Invalid override source')
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        if digest(source) != entry['approved_sha']:
            raise ValueError('Override integrity failure')
        if digest(dest) == entry['approved_sha']:
            # A later GitHub commit already contains the verified correction.
            continue
        if digest(dest) != entry['base_sha']:
            raise ValueError('Upstream changed; override needs merge: '+name)
        pending.append((source,dest))
    for source,dest in pending:
        shutil.copyfile(source,dest)


if __name__ == '__main__':
    apply(Path(sys.argv[1]),Path('/opt/chirashi-ai/private'))
