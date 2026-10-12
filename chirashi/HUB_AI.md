# Vultr autonomous maintenance

The maintenance coordinator runs on Vultr and calls the existing NVIDIA API
configuration. No Codex desktop session, Windows scheduler, local Ollama, or PC
credential file is required. The normal publishing LLM configuration is unchanged.

## Schedule and responsibilities

- `chirashi-ai.timer`: boot startup and 15 minutes after the previous run ends.
- `chirashi-ai.service`: bounded oneshot coordinator, 15-minute deadline,
  768 MiB memory ceiling. An inactive successful oneshot is normal between cycles.
- Existing minute cron: service/network guard and aggregate headquarters report.
- Analyst receives public code, isolated test failures, health results and
  categorized recent failures. Contact details, credentials, post bodies and raw
  production error messages are not sent.
- Engineer proposes exact code replacements. No AI-generated commands run.
- Tests run as a dynamic unprivileged systemd user, without network, with the
  production app and private coordinator directory inaccessible.
- Reviewer independently evaluates the proposed fix after tests pass.
- Deployer applies verified overlays with matching upstream/content hashes,
  validates the complete deployed combination, backs up and verifies internal and
  external HTTP. Failed confirmation restores code and the prior overlay.

## Supported changes and limits

Automatic edits: `form_schema.py`, `cafe24_gate.py`, and the app functions
`_read_form_schema`, `_signup_form_measure`, `_manual_queue_pending`, `_site_is_pub`.
Protected coordinator, credentials, service definitions, tests, login functions,
CAPTCHA/access rules and bulk publication settings are outside AI edit scope.
Failures outside this range are analyzed and marked for attention.

Only reproduced trusted test failures trigger automatic code deployment. Daily
review does not force cosmetic changes. Recent publication failures are locally
classified before analysis and never directly retried. Equivalent incident
categories are not repeatedly sent to AI. Failed candidates have a six-hour
cooldown. AI calls are capped at 24 per UTC day; provider outages and limits are
reported rather than switching to an unapproved paid provider.

## Files and rollback

Coordinator: `/opt/chirashi-ai/hub_ai.py`. Trusted tests: `trusted/`.
Root-only `private/records/` contains per-cycle steps. `private/state.json`
contains counters, stage and last deployed revision. `runs/` holds baseline and
candidate test logs and local Git revisions. GitHub push credentials are not
needed: verified changes persist as root-owned `private/overrides.json` plus
`private/overrides/` files. The normal GitHub updater preserves these changes.
Matching upstream fixes are accepted; other conflicting upstream changes require
merge review and leave the current deployment running. The Codex heartbeat only
observes server results and handles such merge review, and is not the runtime.

Pause future AI work with `systemctl stop chirashi-ai.timer`; an in-flight run is
separate. Inspect `systemctl status chirashi-ai.service`, its journal, private
records and `/tmp/chirashi-autodeploy.log`. After manually restoring a version,
also restore the matching override manifest to prevent reapplication. Publishing
data and queued jobs are never modified by the maintenance coordinator.

## Verified first correction

2026-10-13 07:03:23 KST: actual server AI corrected hidden/disabled write-content
fields being counted as usable. Before: two trusted reproductions failed. After:
31 tests passed, independent review approved, production deployment and internal /
external HTTP succeeded. Local server revision:
`d10e64e2da623edd702d44e5c3a5d705b44c9ee2`.
Later expanded coordinator/privacy/conflict/rollback/isolation checks: 41 tests passed
in the Vultr isolation environment. No production outage was injected.

API implementation references: [NVIDIA chat-completion examples](https://docs.nvidia.com/nim/vision-language-models/1.7.0/examples/nemotron-3-nano-omni-30b-a3b-reasoning/api.html)
and [OpenRouter API](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion).
