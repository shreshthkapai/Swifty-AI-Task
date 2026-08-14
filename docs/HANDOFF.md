# Thin Conversational Harness Handoff

## What changed

The previous staged runtime was replaced by one bounded conversational loop. There is no separate
scope classifier, planner, dynamic tool gate, grounded-response model, repair call, or
question-specific answer router. One configurable model owns interpretation and customer-facing
language. Deterministic code remains where variability would be unsafe: command validation,
business policy, confirmation, authorization, idempotency, mutation execution, and trusted UI
actions.

The migration removed more than ten thousand lines of superseded runtime and test code. Domain
models, the dealer interface, Northstar adapter, persistence, policy, workflows, evidence records,
rendering, and supplied application remain reusable.

## Runtime code map

| File | Responsibility |
| --- | --- |
| `webchat/harness/turn.py` | Input/output for one turn and live-page state refresh |
| `webchat/harness/conversation.py` | Provider-neutral conversation, tool-call, continuation, usage contracts |
| `webchat/harness/tools.py` | Static dealer-independent semantic command catalogue |
| `webchat/harness/conversation_tools.py` | Converts validated model calls into commands or controls |
| `webchat/harness/conversation_runtime.py` | One initial call, optional tool execution, one continuation |
| `webchat/harness/execution.py` | Executes interpreted commands and trusted UI actions |
| `webchat/harness/policy.py` | Deterministic business validation |
| `webchat/harness/workflows/` | Maps semantic commands to `DealerAdapter` operations, state, evidence, UI blocks |
| `webchat/providers/base.py` | `ConversationProvider` port and stable provider failures |
| `webchat/providers/openai.py` | Stateless OpenAI Responses API adapter with `store: false` and SSE parsing |
| `server/services.py` | Creates the chosen provider, dealer adapter, store, and runtime |
| `server/app.py` | Cookie/session HTTP API, per-conversation serialization, streaming, atomic commit |

## Exact call paths

Ordinary advice or a clarification:

```text
user text -> model -> streamed direct answer                       (1 model call)
```

Dealer-backed question:

```text
user text -> model tool call -> validated dealer read
          -> model sees returned facts -> answer + cards           (2 model calls)
```

Consequential request:

```text
user text -> model prepare tool -> deterministic validation
          -> pending action + confirmation UI                      (1 model call)
clicked confirm -> live revalidation -> idempotent dealer write    (0 model calls)
```

The runtime rejects a second tool round. Multiple reads are allowed only as one bounded batch;
independent reads run concurrently. Preparations and action controls cannot be combined with other
calls. Provider errors receive one concise recovery response and no hidden retry-model call.

## Data ownership

The model receives a canonical JSON snapshot containing the current message, server time, selected
entities, preferences, workflow, pending-action metadata, active verification grants, up to three
recent presentation groups, up to eight recent messages, current page observation, and normalized
prior failures. Historical known email, phone, and registration values are redacted from repeated
message text; structured state remains authoritative.

Provider continuation data is opaque and exists only between the two calls of one turn. It is not
conversation memory and is not persisted. The OpenAI adapter uses `store: false`; subsequent turns
are reconstructed from application-owned state and messages.

Dealer truth travels in the opposite direction: Northstar JSON is validated and mapped to immutable
domain objects, workflows create evidence and declarative cards, and the continuation receives a
compact structured tool result. Neither raw Northstar JSON nor HTTP errors enter the model-facing
harness contract.

## Extension points

To add a dealer, implement `webchat.domain.dealer.DealerAdapter`, map its payloads to domain models,
normalize errors, and pass it to `ConversationRuntime`. Dealer URL paths, authentication, price
formats, retry rules, and response quirks stay in that adapter.

To add a model provider, implement `ConversationProvider.converse()`. It must accept the canonical
request and semantic tools, return either text or a bounded tool-call batch, preserve any required
within-turn continuation opaquely, expose usage/latency, and map provider failures to
`ConversationProviderError`. The harness and dealer adapter do not change.

The repository currently composes OpenAI only. “Provider-agnostic” describes the tested internal
port, not a claim that every external provider is already implemented.

## Safety invariants to preserve

- Never add free-text `if/elif` intent routing to production runtime code.
- Never expose raw dealer endpoints as model tools.
- Never let a preparation execute a mutation.
- Never execute a mutation without a current pending action and explicit confirmation.
- Always revalidate live vehicle/slot/booking state immediately before a write.
- Preserve stable idempotency keys across safe retries.
- Keep verification grants scoped to a booking and explicitly expiring.
- Commit messages and structured state together, only after a completed turn.
- Never log prompt text, model output, customer PII, API keys, or raw dealer payloads.

## Operations and verification

Configuration is server-side: `OPENAI_API_KEY`, `CHAT_MODEL`, `OPENAI_BASE_URL`, Northstar settings,
database path, origins, retention, and timeouts. There is one model setting; no planner-model setting
remains.

Use focused tests during development, then run:

```bash
python -m evals.verify
```

This is the reviewer command. A focused live check is:

```bash
python -m evals.verify --only corpus --lane live-provider --scenario vehicle-01
```

The complete live corpus is billable and should be run once after deterministic verification:

```bash
python -m evals.verify --only corpus --lane live-provider
```

Raw reports remain under gitignored `artifacts/`. Do not commit `.env`, SQLite files, raw traces, or
customer data.
