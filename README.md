# Northstar Motors AI Webchat

Northstar Motors is the first integration of a reusable car-dealership chat harness. The supplied
website and dealership platform remain intact; this repository adds natural conversation,
dealer-backed tools, safe bookings, anonymous persistent sessions, streaming UI, and a reproducible
evaluation suite.

## Architecture

```text
Browser -> Chat API -> conversational harness -> DealerAdapter -> Northstar REST API
                            |
                            +-> ConversationProvider -> OpenAI Responses API
```

The model owns language and natural-language interpretation. The harness exposes semantic dealer
tools, executes at most one tool batch, and allows one continuation so the model can answer from
fresh results. Deterministic code is limited to trusted UI actions and business safety: validation,
confirmation, authorization, live revalidation, idempotency, and error recovery. Northstar URLs,
JSON, authentication, retries, and quirks stay in `webchat/adapters/northstar/`.

See [Solution Architecture](./docs/ARCHITECTURE.md) for boundaries and design decisions, and
[Implementation Handoff](./docs/HANDOFF.md) for a code-level tour.

## Run locally

Requirements: Docker Desktop with Compose running; ports `4010`, `4020`, and `4173` available.

### Windows (PowerShell)

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up --build -d
Start-Process http://localhost:4173
```

### macOS/Linux

```bash
cp .env.example .env
nano .env
docker compose up --build -d
open http://localhost:4173  # macOS; otherwise browse to this URL
```

Before starting, set `OPENAI_API_KEY` in `.env`. `CHAT_MODEL` selects the single conversation
model. Secrets remain server-side and `.env` is ignored by Git.

Useful endpoints:

- product: http://localhost:4173
- chat health: http://localhost:4020/health
- supplied API docs: http://localhost:4010/docs
- supplied dealership console: http://localhost:4010/admin

Reset seeded dealership data with `.\reset.ps1` on Windows or `./reset.sh` on macOS/Linux. Stop
services with `docker compose down`.

## Verify

Python 3.12+ and Node.js are required outside Docker.

```bash
python -m pip install .
python -m evals.verify
```

This runs authored Python tests, unchanged supplied-platform tests, browser UI tests, and the
60-scenario scripted corpus. The detailed report is written to the gitignored
`artifacts/evals/reviewer-report.json`.

```bash
python -m evals.verify --only authored
python -m evals.verify --only platform
python -m evals.verify --only ui
python -m evals.verify --only corpus --scenario workshop-16
python -m evals.verify --example stale-test-drive-slot
python -m evals.verify --only corpus --lane live-provider  # billable
```

The scripted and live lanes use the same runtime, dealer fixtures, clock, corpus, and scorer; only
the conversation provider changes.

## Documentation

Read [Product Brief](./PRODUCT-BRIEF.md), [Solution Architecture](./docs/ARCHITECTURE.md), then the
[supplied platform references](./docs/README.md).
