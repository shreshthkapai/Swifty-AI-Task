# Thin Conversational Harness

## Goal

Replace the staged planner, deterministic conversation router, and separate grounded-response
pipeline with one coherent conversational model loop. The result must answer naturally, stream
quickly, and retain deterministic protection around dealership mutations. Northstar remains the
first dealer integration, not a dependency of the generic harness.

## Boundaries retained

The existing `webchat/domain/` contracts and `DealerAdapter` remain the semantic dealership port.
`webchat/adapters/northstar/` continues to own Northstar HTTP, authentication, mapping, validation,
retries, caching, and error translation. Conversation persistence remains server-side in SQLite,
and the browser continues to send live page context with each turn.

The harness depends on a provider-neutral `ConversationProvider`; the OpenAI implementation is
selected only in `server/services.py`. Neither Northstar payloads nor provider SDK objects enter
conversation state.

## Runtime

```text
user message + recent messages + structured state + page context
                              |
                     ConversationProvider
                       /               \
              final response       semantic tool calls
                                         |
                              validate and execute reads
                                         |
                              one provider continuation
                                         |
                                  final response
                              + declarative UI blocks
```

The same model owns understanding and final wording. A turn requires one provider call when the
available state is sufficient and two calls only when newly fetched dealer data must be seen before
answering. Independent safe reads execute concurrently. The runtime permits one tool round only;
it does not recurse until the model chooses to stop.

Tool results contain typed dealer facts and entity references. The final provider result contains
streamable conversational text plus stable entity/action references used to attach cards. Generic
conversation is never rendered from canned workflow prose.

## Tools and safety

The model sees semantic reads such as vehicle search, vehicle details, dealership hours,
test-drive slots, and workshop availability. It does not see raw REST endpoints.

Consequential tools prepare actions but cannot execute them. Deterministic code remains solely for
business invariants: required fields, customer verification, reserved or sold restrictions, live
slot and vehicle revalidation, confirmation, idempotency, expiry, and stable recovery from dealer
errors. Explicit UI confirmation and cancellation may bypass the model because their meaning is
already encoded by a trusted action reference; free-text conversation is not keyword-routed.

## State and context

Persisted state remains authoritative for selected entities, customer-supplied workflow fields,
pending actions, verification grants, and recently presented results. Recent messages and current
page context give the model conversational continuity. Context construction is a small serializer,
not an intent classifier: it applies size limits, removes redundant historical PII, and presents
facts in stable order without deciding what the user means.

## Latency and UX

Use one configurable fast model for both initial and continued responses. Keep instructions and
tool schemas compact, cache stable dealer records, parallelize independent reads, and expose
time-to-first-token and total turn latency. The HTTP API will stream text events before optional
cards and actions. The UI will preserve reading position, avoid forced bottom jumps, show concise
progress during tool execution, place direct prose before supporting cards, and avoid repeating
unchanged vehicle details.

## Failure handling

Provider transport or malformed-output failures return one short recovery message; there is no
repair-model call. Dealer failures are normalized by the adapter and translated into concise
recovery options. A failed read cannot mutate state. A failed mutation retains its stable
idempotency identity and exposes retry only when the normalized failure is retryable.

## Tests and migration

Tests will first lock natural multi-turn behaviour, one-call versus two-call budgets, tool-result
continuation, page references, mutation safety, streaming order, and latency instrumentation. The
new runtime will be introduced behind the existing server boundary, then server, conversation,
corpus, and UI fixtures will migrate to the new provider contract.

After parity is proven, obsolete planning, scope, tool-gating, response-mode, claim-repair, and
deterministic conversational rendering modules and tests will be deleted. Domain, Northstar
adapter, persistence, policy primitives, structured state, declarative UI records, and supplied
platform code remain.
