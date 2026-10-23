# Local fixture lab

User request: create simple through complex runnable test projects, including
intentional bugs, clarification and human approval, without a Liara account.

Extend existing tests and DemoCloud transport. Run only the explicitly registered
fixtures under tests/projects, using installed Python/Node and standard libraries.
Never execute arbitrary uploaded projects, npm install or Docker builds. Archive
and release requests still pass through the production LiaraClient and LangGraph.
The lab transport unpacks the actual source archive into a temporary directory,
launches the allowlisted fixture entrypoints and collects real process logs and
HTTP responses. Local health checks are confined to ports owned by this harness;
production public-HTTPS validation remains intact.

Fixtures: static; healthy Node; missing port; missing environment; HTTP 500 and
fixed counterpart; startup syntax error; wrong listening port; Python/SQLite API
and worker; transient fault that recovers after an approved restart; prompt
injection in logs. A dropped-response switch exercises ambiguous remote writes.

- [x] Write failing local scenario tests against expected application behavior,
  clarification, reject/approve, failed release gating and ambiguous writes.
- [x] Add independent deployable fixtures and bounded process lifecycle helper.
- [x] Extend the existing demo HTTP transport in tests/support only, retaining
  real upload/release contract and source archive validation.
- [x] Add python -m tests.lab runner and optional web UI / real Codex planning.
  Scripted plans test execution mechanics and are not an AI-quality benchmark.
  Real-model checks receive observations, never expected answers.
- [x] Run local integration suite, full regression suite and selected real-agent
  checks. Save machine-readable report and reproduction instructions.
