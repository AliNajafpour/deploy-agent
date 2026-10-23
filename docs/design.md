# Lyara deployment and operations assistant

The requested product covers initial deployment, subsequent releases, environment
configuration, domain/DNS/TLS setup, monitoring and evidence-based troubleshooting.
The human explicitly authorized implementation on 2026-10-23 and rejected limiting
the product to diagnosis. Build inline in this empty, non-Git project directory.

## Shared core

Python LangGraph coordinates inspect → observe → plan → approve → execute → verify.
SQLite checkpoints preserve approval pauses across restarts. A separate SQLite
action journal prevents duplicate writes when checkpoint execution is replayed.
An action with an ambiguous network result is not retried automatically.

One service powers a Persian web UI, CLI and stdio MCP. API-based structured
planning, Codex CLI and Claude Code CLI implement the same planning contract.
MCP exposes the same inspection, evidence, proposal and run-status tools to external
clients without requiring an internal LLM. Sensitive approval is completed in
the local UI/CLI, not by a model asserting that the user approved.

## Deployment

Inspect only within the configured workspace. Recognize Docker, Node, Python and
static projects. Read manifests as data, never execute repository scripts locally.
Build an immutable tar.gz snapshot; exclude credentials, VCS, virtual environments,
dependency trees and symlinks. Show file manifest and archive hash before approval.
Existing project configuration determines deployment platform; creating a project
requires an explicit platform and plan ID. Do not invent plan prices or IDs.
Upload source using the documented multipart endpoint, create a release, poll its
specific release ID and independently check HTTP health. A failed release stops
dependent actions. Redeploying a previously uploaded source supports recovery
without assuming an undocumented rollback endpoint.

## Operations and trust boundaries

Documented API operations include project creation, logs/events/releases/metrics,
environment updates, restart, scaling, resizing, domains, SSL and DNS zones/records.
All mutations receive specific human approval, bound to the immutable plan digest.
Read operations need no approval. Unknown or missing API data is reported honestly.
Environment updates use server-side environment references, never secret values in
LLM prompts/checkpoints. Merge existing variables to avoid deleting unrelated ones.
Credentials and secret-looking log values are redacted before persistence/model use.
No arbitrary shell or arbitrary API-path tool is exposed.

The web service defaults to loopback. Remote use requires an access token; verify
Origin/Host to prevent cross-site actions. Health checks allow only public HTTPS
destinations without redirects to private networks. The source workspace is a
trusted operator configuration, not an arbitrary request path.

## Interface and demo

Persian RTL operations desk: blue #195dcc, ink #172943, pale blue #eef4fb,
white #ffffff, red #b42335 and green #126344. System Persian font stack, separate
monospace data. Main element is a real operation timeline with evidence, plan,
approval and verification, alongside app selection and a deployment form.
Keyboard focus, visible labels, readable errors, mobile layout and reduced motion.
No JS build dependency. All displayed cloud/model text uses textContent.

An explicitly labeled offline demo uses the production graph with a stateful mock
HTTP transport and deterministic demonstration planner. The local execution lab
can instead use actual Codex plans and real fixture processes. The user selected
local acceptance and excluded live Liara testing from this delivery. Simulated
cloud tests do not establish live account compatibility. CLI model auth is reused
only by the selected backend.

## Evidence and reuse

Reuse the neighboring wallex project's LangGraph/SQLite pattern and its read-only
structured Codex CLI pattern, without importing financial logic or changing that
project. Install from its existing wheelhouse into this project's own .venv.
Official Liara PaaS/DNS OpenAPI snapshots are in docs/reference. Authentication and
deployment responses are tested with simulated transport; live account compatibility
is not claimed and live testing is outside the selected scope.
