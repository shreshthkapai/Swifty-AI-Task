# Full Corpus Evaluation

## Goal

Provide one truthful reviewer command that runs every deterministic verification surface and
records pass/fail evidence for all 60 conversational scenarios and 74 turns. Corpus definitions
must be executed through the public `HarnessRuntime` boundary, not merely schema-validated or
reported as passing because their files load.

## Command Contract

```powershell
python -m evals.verify
python -m evals.verify --only authored
python -m evals.verify --only platform
python -m evals.verify --only corpus
python -m evals.verify --only corpus --scenario sales-02
python -m evals.verify --only corpus --lane live-provider
```

The default runs authored tests, supplied platform tests, and the complete scripted corpus. It is
deterministic, key-free, and exits non-zero if any selected suite, scenario, or turn fails. The
live-provider lane is always explicit because it is probabilistic, billable, and requires provider
configuration. Selecting one scenario is a debugging aid and must not be reported as a full run.

## Driver Boundary

`ScriptedConversationDriver` implements the existing `ConversationDriver` protocol. Each scenario
creates a fresh named state fixture, dealer fixture, fixed clock, and deterministic identifier
source. Turns within that scenario share state and dealer side effects.

For free-text turns, the scripted provider returns the corpus `TurnPlan`; it does not return dealer
data or expected answers. Action turns continue through deterministic runtime routing. The actual
harness therefore remains responsible for command validation, policy, dealer calls, recovery,
state reduction, rendering, model-call accounting, and mutation safety.

Fixture registries must cover every named corpus fixture and fail closed on unknown names. Dealer
fixtures implement the dealer-independent protocol and expose deterministic records, failures,
call history, and business side effects. Abstract corpus action references such as
`pending-action` are resolved to the runtime-issued reference in current state or rendered blocks.
No scenario may be skipped silently.

The live-provider lane uses the same corpus, fixtures, state, clock, runtime and scorer. Its only
intentional difference is the `PlanningProvider`. This preserves lane parity and makes planner
quality measurable independently from downstream harness behaviour.

## Observation and Scoring

The driver converts each real `TurnResult` into `ObservedTurn`. Observations are derived from
executed commands, declarative response blocks, structured state, dealer call/side-effect records,
and provider usage—not copied from the turn expectation.

The existing scorer evaluates required and prohibited commands, exact command arguments,
forbidden calls and mutations, state changes, side effects, response strategy, block types,
entities, business facts, notices, next steps, forbidden claims, directness, model-call budgets,
and deterministic exact text. A failure records the first divergent category, path, expected value,
and actual value.

## JSON Evidence

`artifacts/evals/reviewer-report.json` contains run metadata and every selected result:

- evaluated commit, timestamp, Python and corpus/scoring/fixture versions;
- suite totals and failure names;
- overall and per-domain scenario/turn success;
- each customer input and expected observable contract;
- actual response blocks, commands and arguments, state delta and side effects;
- model calls, token counts and latency;
- pass/fail plus structured first-divergence evidence.

Reports use canonical JSON ordering and are written even when evaluation fails. Raw generated
reports remain gitignored. They must not contain API keys, authorization headers, cookies, hidden
reasoning, provider envelopes, or persistent customer data. Hermetic fixture identities may appear
only in gitignored run artifacts.

## Verification

Tests must prove fixture coverage, scenario isolation, multi-turn state continuity, reference
resolution, actual-runtime execution, honest failure scoring, sanitized report serialization,
non-zero failure exits, focused selection, and lane parity. The implementation is complete only
when the default command executes all 60 scenarios and 74 turns, produces a per-turn JSON record,
and reports measured results without conflating corpus size with passing task success.
