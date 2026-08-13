# Live Planning Contract Corrections

## Goal

Raise live-provider conversational quality without weakening business rules, replacing the bounded `TurnPlan`, or teaching the reusable harness Northstar-specific IDs. The scripted lane remains the regression oracle; live changes must improve the same customer-facing corpus through the same runtime.

## Chosen Approach

Repair the planner contract and deterministic orchestration at their existing boundaries. Prompt-only tuning would leave internal-ID and prerequisite-read failures probabilistic. Evaluator-only relaxation would conceal genuine workflow failures. A new agent loop would add cost and undermine the one-call baseline. The selected approach keeps one planning call, semantic dealership intentions, deterministic execution, and dealer-independent resolution.

## Semantic Scoring

Command expectations describe required semantic constraints, not byte-identical JSON. Before comparison, remove null-valued properties and canonicalize the explicit case-insensitive dealership filter fields such as make, fuel, transmission and body style. IDs, dates, contact data and amounts remain exact. Scripted plans do not imply byte-exact expectations for model-derived message, subject, reason or notes wording; those are judged through the rendered confirmation and scenario facts. Expected structured arguments must be a recursive subset of actual arguments. Extra arguments remain visible in the report; scenarios may declare prohibited arguments when absence is material. Output facts, entity references, state changes, mutations, and side effects remain independently scored, so normalization cannot turn an incorrect business result into a pass.

Focused proof: `vehicle-01` must stop failing merely because the provider emits nullable schema fields, while tests must still reject missing `body_style: SUV` and contradictory values.

## Dealership Reference Resolution

Semantic commands may carry either a known `dealership_id` or customer-facing `dealership_query`. A dealer-independent resolver obtains authoritative locations through `DealerAdapter.list_dealerships()` and resolves, in order: exact stable ID, exact case-insensitive name, then exact case-insensitive town. An existing `dealership_id` value such as `Manchester` may be treated as a query for backward compatibility. No location names or Northstar IDs are hardcoded. Missing, unknown, or ambiguous references return deterministic clarification and location choices; they never guess.

The resolver is used consistently by dealership details/hours, vehicle and slot filters, and dealership-bound preparations. Focused proof: `dealer-05` must answer Liverpool parts details from the resolved `northstar-liverpool` record.

## Intent-Level Command Orchestration

The model chooses a customer intent; handlers own mandatory supporting reads. `find_test_drive_slots` resolves the selected vehicle, reads live availability, blocks reserved/sold vehicles through policy, and only then retrieves slots. Dealership hours resolve the location before reading hours. Finance enquiry and part-exchange preparation attach authoritative business qualifications without requiring the model to remember a second read.

Commands may perform bounded safe reads internally, but never multiple consequential actions. Mutations still require prepare, explicit confirmation, live revalidation, and idempotent execution. Corpus expectations distinguish the semantic command from required adapter calls. Focused proof: `sales-04` may use one `find_test_drive_slots` command but must record availability before slot retrieval.

Compound workshop amendments may express a stable slot ID or an ordinal/date preference. The handler may read current slots, resolve the requested choice, and prepare one amendment; it may not execute it. This preserves the one-call planner baseline for requests such as "move it to the second available slot."

## State-Aware Preparation

Preparation inputs are reduced from three sources: validated new arguments, selected entities, and persisted structured customer/workflow fields. Null or omitted planner fields never erase known state. Explicit new customer values override persisted values only after validation. Required-field checks run after the merge. The resulting typed request is snapshotted in `PendingAction`; execution continues to use the snapshot plus live revalidation.

Focused proof: `workshop-06` must reuse known registration and mileage, prepare a confirmation, and create exactly one booking only after the following confirmation turn.

## Safe Provider Diagnostics

Provider transport/refusal/JSON failures remain `PROVIDER_FAILURE`. A syntactically returned plan rejected by harness validation is recorded separately with a stable stage and validation code, such as `unknown_command`, `invalid_arguments`, `invalid_strategy`, or `unsafe_batch`. Reports never contain raw responses, hidden reasoning, credentials, or provider response IDs.

Focused proof: one current `invalid_response` scenario must produce an actionable sanitized classification, then pass after the relevant schema or planning instruction is corrected.

## Verification Loop

Each boundary follows red-green testing, then one billable focused live scenario:

1. scoring: `vehicle-01`;
2. reference resolution: `dealer-05`;
3. prerequisite orchestration: `sales-04`;
4. state merge and confirmation: `workshop-06`;
5. provider validation: `dealer-06`.

After each live run, compare the generated JSON's first divergence, commands, adapter calls, state changes, and model-call count. Generated reports remain under ignored `artifacts/`; no stage output is committed. Final acceptance requires the complete key-free suite to remain green, one complete live corpus run, and three consecutive live passes of the critical safety, ambiguity, confirmation, verification, stale-state, and scope subset. Any remaining failures are fixed at the first responsible layer rather than by loosening unrelated expectations.

## Non-Goals

No recursive tool loop, second synthesis call, model upgrade, Northstar location table in the harness, user accounts, long-term profiles, raw-provider trace storage, or changes to supplied platform behaviour.
