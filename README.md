# Northstar Motors AI Webchat

This repository contains the Northstar Motors website, local dealership services, and the
requirements in [PRODUCT-BRIEF.md](./PRODUCT-BRIEF.md).

## Start

Requirements:

- Docker with Docker Compose
- Ports `4010`, `4020`, and `4173` available
- An OpenAI API key in `.env` for the server-side chat planner

Create `.env` from `.env.example`, set `OPENAI_API_KEY`, and optionally change
`CHAT_MODEL`. Never place provider or dealership keys in browser code.

```bash
docker compose up --build -d
```

Then open:

- Dealership website: http://localhost:4173
- Webchat API health: http://localhost:4020/health
- API documentation: http://localhost:4010/docs
- Dealership Systems Console: http://localhost:4010/admin

Public catalogue and reference reads do not require authentication. Saved customer-record reads
and write operations require this local API key:

```text
northstar-local-development
```

Send it using the `X-API-Key` header. Keep the key in server-side code rather than
browser-delivered code.

Start with:

- [PRODUCT-BRIEF.md](./PRODUCT-BRIEF.md) for the product requirements;
- [docs/README.md](./docs/README.md) for the adapter documentation;
- [docs/INTEGRATION-GUIDE.md](./docs/INTEGRATION-GUIDE.md) for API usage;
- [docs/BUSINESS-SEMANTICS.md](./docs/BUSINESS-SEMANTICS.md) for operation outcomes;
- [docs/SEEDED-SCENARIOS.md](./docs/SEEDED-SCENARIOS.md) for the seed data catalogue.

## Reset

```bash
./reset.sh
```

Resetting clears dealership-platform records and restores the original seed data.

## Stop

```bash
docker compose down
```

Dealership and anonymous chat state remain in their named Docker volumes until reset or removal.

## Webchat development

The dealer-independent contracts and Northstar adapter require Python 3.12 or newer:

```bash
python -m pip install .
python -m unittest discover -s tests -t . -p "test_*.py" -v
```

The live contract tests start an isolated dealership-platform process and temporary SQLite
database. Runtime configuration uses `NORTHSTAR_BASE_URL` and the server-side
`NORTHSTAR_API_KEY`; never expose the API key to browser code.

## Reviewer verification

Run the authored unit, integration, API, adapter, and conversational tests together with the
supplied dealership-platform tests:

```bash
python -m evals.verify
```

The command prints dense suite statistics and writes a sanitized machine-readable report to
`artifacts/evals/reviewer-report.json`. Generated reports are gitignored. Selected assignment
edge cases and their actual deterministic harness outputs over hermetic dealer fixtures are
committed in [`evals/examples.json`](./evals/examples.json); inspect one without running the
suites using:

```bash
python -m evals.verify --example stale-test-drive-slot
```

The examples cover scope routing, dealership-adjacent advice, mixed requests, reserved/sold
vehicles, price-on-request, stale slots, workshop verification, and holiday hours. They contain
no credentials, customer PII, hidden reasoning, or provider envelopes. The live adapter and
supplied-platform suites independently verify Northstar's real HTTP contract and seeded records.

## Dealership services

The dealership platform provides:

- vehicle inventory and offers;
- dealership locations and opening hours;
- sales enquiries, callbacks, and test drives;
- workshop availability and bookings;
- dealership messages and part-exchange valuations.

The customer website is editable and uses the same vehicle inventory API.
