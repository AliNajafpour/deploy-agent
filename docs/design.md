# Lyara deployment and operations assistant

The requested product covers initial deployment, subsequent releases, environment
configuration, domain/DNS/TLS setup, monitoring and evidence-based troubleshooting.
The application supports deployment and approved repair through one shared service.

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

Persian RTL interface follows Liara's dark graphite, mint and cyan visual direction.
Vazirmatn is bundled with its license; technical values use monospace.
ZIP upload and a short request lead to a compact approval and verification view.
Detailed evidence stays collapsed, and missing credentials are collected in forms.
Keyboard focus, visible labels, readable errors, mobile layout and reduced motion.
No JS build dependency. All displayed cloud/model text uses textContent.

An explicitly labeled offline demo uses the production graph with a stateful mock
HTTP transport and deterministic demonstration planner. The local execution lab
can instead use actual provider plans and real fixture processes. Simulated
cloud tests do not establish live account compatibility. CLI model auth is reused
only by the selected backend.

## Evidence and reuse

Official Liara PaaS/DNS OpenAPI snapshots are in docs/reference. Authentication and
deployment responses are tested with simulated transport; live account compatibility
is not claimed and live testing is outside the selected scope.

## Backend repair and monitoring

Repairs collect bounded existing backend files, prioritizing traceback paths within
the archive. Hash checks, path rules, secret checks and syntax-only validation run
before a new immutable artifact is presented for approval. Uploaded code and package
hooks never run on the assistant host. Downloadable patches preserve final-newline
semantics; the original source remains unchanged.

Opt-in watches and incidents persist in operations.sqlite. Two consecutive failed
public HTTPS probes reserve one incident and one diagnostic run. Missing or blocked
probes and incomplete telemetry remain unknown. Recovery closes the incident.
Diagnostics reuse source provenance from verified release history and cannot approve
mutations. A single-process scheduler shares the Assistant lock with operations;
this version supports one active instance and up to 25 watches.

The offline doctor checks operator setup before initializing the service. It neither
contacts providers nor creates state. Successful checks do not prove live API access,
DNS/TLS, persistent mounts or plan prices.

## Completion v2

Hosting selection now reads the account-scoped official catalog, bundle plans, networks
and personal/team wallet independently. Raw identity/storage credentials are discarded.
Manual choices are an explicit fallback. Source verification discovers conservative GET
routes, compares repeated samples and requires stable exact-release identity.

Deterministic diagnostics feeds the planner and persistent monitor. Unknown metric units
and stale samples remain unknown. Emergency control generations and explicit managed-site
scope are persisted and checked atomically with every action reservation. Already reserved
external writes may finish; old approvals never revive after controls change.

Public GitHub imports resolve to a commit before reusing ZIP validation. Provider admission
and retries are bounded on the configured endpoint; actual usage and unknown costs persist.
Coding handoffs are sanitized quoted data, available through web, CLI and read-only MCP.
See [completion-v2.md](completion-v2.md) for precise limits, checks and operator setup.
