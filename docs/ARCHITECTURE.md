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
| `dealership-website/` | Supplied customer site plus the streaming chat UI |

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
                                  model continuation (may start another tool round)
                                             |
                              direct answer + supporting UI blocks
                                             |
                              atomic messages + state commit
```

A turn uses one model call when no fresh dealer data is needed, two when the answer must see tool
results, and up to three when a follow-up tool round is needed (e.g. search then detail lookup).
There are at most two tool rounds and no recursive loop. The same model interprets the request and
owns final wording, avoiding competing canned, planning, and synthesis responses.

All natural-language messages go to the model. Only trusted, typed UI actions such as a clicked
confirmation, cancellation, result, or slot can use a zero-call path. This keeps conversation
expressive without moving business authority into the model.

### Exact call paths

Ordinary advice or a clarification:

```text
user text -> model -> streamed direct answer                       (1 model call)
```

Dealer-backed question:

```text
user text -> model tool call -> validated dealer read
          -> model sees returned facts -> answer + cards           (2 model calls)
```

Multi-step lookup (e.g. search then detail):

```text
user text -> model tool call -> dealer read -> model sees results
          -> model follow-up tool call -> dealer read
          -> model sees both results -> answer + cards             (3 model calls)
```

Consequential request:

```text
user text -> model prepare tool -> deterministic validation
          -> model continuation -> natural language + confirmation UI   (2 model calls)
clicked confirm -> live revalidation -> idempotent dealer write        (0 model calls)
```

Multiple reads within a round run concurrently. Preparations can combine with reads in the same
batch but not with other preparations or control tools. Provider errors receive one concise recovery
response and no hidden retry-model call.

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

### Safety invariants

- No free-text intent routing in production runtime code.
- No raw dealer endpoints as model tools.
- Preparations never execute mutations.
- Mutations require a pending action and explicit confirmation.
- Always revalidate live state immediately before a write.
- Stable idempotency keys across safe retries.
- Verification grants scoped to a booking and explicitly expiring.
- Messages and state committed together, only after a completed turn.
- Never log prompt text, model output, customer PII, API keys, or raw dealer payloads.

## Data ownership

The model receives a canonical JSON snapshot containing the current message, server time, selected
entities, preferences, workflow, pending-action metadata, active verification grants, up to three
recent presentation groups, up to eight recent messages, current page observation, and normalized
prior failures. Historical known email, phone, and registration values are redacted from repeated
message text; structured state remains authoritative.

Provider continuation data is opaque and exists only between calls within one turn. It is not
conversation memory and is not persisted. The OpenAI adapter uses `store: false`; subsequent turns
are reconstructed from application-owned state and messages.

Dealer truth travels in the opposite direction: Northstar JSON is validated and mapped to immutable
domain objects, workflows create evidence and declarative cards, and the continuation receives a
compact structured tool result. Neither raw Northstar JSON nor HTTP errors enter the model-facing
harness contract.

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

## Frontend

The chat widget is embedded in the supplied dealership website as a floating overlay:

- Streaming responses with progressive markdown rendering (bold, italic, lists, links, code)
- Typing indicator while waiting for a response
- Message timestamps
- Contextual welcome prompts that adapt to the current page section
- Follow-up suggestion chips based on response type
- Vehicle cards, confirmation cards, and action buttons rendered from structured blocks
- 15-second fetch timeouts with graceful error recovery
- Keyboard accessible and responsive across desktop and mobile

## Code map

### Backend

| File | Responsibility |
| --- | --- |
| `webchat/harness/turn.py` | Input/output for one turn and live-page state refresh |
| `webchat/harness/conversation.py` | Provider-neutral conversation, tool-call, continuation, usage contracts |
| `webchat/harness/tools.py` | Static dealer-independent semantic command catalogue |
| `webchat/harness/conversation_tools.py` | Converts validated model calls into commands or controls |
| `webchat/harness/conversation_runtime.py` | Initial call, up to two tool rounds, bounded continuations |
| `webchat/harness/execution.py` | Executes interpreted commands and trusted UI actions |
| `webchat/harness/policy.py` | Deterministic business validation |
| `webchat/harness/workflows/` | Maps semantic commands to `DealerAdapter` operations, state, evidence, UI blocks |
| `webchat/providers/base.py` | `ConversationProvider` port and stable provider failures |
| `webchat/providers/openai.py` | Stateless OpenAI Responses API adapter with `store: false` and SSE parsing |
| `server/services.py` | Creates the chosen provider, dealer adapter, store, and runtime |
| `server/app.py` | Cookie/session HTTP API, per-conversation serialization, streaming, atomic commit |

### Frontend

| File | Responsibility |
| --- | --- |
| `dealership-website/src/features/webchat/webchat.js` | Chat widget lifecycle, streaming, state, rendering |
| `dealership-website/src/features/webchat/api.js` | Fetch wrapper with timeouts, NDJSON stream parsing, error normalization |
| `dealership-website/src/features/webchat/markdown.js` | Inline markdown to HTML (bold, italic, lists, links, code) |
| `dealership-website/src/features/webchat/webchat.css` | Widget layout, responsive sizing, animations |
| `dealership-website/src/app.js` | Page observation wiring and webchat initialization |

## Extension points

To add a dealer, implement `webchat.domain.dealer.DealerAdapter`, map its payloads to domain models,
normalize errors, and pass it to `ConversationRuntime`. Dealer URL paths, authentication, price
formats, retry rules, and response quirks stay in that adapter.

To add a model provider, implement `ConversationProvider.converse()`. It must accept the canonical
request and semantic tools, return either text or a bounded tool-call batch, preserve any required
within-turn continuation opaquely, expose usage/latency, and map provider failures to
`ConversationProviderError`. The harness and dealer adapter do not change.

The repository currently composes OpenAI only. "Provider-agnostic" describes the tested internal
port, not a claim that every external provider is already implemented.

## Verification

`python -m evals.verify` runs authored tests, unchanged supplied-platform tests, browser UI tests,
and a 60-scenario scripted corpus. The scripted lane uses model-shaped semantic decisions but real
harness execution and dealer fixtures. The billable `live-provider` lane changes only the
conversation provider. Reports include tool calls, dealer calls, state changes, writes, call count,
tokens, latency, answer assertions, and first divergence.

Configuration is server-side: `OPENAI_API_KEY`, `CHAT_MODEL`, `OPENAI_BASE_URL`, Northstar settings,
database path, origins, retention, and timeouts.
