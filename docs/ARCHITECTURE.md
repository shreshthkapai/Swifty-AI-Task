# Solution Architecture

## Goal and boundaries

This is a reusable car-dealership conversation harness with Northstar as its first integration. It
is deliberately not a general agent framework.

| Layer | Owns |
| --- | --- |
| `webchat/domain/` | Dealer-independent models, stable failures, `DealerAdapter` |
| `webchat/harness/` | Conversation state, semantic tools, bounded orchestration, policy, execution, UI blocks |
| `webchat/providers/` | `ConversationProvider` implementations; currently OpenAI Responses API |
| `webchat/adapters/northstar/` | Northstar transport, auth, JSON mapping, retries, caching, error normalization |
| `webchat/persistence/` | Atomic transcript/state store; currently SQLite |
| `server/` | HTTP, cookies, composition, concurrency, streaming, redacted telemetry |

Generic harness code imports neither Northstar nor a model SDK. Another dealer implements
`DealerAdapter`; another model company implements `ConversationProvider`. Startup composition selects
the concrete implementations.

## Turn lifecycle

```text
text + recent messages + structured state + live page context
                              |
                    one model conversation call
                       /                    \
                 final text          semantic tool batch
                                             |
                                  schema + policy validation
                                             |
                                      DealerAdapter calls
                                  (safe reads can run together)
                                             |
                                  one model continuation
                                             |
                              direct answer + supporting UI blocks
                                             |
                              atomic messages + state commit
```

A turn uses one model call when no fresh dealer data is needed and two when the answer must see tool
results. There is one tool round and no recursive loop. The same model interprets the request and
owns final wording, avoiding competing canned, planning, and synthesis responses.

All natural-language messages go to the model. Only trusted, typed UI actions such as a clicked
confirmation, cancellation, result, or slot can use a zero-call path. This keeps conversation
expressive without moving business authority into the model.

## Safety and factual authority

Tools describe business operations (`search_vehicles`, `find_test_drive_slots`,
`prepare_workshop_booking`), never raw HTTP endpoints. Tool arguments are schema-validated before
execution. Business facts come from `DealerAdapter` results; tool output supplies those facts to the
continuation and cards are rendered from the same typed results.

Consequential work follows:

```text
prepare -> validate -> show confirmation -> customer confirms
        -> revalidate live dealer state -> idempotent mutation
```

Code enforces required fields, vehicle status, slot validity, workshop verification, pending-action
expiry, legal transitions, retryability, and idempotency. The model cannot directly execute a write.
Northstar failures map to stable domain failures, so recovery never parses error prose.

## State, persistence, and streaming

`ConversationState` records customer-supplied workflow fields, preferences, selected entities,
recently presented groups, pending actions, and expiring workshop verification grants. Live page
context refreshes the current observation; persisted state is not reconstructed from transcript.

An opaque `HttpOnly`, `SameSite=Lax` cookie identifies an anonymous conversation. Transcript and
state remain server-side in SQLite. Optimistic revisions plus an atomic message/state commit protect
concurrent or duplicate turns. PII is not stored in the cookie or written to telemetry.

`POST /api/chat/turns/stream` emits NDJSON text deltas followed by one complete payload. Partial text
is never committed. If the browser disconnects or the provider fails, only a completed validated
turn can alter persisted state. The UI follows streaming text only while the reader is already at
the bottom.

## Verification

`python -m evals.verify` runs authored tests, unchanged supplied-platform tests, browser UI tests,
and a 60-scenario scripted corpus. The scripted lane uses model-shaped semantic decisions but real
harness execution and dealer fixtures. The billable `live-provider` lane changes only the
conversation provider. Reports include tool calls, dealer calls, state changes, writes, call count,
tokens, latency, answer assertions, and first divergence.
