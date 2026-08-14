# Solution Architecture

## Design goal

This is a production-shaped dealership chatbot harness with Northstar as its first integration—not
a general-purpose agent framework. It improves on a direct prompt-to-API call by making business
truth, actions, memory, recovery, and evaluation explicit and testable.

## Boundaries

| Layer | Responsibility | Depends on |
| --- | --- | --- |
| `webchat/domain/` | Immutable dealership models, stable failures, `DealerAdapter` protocol | Python types only |
| `webchat/harness/` | State, scope, planning, tool gating, workflows, policies, evidence, rendering | Domain and provider protocols |
| `webchat/providers/` | Translate provider-neutral planning and grounded-response contracts | Current OpenAI HTTP adapter |
| `webchat/adapters/northstar/` | Northstar REST transport, authentication, mapping, caching, retries, error normalization | `DealerAdapter` contracts |
| `webchat/persistence/` | Atomic transcript and structured-state storage | `ConversationStore` protocol; SQLite implementation |
| `server/` | HTTP/session boundary, composition, concurrency control, redacted observability | Harness, store, configured adapters |
| `dealership-website/` | Supplied site plus declarative chat UI | Chat HTTP API |

The harness contains no Northstar, OpenAI, Claude, or Kimi types. A new dealership implements
`DealerAdapter`; a new model company implements `PlanningProvider` and `GroundedResponseProvider`.
Those are integration ports rather than a dynamic plug-in framework: composition happens once in
the server startup layer, keeping runtime behaviour explicit.

## Turn lifecycle

```text
message + live page context + persisted state
                    |
          conservative scope/fast-path check
              /                         \
       deterministic                 context compiler
       command/action                     |
              |                    gated semantic tools
              |                           |
              |                    typed model TurnPlan
              +-------------+-------------+
                            |
                 policy and command handlers
                            |
                       DealerAdapter
                            |
                domain facts + UI structures
                            |
             evidence-grounded answer/validation
                            |
                state reduction + atomic commit
```

Each ordinary planned turn is bounded: compile relevant state, request one typed plan, validate a
small command batch, and execute deterministic handlers. There is no recursive tool loop. Read
commands may be combined when useful; multiple consequential actions are rejected.

Fresh dealer results become typed evidence before conversational synthesis. The grounded responder
must classify claims and reference evidence IDs. Validation rejects unknown references,
unsupported dealer facts, invented exact specifications, and claims that contradict explicit
unknowns. One controlled repair is allowed; failure produces a factual deterministic fallback.
Cards and actions are rendered separately from prose, so one component owns the customer answer.

## Deterministic and model responsibilities

Deterministic code handles meaning already established by application state: explicit UI actions,
confirm/cancel, selecting a presented vehicle or slot, retry, show-more, obvious out-of-domain
redirects, validation, policy enforcement, state transitions, execution, and recovery. These paths
normally use zero model calls.

The model handles ambiguous natural-language intent, reference interpretation, dealership-adjacent
advice, bounded multi-intent planning, and natural synthesis from supplied evidence. It receives a
budgeted context snapshot and only relevant semantic tools—not raw HTTP endpoints, full database
records, or the complete transcript. The model may propose; it cannot authorize or directly execute
a mutation.

## State, safety, and persistence

`ConversationState` records customer-supplied workflow fields, page and selected entities,
preferences, presentation groups, workflow stage, pending actions, and expiring verification
grants. The opaque conversation ID is stored in an `HttpOnly`, `SameSite=Lax` cookie; messages and
state remain server-side in SQLite. Optimistic revisions and atomic message-plus-state commits
prevent concurrent turns from silently overwriting each other.

Consequential operations use `prepare -> validate -> explicit confirm -> revalidate live state ->
execute`. `PendingAction` owns confirmation status, expiry, attempts, and stable idempotency key.
Hard code—not prompt wording—enforces required customer data, vehicle status, current slot validity,
workshop verification, legal workflow transitions, and retry rules. Northstar errors are mapped to
stable domain failures so recovery never parses prose.

## Portability

To integrate another dealer, map its records and failures to `webchat/domain/`, implement every
applicable `DealerAdapter` operation, and add adapter contract tests. Dealer authentication,
endpoint paths, payload quirks, caching, and retry behaviour remain inside that adapter.

To integrate another model provider, translate the provider-neutral planning and grounded-response
requests/results in a new `webchat/providers/` module and select it at composition. Authoritative
state, semantic tools, policy, evidence validation, and dealership execution do not change. The
current implementation ships only the OpenAI adapter because the assignment needs one model, not a
multi-provider framework.

## Test strategy

The suite tests boundaries independently before exercising complete conversations:

| Tests | Coverage |
| --- | --- |
| `tests/webchat/` | Domain invariants, state, persistence, scope, context, tool gate, policy, workflows, evidence, provider translation, and Northstar mapping/HTTP contracts |
| `tests/server/` | Cookies, session restoration, atomic commits, conflicts, configuration, API errors, and PII-safe logging |
| `dealership-website/tests/` | Chat interaction, rendering, page context, accessibility behaviour, and scrolling |
| `dealership-platform/tests/` | Supplied platform behaviour, run unchanged |
| `tests/conversations/` | Multi-turn answers, references, mutations, adversarial inputs, recovery, and grounding |
| `evals/corpus.json` | 60 versioned scenarios covering supplied edge cases and additional failure/scope cases |

`python -m evals.verify` is the reviewer entry point. Its scripted lane uses valid provider fixtures
while running the real harness, policies, dealer fixture, state reducer, renderer, and scorer. The
`live-provider` lane changes only the planning and answer providers, enabling a like-for-like model
check without weakening deterministic assertions. Reports capture commands, arguments, state
changes, side effects, model usage, latency, expectations, and first divergence; raw reports stay
under gitignored `artifacts/`.
