# Competition completion implementation plan

> For agentic workers: use subagent-driven-development for the independent repair, monitoring and readiness tasks; integrate and review each before delivery.

Goal: close the substantive gaps in challange.md while preserving the novice ZIP interface, explicit approval and existing Liara API adapter.

Architecture: retain the single-process FastAPI/LangGraph/SQLite service. Extend staged repairs to bounded existing backend source. Persist opt-in read-only monitoring in the same Store and expose it through the web interface. Add an offline deployment doctor, without requiring model/cloud keys merely to inspect setup.

Spec: challange.md and the user's quoted organizer clarification that source-code repair is preferred. Demo video is deferred explicitly by the user on this turn. The introductory video URL is unavailable; no claim to have watched it.

Constraints: reuse installed dependencies and stdlib; never run uploaded application code/hooks on the assistant host. No live Liara purchase/deployment or Liara credential transmission. Two authorized BoxAPI scenarios use the existing model key. Keep BoxAPI Luna defaults. Never let monitoring execute cloud mutations or bypass reviewed plan digests. No sample project catalog or technical setup fields in the main UI. Current deployment remains one operator/one active instance.

## 1. Bounded backend bug repair

- [x] Extend repair.py extraction/validation to existing Python/backend JavaScript source, prioritizing paths implicated by tracebacks and retaining manifests within the bounded model context.
- [x] Keep traversal, secret, hash, file size, syntax, lockfile and original-source protections. New arbitrary backend files and database migrations remain excluded.
- [x] Update model repair instructions to permit defect correction while forbidding unrelated product/UI changes.
- [x] Test a nested backend HTTP 500 fix, rejection, exact approved artifact, unchanged originals, and forbidden paths/secrets.
- [x] Integrate source context in service.py and expose the same bounded inspection through MCP.

## 2. Persistent opt-in monitoring

- [x] Add monitoring.py using Store.connect for watch settings, observations and incidents; one read-only tick at a time.
- [x] Require two consecutive failed health checks for an incident; represent missing telemetry as unknown; deduplicate until recovery.
- [x] Start one read-only diagnostic plan per confirmed incident; retain its run ID, never execute its proposed writes.
- [x] Add authenticated watch/incident endpoints and a simple per-site switch/status view; the scheduler runs only for explicitly enabled watches.
- [x] Verify persistence, cooldown/deduplication, recovery, missing data, redaction, opt-out and zero mutations.

## 3. Operator readiness and actual CLI instructions

- [x] Add readiness.py returning safe offline checks for Python/Node, provider/key presence, Liara identity, public host, private data and hosting choices.
- [x] Add python -m lyara doctor before Assistant initialization, with JSON output and nonzero exit on blocking configuration errors.
- [x] Document exactly which commands run on the developer computer and which operations use the hosted API; include the existing GitHub deployment route.
- [x] Test missing keys and malformed setup without showing their values or accessing the network.

## 4. Delivery and review

- [x] Publish an honest requirement/evidence mapping and reconcile outdated deployment-only limitations in README/docs.
- [x] Run focused tests, one easy and one hard real-model scenario if applicable; do not replay all model fixtures.
- [x] Verify browser controls in isolated test state and keep production history free of test projects.
- [x] Review security and approval boundaries independently; fix important findings, scan staged content for secrets, commit and push.
- [x] Report remaining account-side deployment/credentials and deferred video explicitly; never claim a score or live Liara success without evidence.
