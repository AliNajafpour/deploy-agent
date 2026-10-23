# Deployment repair fixtures

These three additional fixtures cover an incorrect Node start command, a missing
Python runtime package and a wrong backend PORT binding. They do not change application features.

`python -m tests.repair_agent --provider codex` proposes actual model repairs,
checks their precise scope as a test operator, approves, then runs the frozen
uploaded candidate through a local test builder. The Python builder installs
colorama 0.4.6 from a local wheelhouse or the existing uv cache into an
isolated disposable venv. Set LYARA_WHEELHOUSE for a custom local wheel directory.
Both paths are offline; no network package download or host installation.
Cloud HTTP endpoints are simulated; build, model, child processes and HTTP are real.

`--provider scripted` runs the same execution checks without model inference.
