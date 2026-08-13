# Northstar Adapter Implementation

> **Solution design 6 of 6 — Verified integration.** This document defines the implementation and proof boundary between the dealer-independent port and Northstar REST API.
>
> Previous: [Dealership Domain Contracts](./DEALERSHIP-DOMAIN-CONTRACTS.md)

## Goal and Boundary

`NorthstarAdapter` implements all 24 operations in `DealerAdapter`. Harness code supplies and receives only immutable dealership-domain values; it never handles Northstar paths, camelCase JSON, credentials, vendor errors, or `httpx` types. No LLM is involved in this layer or its tests.

```text
domain request → NorthstarAdapter → NorthstarClient → Northstar REST
domain record  ← strict mapper     ← validated JSON   ← response
```

The implementation uses `httpx==0.28.1`. `NorthstarClient` owns an injected `httpx.AsyncClient` and its lifecycle. `NorthstarAdapter` receives a `NorthstarClient`, keeping transport details replaceable and tests deterministic.

## Package Structure

```text
webchat/adapters/northstar/
├── __init__.py          # supported public construction surface
├── adapter.py         # DealerAdapter implementation and operation policy
├── client.py          # HTTP, credentials, JSON, timeout and lifecycle
├── config.py          # URL, key, timeout, locale and cache settings
├── errors.py          # platform-code to DealerError translation
└── mapping/
    ├── common.py      # strict primitives, dates, money and identity
    ├── vehicles.py
    ├── sales.py
    ├── workshop.py
    └── dealerships.py
```

Mapping modules are pure. Request functions produce exact Northstar query/body values; response functions reject missing fields, wrong types, invalid enums, naive timestamps, and structurally inconsistent records. Harmless documented additions such as `idempotentReplay` may be ignored. Relative asset paths are resolved only against the configured platform URL.

Northstar-specific translations include GBP minor units, `part_exchange` to `part-exchange`, UTC timestamps, configured `Europe/London` opening-hours timezone, configured United Kingdom address country, and composed slot labels. Workshop lookup performs identity verification first, then obtains the full dealership record needed by `WorkshopBookingDetails`.

## Client and Configuration

Configuration contains the platform base URL, server-side API key, explicit connect/read/write/pool timeouts, `GBP`, `Europe/London`, address country, retry delays, and location-cache TTL. Secrets are never represented in domain values or error context. The production constructor reads environment configuration at the server boundary; tests construct configuration directly.

`NorthstarClient` attaches `X-API-Key` only to protected operations and `Idempotency-Key` only when supplied by a create action. It accepts only relative API paths, enforces JSON-object responses, and converts transport failures into safe structured failures. It does not select a generic retry policy: each adapter operation supplies its allowed execution policy.

## Operation-Specific Recovery

| Operation class | Transport/5xx behaviour |
| --- | --- |
| Catalogue and protected reads | Bounded retry with short backoff |
| Workshop lookup | Retry as a logical read |
| Creates | Retry only with the identical body and idempotency key |
| Workshop cancellation | Retry because cancellation is repeat-safe |
| Workshop amendment | Never retry blindly; reconcile once with protected read |
| Business 4xx/409 | Do not retry |

After an ambiguous amendment, the adapter reads the current booking. It returns success only when every requested change is visible; otherwise it raises `TEMPORARY_FAILURE` for harness-controlled recovery. Injected sleep and monotonic-clock functions make retry and cache tests instant and deterministic.

A five-minute default TTL caches only stable dealership/location records by ID. Dealership and workshop-location lists populate it, direct dealership reads consult it, and lookup enrichment reuses it. Vehicle truth, availability, slots, bookings, opening hours, and business operations are never cached.

## Failure Contract

Northstar errors are translated by code, never message text. Field names are converted from Northstar payload paths to domain collection paths before becoming `FieldViolation` values.

- `INVALID_REQUEST` means the adapter locally rejected a structurally valid but Northstar-incompatible domain input, such as non-GBP money or a `general` callback department. No request is sent.
- `VALIDATION` means Northstar rejected customer-correctable input; valid conversation state can be retained.
- `INVALID_RESPONSE` means a successful or error response violated Northstar's documented contract.
- Recovery-specific codes retain their existing stable kinds: verification failure, lost slot, vehicle state changes, cancelled booking, idempotency conflict, authentication failure, and temporary failure.

Unknown platform error codes and malformed error envelopes fail closed as `INVALID_RESPONSE`. Credentials, raw bodies, customer identity, free text, and vendor messages are not copied into exceptions.

## Verification Strategy

Isolated `unittest.IsolatedAsyncioTestCase` tests use `httpx.MockTransport` to verify exact methods, paths, queries, bodies, headers, typed mappings, local rejection, all documented error codes, retry counts, amendment reconciliation, cache expiry, malformed JSON, missing fields, wrong types, and unknown enum values. Every adapter operation has direct request-and-response coverage.

Live contract tests start the supplied platform on an isolated port with a temporary SQLite database. They exercise all 24 methods through real HTTP and cover the seeded reserved/sold vehicles, stale slots, incorrect workshop identity, cancelled amendments, replayed creates, and idempotency conflicts. Test reset uses only the temporary instance; the Docker volume and developer data are untouched.

Step 5 is complete only when isolated adapter tests, live adapter contract tests, existing domain/evaluation tests, and the supplied platform tests all pass. This creates a deterministic, measurable integration boundary before model orchestration begins.

Run the implementation proof from the repository root:

```bash
python -m unittest discover -s tests/webchat -p "test_*.py" -v
python -m unittest discover -s tests/evals -p "test_*.py" -v
cd dealership-platform
python -m unittest discover -s tests -p "test_*.py" -v
```

## Non-goals

This step does not add prompts, tools, conversation state, confirmation UI, persistence, model-provider code, general caching, or a generated OpenAPI client. It does not modify Northstar behaviour or seed data.
