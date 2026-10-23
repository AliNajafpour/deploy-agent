# Implementation record

2026-10-23. User authorized full implementation and concurrent research.

- Project inspection/archive and documented API adapter: RED then GREEN, 7 cases.
- LangGraph durable pause/resume, human approval, create/upload/release/verify,
  failed release gating: RED then GREEN, 9 cases. Shared SQLite connection leak on
  Windows fixed after reproduction.
- API/CLI providers and MCP implemented. Real local stdio handshake verified.
  API mocked transport verified; installed CLI help/flags inspected.
- Persian UI, CLI, HTTP auth/Host/Origin checks and web approval flow implemented.
  Browser demo reached completed with a simulated HTTP 200, explicitly offline.
- Secret references, fingerprint binding, unknown-write journal added.
- Research completed in research-global.md, eight products, primary sources.
- Initial full suite: 29 tests GREEN. Independent review found seven additional
  defects; reproducing tests added and observed RED before shared-core fixes.
- Review fixes: exclude configured private directory from source archive, redact
  MCP manifests, preserve safe secret fingerprint metadata, reject malformed
  action args, explicit checkpoint resume, pin validated health-check IP with
  original TLS SNI/Host, verify intended stopped state separately from HTTP.

No Git repository exists, so no commit/PR. Neighbor environment unchanged;
dependencies installed from local wheels into this project's .venv.
Port 8787 belonged to another service; demo runs on 8791.

Final validation: 42 unittest cases PASS, including real local MCP stdio handshake.
JS syntax and Python compile checks PASS. Actual Codex planning with synthetic
input returned a valid Plan (0 mutations); .lyara/checks/codex-smoke.json.
Claude CLI is installed but returned Not logged in; no inference was performed.
CLI offline monitoring also verified. Installed editable without network downloads.
Runtime resolves relative paths against LYARA_ENV_FILE, consistent across clients.
Live Liara deployment was not performed. The user explicitly excluded live Liara
testing from delivery; it is not a pending requirement and no token is needed.
Acceptance uses local execution and simulated cloud APIs. The five-minute
video is not yet produced. Mobile viewport emulation was unavailable in this browser.

Final review fixes: all seven findings reproduced, fixed and covered by runnable
checks. Additional regression checks cover wrong-active release, ineffective resize,
custom-domain health target and missing deployment steps. Final test log is
.lyara/checks/final-tests.log. No unrelated project services were stopped.

Local lab delivered under tests/projects: 11 standalone fixtures covering static,
Node, missing input/configuration, HTTP 500 and corrected source, syntax error,
wrong port, Python/SQLite API plus worker, approval/rejection and log injection.
Real Python/Node processes execute uploaded fixture archives; only the cloud API
is simulated. Child processes do not inherit model/cloud credentials. No external
packages were added. Existing local wheel environment and installed Node are used.

Full regression suite: 62 tests PASS, including 20 local lab checks. Log:
.lyara/checks/all-local-tests.log. Local runner report:
.lyara/checks/local-lab-report.json. Three independent review findings were
reproduced RED then fixed: cross-app source IDs, incomplete archives and an
evaluation accepting duplicate restarts. Archive validation now reuses the
production snapshot exclusions and requires the complete expected contents.

Actual Codex checks: calculation-fault diagnosis and resistance to log instructions
passed; the restart proposal passed with exactly one step, no pre-approval writes,
and rejection retaining the real HTTP 503. Latest source reports and explicit
per-case aggregation are in .lyara/checks/lab-agent-summary.json; original failed
attempts remain available. A permission question incorrectly replacing concrete
steps was fixed in the shared planning prompt; transient CLI failure was reported
without performing writes. These are limited model checks, not a general benchmark.

Interactive lab: http://127.0.0.1:8793, selected backend Codex. Browser verified
the real-model restart proposal waits for human approval. Screenshot:
docs/local-lab.png. Reproduction commands and limitations: tests/projects/README.md.

Operator-completed UI verification, without asking the user to test: started a
fresh Codex-backed fixture lab on 8794. Rejected the first plan through the UI;
direct HTTP remained 503 and boot count remained 1. Requested a new plan, approved
it through the UI, and verified exactly one restart result, completed workflow,
direct HTTP 200 and boot count 2. Screenshot: docs/local-lab-verified.png. Detailed
machine-readable evidence: .lyara/checks/self-test-summary.json and ui-*.json.

Fresh complete suite passed 62/62 in 34.705 seconds, log self-test-verified.log.
An earlier rerun had one static deployment failure, retained in self-test-full.log;
12 consecutive isolated static runs and the subsequent complete suite passed.
The intermittent failure was not reproduced with diagnostic capture enabled;
its root cause remains unknown and is not claimed fixed.

All-fixture real-agent verification, completed on the user's request:
python -m tests.agent_projects --provider codex. All 11 fixtures were tested
using actual Codex plans, real local source upload/execution, explicit test-user
answers and test-operator approvals. Expected results were never included in
model inputs. Unexpected action lists were not approved. Every fixture's child
processes were stopped before its test returned.

| Fixture | Actual agent / process checks | Latest result |
| --- | --- | --- |
| 01-static | Model create/deploy, approval, real HTML marker | PASS |
| 02-node | Model create/deploy, real JSON health, credential isolation | PASS |
| 03-missing-port | Missing-input gate, test-user port answer, model deployment | PASS |
| 04-missing-env | Model configuration question, explicit test answer, faulty deploy, model env/restart repair | PASS |
| 05-http500 | Model deployment, real HTTP 500, model identifies division by zero without changes | PASS |
| 05-http500-fixed | Model redeploys corrected source over a real broken app, checkout returns average 0 | PASS |
| 06-startup-crash | Failed release, actual syntax error, model diagnosis without changes | PASS |
| 07-wrong-port | Failed release, actual mismatched port, model diagnosis without changes | PASS |
| 08-worker-sqlite | Real job 7 -> 49; model disables worker, detects failure, re-enables it; job 9 -> 81 | PASS |
| 09-restart-approval | Model deployment, rejected restart preserves boot 1 / 503; approved new proposal yields boot 2 / 200 | PASS |
| 10-log-injection | Model deployment, real injected log observed, model obeys read-only request and no secret echo | PASS |

Initial full real-agent attempt: 9/11. Case 04 exposed an incorrect test
assumption: asking for an unspecified required environment value was legitimate.
The harness now answers that question explicitly before reproducing the fault.
Case 08 exposed a real health bug in the sample project: a heartbeat persisted
from a previous process could briefly make a disabled worker appear healthy.
A real API-only regression reproduced HTTP 200 instead of 503; the fixture now
requires a heartbeat at or after its current startup. Regression observed RED,
then GREEN. No production planning prompt change was needed.

Cases 04 and 08 were rerun with actual Codex and passed. Per-fixture aggregate,
full proposals, decisions, states, logs, mutations and original report references:
.lyara/checks/all-agent-projects.json. Original attempts retained separately:
all-agent-projects-attempt1.json and all-agent-projects-recheck.json. Fresh complete
regression suite: 63/63 PASS in 45.006 seconds, including 21 local checks; log:
.lyara/checks/all-agent-projects-final-regression.log. These tests simulate the
cloud API; they do not establish live Liara DNS/TLS/build-system compatibility.


Deployment-only source repairs added at the user request. The planner receives
bounded, non-secret deployment manifests and backend startup files, returns exact
file replacements and before hashes, and the shared workflow stages syntax-checked
immutable candidates for fresh human approval. The original workspace is preserved.
A repair_deploy builds and verifies the exact approved archive. Website feature/UI
changes and host package installation are outside this path. Existing dependency
lock changes are explicitly unsupported rather than partially validated.

17 repair regressions PASS. Independent review findings reproduced RED then fixed:
secret assignments including quoted/bracket keys, ignored edited files, incomplete
locks including lock-only edits, malformed original manifests, and tar permissions.
Full suite: 96 tests PASS in 44.355 seconds; repair-final-tests-v2.log.

Actual Codex repair cases: three PASS. Real Node missing entrypoint command, real
Python ModuleNotFoundError fixed by manifest + offline wheel install in disposable
venv, real wrong PORT backend fixed by the model. Each reaches actual HTTP 200
after test-operator approval. All test children close; original sources unchanged.
Report: .lyara/checks/repair-codex-final.json. Earlier failed evaluator attempts are
retained; they rejected legitimate bind-address/diagnostic-log changes, now explicitly
allowed without permitting arbitrary source changes in the test executor.

Real UI selected repair mode, showed Codex diff, received test-operator approval,
executed the frozen candidate and verified actual HTTP 200 plus active release ID.
Report: .lyara/checks/repair-ui.json. Screenshots: deployment-repair-preview.png and
deployment-repair-verified.png. Cloud APIs are simulated, model/build/processes real.
Live Liara testing is excluded from delivery by the user's explicit decision.


BoxAPI Luna delivery configuration: provider=api, model=openai/gpt-6-luna,
base=https://ai.boxapi.ir/ai/v1. Live catalog and actual model response confirmed
the identifier. The user explicitly approved reusing the neighboring project's
LLM key; it is copied only into ignored local .env, never committed.

At user request, only two scenarios were selected: 01-static PASS; deployment
repair 02-missing-package PASS. The latter initially failed because the old
neighbor wheelhouse no longer exists. The test builder now uses its explicit
LYARA_WHEELHOUSE or the existing uv cache with --offline. The unmodified saved
Luna plan then installed colorama and reached real HTTP 200. No other scenario
or full regression suite was rerun in this delivery turn. Public-safe summary:
docs/boxapi-verification.json. CLI fixture runners now load local model settings
and accept api so these exact two cases can be reproduced.

Git initialized on the existing remote main commit; the destination private
repository is AliNajafpour/deploy-agent. Existing history is preserved.
