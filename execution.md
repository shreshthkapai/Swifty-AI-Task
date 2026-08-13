# Northstar Webchat Implementation Plan

> Execute in order on `main`. Each phase is complete only after its tests and exit gate pass; do not build later phases around failing earlier work.

**Goal:** Complete the supplied Northstar Motors assignment as a polished AI dealership webchat whose deterministic harness is more reliable than a direct prompt-to-API call while remaining small, dealer-pluggable, and model/provider-agnostic.

**Architecture:** The harness receives a `DealerAdapter`, a `PlanningProvider`, and a `ConversationStore`. The model produces a bounded semantic plan only when deterministic routing cannot resolve the turn. Harness code validates commands, obtains dealership truth through the adapter, controls every mutation, reduces typed state, renders structured UI output, and atomically persists the turn.

**Initial stack:** Python 3.12, frozen dataclasses, `httpx`, FastAPI/Uvicorn at the HTTP boundary, SQLite via the standard library, one OpenAI planning-provider adapter, and the existing dependency-free JavaScript site.

```text
Northstar website -> Chat API -> dealership harness
                                  |-> PlanningProvider -> OpenAI first
                                  |-> ConversationStore -> SQLite first
                                  `-> DealerAdapter -> NorthstarAdapter -> supplied platform
```

Only the composition root selects those concrete implementations. A later dealer replaces `NorthstarAdapter`; a later model vendor replaces the planning provider. Neither change rewrites the harness.

## Source of Truth and Current Status

Read in this order:

1. `PRODUCT-BRIEF.md` - required customer experience and delivery constraints.
2. `docs/INTEGRATION-GUIDE.md`, `docs/BUSINESS-SEMANTICS.md`, and `docs/SEEDED-SCENARIOS.md` - supplied Northstar behaviour.
3. `dealership-platform/openapi.json` and executable platform tests - wire contract.
4. `docs/README.md` - authored integration story and detailed adapter specifications.

Already complete and retained:

- dealer-independent immutable domain models and all 24 `DealerAdapter` operations;
- strict `NorthstarAdapter`, authentication, error mapping, operation-specific retries, idempotency, reconciliation, and stable-record caching;
- isolated and live adapter tests covering all supplied operations and seeded failures;
- the unmodified supplied customer website and dealership platform.

Do not rewrite these layers unless a failing contract test proves a defect.

## Non-Negotiable Runtime Invariants

1. Dealer APIs are the only authority for inventory, prices, availability, slots, bookings, locations, hours, offers, and operation outcomes.
2. The model sees semantic dealership commands, never raw URLs, JSON payloads, credentials, arbitrary HTTP, or mutation execution tools.
3. A free-text turn uses at most one planning call in the baseline. Confirm/cancel/select/retry/show-more and obvious scope redirects use zero calls.
4. A semantic command is not one dealer call. Its handler may perform a bounded sequence of deterministic reads, such as availability plus matching slots, without returning to the model.
5. Planner output is untrusted data. Typed parsing, command limits, tool gating, business policy, required fields, and workflow transitions are enforced in code.
6. Writes follow `prepare -> summarize -> explicit confirmation -> refresh live truth -> execute`. The model cannot confirm or execute on the customer's behalf.
7. Each pending mutation owns one stable idempotency key. Retries reuse the exact request and key; edited requests create a new pending action and key.
8. The dealership platform remains final commit authority. Stale slots and changed vehicle/booking state produce deterministic recovery, not invented success.
9. Transcript, structured state, and pending actions are committed atomically. Duplicate client turn IDs cannot repeat a side effect.
10. Browser context is advisory. Stable IDs may seed context, but live business facts are re-read.
11. Logs contain operation metadata and normalized errors, not message text, credentials, names, email, phone, registration, or free-form notes.

## Deliberate Scope Limits

Build one dealership-chat runtime, one Northstar composition root, one custom website component, and one initial provider adapter. Do not add a pre-built chat product, user accounts, cross-device identity, long-term profiles, a vector database, retrieval infrastructure, WebSockets, background queues, multi-agent coordination, a generic plugin marketplace, arbitrary recursive tool loops, or an automatic second model call. Add an optional synthesis call only if measured eval evidence later shows a specific read-only comparison cannot be rendered well deterministically.

## Target Structure

```text
webchat/
  harness/
    contracts.py       # TurnRequest, TurnPlan, commands, response blocks
    state.py           # versioned ConversationState and reducers
    actions.py         # PendingAction lifecycle and idempotency identity
    scope.py           # conservative zero-call scope/action routing
    planning.py        # context compiler and dynamic semantic-tool gate
    policy.py          # executable authorization and business rails
    runtime.py         # one-turn orchestration and atomic outcome
    workflows/
      vehicles.py      # discovery, refinement, details, comparison
      sales.py         # enquiries, test drives, interest, callbacks, PX
      workshop.py      # booking, verification, amendment, cancellation
      dealerships.py   # locations, hours, messages, business notices
    render.py          # factual structured responses from typed results
  providers/
    base.py            # PlanningProvider protocol and safe usage metadata
    openai.py          # first provider-specific structured-plan adapter
  persistence/
    base.py            # ConversationStore protocol
    sqlite.py          # schema, migrations, optimistic atomic commits, expiry
  server/
    config.py          # validated environment configuration
    app.py             # HTTP API, cookie flow, CORS, lifecycle, health
  observability.py     # redacted structured event logging

dealership-website/src/features/webchat/
  api.js               # credentialed chat API client
  webchat.js           # panel lifecycle and interaction controller
  render.js            # safe declarative message/card/action rendering
  webchat.css           # responsive and accessible component styles

evals/
  corpus.json           # customer utterances, turns, and observable expectations
  run.py                # hermetic/live-provider lane runner

tests/conversations/
  test_questions.py     # black-box question/answer behaviour
  test_workflows.py     # multi-turn and consequential journeys
  test_adversarial.py   # ambiguity, recovery, scope, and trick questions
```

Tests mirror production packages under `tests/webchat/`; evaluator contract tests live in `tests/evals/`. Raw traces go under gitignored `artifacts/evals/`.

## Mandatory Phase Audit

After implementing a phase and before committing it:

1. Re-read that phase's file list, checklist, exit gate, and the global runtime invariants.
2. Review every added or changed line in production code, tests, fixtures, configuration, and documentation; map it to a requirement or remove it.
3. Trace each requirement to executable coverage and each test expectation back to a stated requirement. Check boundaries, failure paths, serialization, side effects, and out-of-scope additions explicitly.
4. Fix every mismatch test-first and repeat the changed-line audit for the fix.
5. Run the complete authored suite, the supplied platform suite, `git diff --check`, and a supplied-file/seed-data diff check from the final working tree.

A phase is not complete merely because its tests pass. It is complete only when this audit finds no unresolved mismatch and fresh verification satisfies its exit gate.
Keep audit notes, command logs, pass counts, traces, and intermediate evaluation results local. Do not add per-phase result diaries to tracked documentation or commits.

## Phase 0 - Protect the Verified Foundation

**Files:** existing `webchat/domain/`, `webchat/adapters/northstar/`, and their tests.

- [x] Run all current webchat and supplied platform tests before changing application code.
- [x] Verify the exact passing commands and test counts locally without committing a result diary.
- [x] Add no harness behavior to domain models or Northstar mapping/client modules.

**Exit gate:** all retained tests pass from a clean checkout and the working diff contains no supplied platform behavior or seed-data change.

## Phase 1 - Define Conversational Quality Before the Runtime

**Files:** create `webchat/harness/contracts.py`, `evals/corpus.json`, `evals/run.py`, `tests/webchat/harness/test_contracts.py`, `tests/evals/test_corpus.py`, `tests/evals/test_runner.py`, `tests/conversations/`, and reusable fakes under `tests/webchat/fakes/`.

- [x] Define the provider-neutral contract first: versioned scope values, a closed `HarnessCommand` union, `ResponseStrategy`, and `TurnPlan`. A plan contains at most two commands; it may contain multiple safe reads or one workflow preparation plus one safe read, but never multiple preparations or a mutation.
- [x] Define a versioned corpus of 60 black-box conversations: 12 vehicle discovery, 12 sales/test-drive, 16 workshop, 8 dealership/contact, 6 state/reference-resolution, and 6 scope/resilience scenarios. Corpus turns contain customer messages and UI actions—not pre-resolved intents.
- [x] Include all seeded cases: reserved `veh-007`, sold `veh-013`, price-on-request `veh-019`, stale slots, Bolton no-availability window, bank-holiday hours, bad workshop identity, cancelled booking, idempotent replay/conflict, and temporary API failure.
- [x] Include multi-turn refinement, page vehicle context, ordinal selection, topic switching, malformed contact data, double confirmation, repeated requests, interruption, and refresh restoration.
- [x] Encode these scope expectations:

| Input | Required route |
| --- | --- |
| `What size is the moon?` | deterministic dealership redirect; zero dealer/model calls |
| `Who won the World Cup?` | deterministic dealership redirect; zero dealer/model calls |
| `Is an SUV good for a family of five?` | allow useful dealership-adjacent advice |
| `How big is the moon, and will my telescope fit in this X3?` | retain the X3/space intent; do not answer trivia |

- [x] Make each scenario declare initial browser/state fixtures, the exact customer question(s), dealer fixtures, required/allowed commands and arguments, prohibited calls or mutations, expected state and side effects, maximum model calls, and observable answer assertions.
- [x] Test the actual returned answer for every turn. Prefer stable semantic assertions over exact prose: response strategy/block types, referenced entity IDs, required dealer facts, required clarification or next step, forbidden claims, notices/qualifications, and whether the answer directly addresses the question. Exact text is reserved for deterministic redirects and fixed safety/recovery copy.
- [x] Include straightforward questions, colloquial phrasing, typos, underspecified requests, contradictory constraints, pronouns and ordinals, corrections, compound questions, misleading premises, attempts to skip confirmation/verification, repeated messages, and unrelated or mixed-domain trick questions.
- [x] Encode complete natural-language exchanges such as search -> `the second one` -> `actually cheaper` -> test-drive slots -> details -> confirm, and workshop lookup -> failed identity -> verified lookup -> amend -> confirm -> cancel. The evaluator's only runtime seam is the public `ConversationDriver.execute_turn` boundary; Phase 7 wires the completed runtime to it without exposing workflow internals.
- [x] Define two parity lane configurations: `scripted` supplies valid predetermined `TurnPlan` values and `live-provider` supplies plans through `PlanningProvider`. The runtime, fake dealer, initial state, clock, corpus, and scoring inputs must otherwise be identical. Wire the lane interface to the completed runtime in Phase 7; existing live adapter tests remain the separate Northstar integration proof.
- [x] Capture the first divergence and classify failures as `MODEL_REASONING`, `PROVIDER_FAILURE`, `CONTEXT_MISSING`, `BAD_TOOL_SCHEMA`, `STATE_ERROR`, `POLICY_MISSING`, `ADAPTER_ERROR`, `AMBIGUITY`, `ANSWER_QUALITY`, or `RECOVERY_ERROR`.

**Exit gate:** corpus schema tests pass, all 60 customer-facing scenarios load deterministically, malformed expectations fail loudly, and the runner can score a no-op chatbot as failed rather than accepting missing answers.

## Phase 2 - Structured Conversation State and SQLite

**Files:** create `harness/state.py`, `harness/actions.py`, `persistence/base.py`, `persistence/sqlite.py`, and focused tests.

- [ ] Give `ConversationState` and declarative `StructuredMessage` payloads independent schema versions. Model immutable state sections for customer details, page/entity context, search preferences, workflow domain/stage/fields, pending action, verification grants, and presentation groups.
- [ ] Retain the last five presentation groups with stable entity IDs, display order, snapshot timestamp, and provenance so `the second one` resolves without transcript reconstruction. Mark snapshots non-authoritative.
- [ ] Model `PendingAction` separately from dealership requests with action ID/type, serialized typed request, state, stable idempotency key, creation/expiry, attempt count, and last normalized failure.
- [ ] Give workshop verification grants an explicit booking ID and short expiry (default 15 minutes); never infer authorization from cookie possession or old transcript.
- [ ] Define `ConversationStore.load_or_create`, `load`, `commit(expected_revision, messages, state)`, `delete`, and `purge_expired`. `commit` atomically writes versioned structured messages and state using optimistic revision checks.
- [ ] Implement SQLite WAL mode, migrations, foreign keys, unique `(conversation_id, client_turn_id)`, and configurable seven-day inactivity retention. Store only an opaque random conversation ID in the browser cookie.
- [ ] Test restart restoration, revision conflict, duplicate-turn deduplication, atomic rollback, pending-action survival, expired grants, schema rejection/migration, and retention cleanup.

**Exit gate:** identical inputs and explicit `now` values serialize identically; refresh/restart restore state without replaying transcript; duplicate turns cannot duplicate messages or execution records.

## Phase 3 - Planner and Provider Boundary

**Files:** consume `harness/contracts.py`; create `harness/scope.py`, `harness/planning.py`, `providers/base.py`, `providers/openai.py`, and tests.

- [ ] Define `PlanningProvider.plan(request: PlanningRequest) -> PlanningResult`. No provider response IDs, SDK objects, or hidden provider conversation state may enter harness state.
- [ ] Strictly parse provider output into the existing `TurnPlan`, including scope, commands, response strategy, clarification fields, and optional bounded adjacent advice; reject unknown fields, invalid combinations, and every mutation command.
- [ ] Expose semantic commands covering the product brief: vehicle search/details/availability/offers; test-drive slots and preparation; enquiry, interest, callback and part-exchange preparation; services/workshop locations/slots/lookup and booking/amend/cancel preparation; dealership/location/hours/message/business information.
- [ ] Implement a conservative deterministic gate only for explicit UI actions, pending-action control, clearly unrelated single-purpose trivia, and exact retry/pagination events. Ambiguous, adjacent, and mixed requests reach the planner.
- [ ] Compile canonical context from current input, explicit server time, current workflow, selected entities, relevant preferences, bounded presentation groups/history, page observation, and normalized prior failures. Historical PII is represented as known/missing unless a command needs server-side merging; current input may contain newly supplied PII.
- [ ] Add hard priority tiers and token/character budgets. Emit canonical diagnostics containing compiler-policy version, selected/omitted sections, size, ordering, tool-gate policy version, and stable machine-readable inclusion/exclusion reasons.
- [ ] Dynamically expose only relevant semantic commands. Tool visibility is not execution authorization. During a pending action retain safe cross-domain entry and explicit action controls, but remove competing preparation commands.
- [ ] Implement the first OpenAI adapter with server-side `store: false`, strict structured output parsing, timeout handling, usage/latency capture, configurable model/base URL/key, and no reliance on previous model reasoning. Use a fake provider for all normal tests.

**Exit gate:** malformed or over-broad plans fail closed; deterministic routes make zero provider calls; identical planning inputs compile byte-identically; replacing the fake/OpenAI provider requires no harness change.

## Phase 4 - Deterministic Workflows, Policies, and Rendering

**Files:** create workflow modules, `harness/policy.py`, `harness/runtime.py`, `harness/render.py`, and mirrored tests.

- [ ] Implement vehicle discovery, refinement, pagination, selection, details, deterministic comparison tables, availability, and offer flows. Preserve null price as `price on request`; never derive or invent a figure.
- [ ] Implement sales enquiry, test-drive, reserved-vehicle interest, callback, and part-exchange flows with typed field collection and business qualifications.
- [ ] Implement service discovery, workshop booking, verified lookup, amendment, and cancellation. A successful lookup creates only a short-lived conversation-scoped grant for that booking ID.
- [ ] Implement dealership discovery, department-specific and holiday opening hours, messages, callbacks, contact details, and finance/privacy/valuation notices from `BusinessInformation`.
- [ ] Resolve explicit actions deterministically: select entity/slot, show more, confirm, cancel, retry, start over, and switch workflow. Starting a different workflow supersedes an old pending action deterministically.
- [ ] Enforce required details, valid transitions, capability/tool authorization, verification grants, confirmation state, idempotency identity, vehicle eligibility, slot freshness, and booking status in handlers before adapter calls.
- [ ] Encode recovery explicitly: a reserved vehicle offers interest/enquiry instead of test drive; a sold vehicle allows enquiry only; an interest request stops if the vehicle is no longer reserved; a lost slot refreshes choices; a cancelled workshop booking cannot be amended.
- [ ] Immediately before a confirmed mutation, re-read relevant live vehicle/slot/booking truth where possible. Treat the adapter response as final; map `DealerErrorKind` to specific recovery without matching text.
- [ ] Render versioned declarative blocks (`text`, `vehicle_cards`, `comparison`, `slot_choices`, `confirmation`, `notice`, `actions`, `link`) with stable entity/action references. Model-authored adjacent advice cannot interpolate dealer facts.
- [ ] Keep orchestration bounded: deterministic gate -> optional one planning call -> validated command batch -> adapter calls -> reducer -> renderer -> atomic commit. There is no recursive agent loop.

**Exit gate:** focused workflow tests cover every product capability and normalized failure; black-box conversation tests prove questions produce correct structured answers; no invalid mutation reaches a fake dealer; retry and double-confirm tests produce one business record.

## Phase 5 - Chat HTTP Application and Operations

**Files:** create `server/config.py`, `server/app.py`, `observability.py`, server tests, `Dockerfile.webchat`, `.env.example`; update `pyproject.toml`, `compose.yaml`, and `.gitignore`.

- [ ] Compose `NorthstarAdapter`, `SQLiteConversationStore`, configured `PlanningProvider`, and `HarnessRuntime` only at startup. Core packages do not read environment variables.
- [ ] Provide `GET /health`, `GET /api/chat/session`, `POST /api/chat/turns`, and `DELETE /api/chat/session`. A turn accepts a client-generated ID, either text or a declarative action reference, and sanitized page observation.
- [ ] On first session access, generate the conversation ID with `secrets.token_urlsafe(32)` and set `northstar_chat=<opaque token>` with `HttpOnly`, `SameSite=Lax`, scoped path, retention-aligned `Max-Age`, and `Secure` outside local development. Return transcript/UI blocks, not internal state.
- [ ] Allow credentials only from the configured website origin (`http://localhost:4173` locally), cap message/body sizes, reject unknown fields/actions, and return stable safe error envelopes.
- [ ] Add per-conversation serialization or optimistic-conflict retry so concurrent turns cannot reorder state. Return an in-flight conflict without repeating provider/dealer work when safety is uncertain.
- [ ] Emit one redacted JSON event per turn and external call with request/turn IDs, hashed conversation ID, route, operation, duration, retries, model calls/tokens, outcome, and normalized error kind.
- [ ] Add a `webchat-api` Compose service on port `4020`, persistent SQLite volume, health check, and explicit environment names: `CHAT_PROVIDER`, `CHAT_MODEL`, `OPENAI_API_KEY`, `NORTHSTAR_BASE_URL`, `NORTHSTAR_API_KEY`, `CHAT_DATABASE_PATH`, `CHAT_ALLOWED_ORIGIN`, and retention/timeout settings.

**Exit gate:** API contract tests prove cookie restoration, CORS, validation, duplicate-turn handling, redaction, provider/dealer outage responses, and backend restart persistence; secrets never appear in responses or logs.

## Phase 6 - Website Chat Experience

**Files:** create the webchat feature files; modify `dealership-website/index.html`, `src/app.js`, and only necessary shared styles/config.

- [ ] Add a branded launcher and panel that fits the existing Northstar design: compact desktop panel and near-full-screen mobile sheet without obscuring primary navigation or vehicle content.
- [ ] Implement closed/open, loading, unread, unavailable, recoverable error, empty, and restored states. Disable duplicate sends while a turn is in flight but preserve safe retry.
- [ ] Render transcript and declarative blocks with DOM APIs/text content; never inject model HTML. Vehicle cards link to `/?vehicle=<id>` and action buttons send only server-issued action references.
- [ ] Send refreshable page context on every turn: current URL, current vehicle ID, and relevant visible search filters. The server verifies IDs and treats filters as hints.
- [ ] Restore the anonymous session with `credentials: "include"`; provide an explicit `Start new conversation` control that clears server state and cookie after confirmation.
- [ ] Support Enter-to-send/Shift+Enter newline, launcher and panel focus management, Escape/close, visible focus, labeled controls, `aria-live` status, keyboard scrolling, adequate contrast/touch targets, and reduced motion.
- [ ] Add automated DOM/component tests for transcript rendering, action-reference dispatch, page-context capture, restored/loading/error states, keyboard semantics, and safe text rendering. Keep only visual layout inspection as a small manual check; conversational correctness must not depend on clicking through the UI.
- [ ] Test at desktop and mobile widths against inventory, vehicle dialog, locations, and service sections. Do not alter existing vehicle/site behavior.

**Exit gate:** automated API/conversation tests complete vehicle search -> selection -> confirmed test drive and workshop lookup -> amendment/cancellation, including refresh restoration. UI automation proves browser wiring; manual review is limited to responsive appearance and interaction feel.

## Phase 7 - Full Evaluation and Correct-Layer Hardening

**Files:** finish `evals/run.py`, add missing regression tests, and keep raw results under gitignored `artifacts/evals/`.

- [ ] Run all 60 question-and-answer conversations through the scripted lane and assert every returned answer, tool call, state transition, and side effect. Fix the first divergence at state, context, schema, policy, adapter, recovery, or renderer rather than masking it in prompts.
- [ ] Run all 60 customer questions once through the live-provider lane, then repeat the critical ambiguity, safety, confirmation, verification, stale-state, and trick-question subset three times. Record evaluated commit, date, provider/model configuration (no key), compiler/tool-gate policy versions, scenario version, answer-quality failures, latency, token/model-call totals, category counts, and per-domain success.
- [ ] Run a small full-stack smoke set against the real local Northstar platform for available/reserved/sold vehicles, a stale slot, workshop verification, booking amendment/cancellation, bank-holiday hours, and API outage.
- [ ] Keep run summaries, prompts, model responses, transcripts, PII, traces, and other evaluation outputs under gitignored `artifacts/evals/`; commit only reusable evaluator code, fixtures, and regression tests.

Required release thresholds:

- scripted lane: 100% task success, 0 hallucinated dealership facts, 0 invalid mutations, and 100% expected zero-model routes;
- live-provider lane: at least 90% overall task success and 100% critical safety/policy compliance across the recorded runs;
- every deterministic action: 0 model calls; every planned baseline turn: at most 1 model call;
- all domain, adapter, harness, persistence, server, evaluator, and supplied platform tests pass;
- no duplicate business record in retry, refresh, concurrent-send, or double-confirm tests.

The routine, key-free command `python -m unittest discover -s tests -t . -p "test_*.py" -v` must run unit, integration, persistence, API, and all scripted conversational Q&A tests in one invocation. Live-provider conversational tests use a separate explicit command and are never silently skipped as part of a reported live baseline. Neither suite claims to decide the reviewer's assessment; it produces reproducible evidence of what questions the chatbot answered and where it failed.

**Exit gate:** every failure is categorized with first-divergence evidence, thresholds pass, and the evaluation is reproducible locally from documented commands without committing its outputs.

## Phase 8 - Reviewer Delivery

**Files:** update `README.md`, `docs/README.md`, `.env.example`, and add a short architecture/limitations section without duplicating the detailed adapter docs.

- [ ] Make `docker compose up --build` start platform, website, and webchat concurrently; document where a provider key/model is required and how the unavailable state behaves without it.
- [ ] Document setup, ports, environment names, reset/migration behavior, one key-free full test command, one explicit live-model Q&A command, architecture boundaries, privacy/retention, operational logs, and known limitations.
- [ ] Verify a clean checkout with a fresh SQLite volume, then run every documented command exactly as written.
- [ ] Review the final diff for accidental platform/seed changes, secrets, generated data, raw artifacts, stale instructions, dead code, and abstractions without a current consumer.

**Final definition of done:** every capability in `PRODUCT-BRIEF.md` is reachable through the webchat; refresh persistence, page context, accessibility, deterministic action safety, Northstar edge cases, provider configuration, and graceful failure are demonstrated; the harness imports neither Northstar nor OpenAI implementation details and can be composed with another conforming dealer adapter or planning provider.

## Commit Sequence

Use one reviewable commit per completed gate:

1. `Add executable conversation eval corpus`
2. `Add persistent structured conversation state`
3. `Add bounded planning provider boundary`
4. `Add deterministic dealership workflows`
5. `Add chat API and operational logging`
6. `Add Northstar website chat experience`
7. `Harden harness with full evaluation suite`
8. `Document complete webchat delivery`

Never commit a phase while its gate is failing. Do not force-push or rewrite this clean foundation history unless the user explicitly requests it.
