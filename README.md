# Northstar Motors AI Webchat

Northstar Motors is the first integration of a reusable car-dealership chat harness. The supplied
website and dealership platform remain intact; this repository adds the conversational runtime,
Northstar integration, persistent anonymous sessions, and reviewer-facing evaluation suite.

## Architecture at a glance

```text
Browser -> Chat API -> dealer-independent harness -> DealerAdapter -> Northstar REST API
                            |                           |
                            +-> provider protocols      +-> Northstar mapping/auth/errors
                                  -> OpenAI adapter
```

The harness owns conversation state, deterministic fast paths, semantic planning, policy checks,
confirmation, evidence-grounded answers, and rendering. It depends on typed provider and dealer
protocols—not OpenAI SDK objects or Northstar JSON. See [Solution Architecture](./docs/ARCHITECTURE.md)
for the runtime decisions, extension boundaries, and test strategy.

## Run locally

Requirements: Docker with Compose and ports `4010`, `4020`, and `4173` available.

1. Copy `.env.example` to `.env` and set `OPENAI_API_KEY`. `CHAT_MODEL` selects the grounded-answer
   model; optional `CHAT_PLANNER_MODEL` selects a faster planning model. Keys remain server-side.
2. Start the complete product:

   ```bash
   docker compose up --build -d
   ```

3. Open the dealership website at http://localhost:4173.

Supporting services:

- chat health: http://localhost:4020/health
- supplied API documentation: http://localhost:4010/docs
- supplied dealership console: http://localhost:4010/admin

Reset deterministic dealership data with `./reset.sh` or `.\reset.ps1`. Stop services with
`docker compose down`. Anonymous chat and dealership data remain in Docker volumes until reset or
volume removal.

## Verify

Python 3.12+ and Node.js are required when running tests outside Docker.

```bash
python -m pip install .
python -m evals.verify
```

The single verification command runs authored Python tests, supplied platform tests, browser UI
tests, and the 60-scenario scripted conversation corpus. It exits non-zero on failure and writes a
detailed, gitignored report to `artifacts/evals/reviewer-report.json`.

Useful focused commands:

```bash
python -m evals.verify --only authored
python -m evals.verify --only platform
python -m evals.verify --only ui
python -m evals.verify --only corpus --scenario workshop-16
python -m evals.verify --example stale-test-drive-slot
```

The deterministic corpus is reproducible and does not call a model API. The billable provider lane
uses the same runtime, fixtures, clock, scenarios, and scorer while replacing only the scripted
planner/response providers:

```bash
python -m evals.verify --only corpus --lane live-provider
```

## Documentation

Read in this order:

1. [Product Brief](./PRODUCT-BRIEF.md)—supplied requirements and assessment scope.
2. [Solution Architecture](./docs/ARCHITECTURE.md)—implemented boundaries, runtime, safety, and tests.
3. [Supplied Platform References](./docs/README.md)—API usage, business semantics, seeded edge cases,
   and OpenAPI contract.
