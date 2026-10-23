# Lyara implementation plan

Goal: deliver the full deployment/operations workflow with interchangeable model
providers and MCP integration, with executable offline checks and a usable UI.
Spec: docs/design.md. Execute inline as explicitly requested by the user.

Global constraints: Python >=3.11; local wheelhouse; no sibling-project changes;
no cloud mutations without human approval; no invented endpoints or plan IDs.

Review focus: credentials in logs/archives, replayed mutations, source path escape,
unknown release states, HTTP success that masks a failed deployment.

## Task 1: project inspection and documented Liara operations
- [x] Write unittest cases for archive exclusions/path escape, source upload and
  release request shapes, error classification and safe environment merging.
- [x] Run tests to observe missing functionality, implement project.py, models.py,
  security.py and liara.py, run the tests again.

## Task 2: durable approval and execution
- [x] Test approval bypass/rejection, exact plan binding, restart/resume, failed
  deployment, repeated requests and ambiguous network writes.
- [x] Implement store.py and service.py using SQLite and LangGraph interrupts.
- [x] Verify deployment creates, uploads, releases and checks its specific release;
  dependent domain actions stop after failed deploy.

## Task 3: interchangeable planners and MCP
- [x] Test structured model output rejection and API/CLI request contracts.
- [x] Implement providers.py with API, Codex and Claude planners plus a clearly
  marked demo planner. Implement mcp_server.py using the shared service.
- [x] Exercise a real MCP stdio handshake and proposal/status tools locally.

## Task 4: web, CLI, demo and documentation
- [x] Test HTTP authentication, Origin checks, proposal/approval/status flow.
- [x] Implement web.py, cli.py and static UI. Add a stateful demo transport.
- [x] Run the entire unittest suite, actual CLI demo and browser desktop
  checks; document installation, model choice, MCP and demo recording.
- [x] Review implementation, repair security/correctness findings and rerun suite.

Mobile viewport/device verification remains pending.

Live Liara verification is outside delivery scope by the user's explicit decision.
Local execution and simulated cloud APIs are the acceptance environment;
no Liara credential is required. Live account compatibility is not claimed.
