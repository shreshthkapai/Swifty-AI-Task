# Live Planning Contract Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the planner-facing contract so the existing bounded harness resolves natural dealership references, owns prerequisite reads and persisted state, and produces a meaningful live-provider score.

**Architecture:** Preserve one provider call and the existing `TurnPlan`. Add semantic comparison at the evaluator boundary, a pure resolver over `DealerAdapter.list_dealerships()`, and bounded orchestration inside existing workflow handlers. Keep mutations behind persisted confirmation and classify provider transport separately from invalid planner output.

**Tech Stack:** Python 3.12+, stdlib `unittest`, frozen dataclasses/enums, async `DealerAdapter`, `httpx`, OpenAI Responses API structured output.

## Global Constraints

- Do not change supplied dealership platform behaviour or seed data.
- Do not hardcode Northstar dealership IDs or towns in the reusable harness.
- Do not add recursive tool loops, a second synthesis call, or another model/provider.
- Generated live reports remain under ignored `artifacts/`; never commit `.env`, keys, raw responses, reasoning, or PII.
- Every behavioural change follows red-green testing and preserves explicit confirmation, live revalidation, idempotency, and zero-model deterministic paths.

---

### Task 1: Semantic Argument Scoring

**Files:**
- Modify: `evals/run.py`
- Test: `tests/evals/test_runner.py`

**Interfaces:**
- Produces: `_arguments_satisfy(name: str, expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool`
- Uses: `webchat.harness.tool_gate.command_spec()` to identify enum-valued fields.

- [ ] **Step 1: Write failing semantic-comparison tests**

Add focused scorer cases proving omitted versus emitted nulls and enum casing pass, while a missing or contradictory required constraint fails:

```python
def test_command_arguments_compare_required_semantics_not_raw_nullable_json(self):
    self.assertTrue(_arguments_satisfy(
        "search_vehicles",
        {"body_style": "SUV"},
        {"body_style": "suv", "make": None, "sort": None},
    ))
    self.assertFalse(_arguments_satisfy(
        "search_vehicles",
        {"body_style": "SUV"},
        {"body_style": "Hatchback", "make": None},
    ))
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.evals.test_runner -v`

Expected: failure because `_arguments_satisfy` does not exist and raw frozen arguments are compared exactly.

- [ ] **Step 3: Implement required-subset comparison**

Drop null-valued actual properties for comparison, recursively require every expected key, and case-fold only fields whose semantic schema declares an enum. Keep IDs, free text, dates, amounts, and non-enum strings exact. Replace the raw equality check in `_score_turn` with this helper. Do not change answer, state, call, mutation, or side-effect scoring.

- [ ] **Step 4: Verify GREEN and focused live improvement**

Run:

```powershell
python -m unittest tests.evals.test_runner tests.evals.test_corpus -v
python -m evals.verify --only corpus --lane live-provider --scenario vehicle-01
```

Expected: unit tests pass; `vehicle-01` no longer diverges because of nullable fields or `SUV` casing. Record only the generated ignored JSON.

- [ ] **Step 5: Commit**

```powershell
git add evals/run.py tests/evals/test_runner.py
git commit -m "Score semantic planner arguments"
```

### Task 2: Dealer-Independent Dealership Resolution

**Files:**
- Create: `webchat/harness/workflows/references.py`
- Modify: `webchat/harness/tool_gate.py`
- Modify: `webchat/harness/workflows/dealerships.py`
- Modify: `webchat/harness/workflows/sales.py`
- Modify: `webchat/harness/workflows/workshop.py`
- Modify: `webchat/harness/workflows/vehicles.py`
- Test: `tests/webchat/harness/test_reads.py`
- Test: `tests/webchat/harness/test_preparations.py`
- Test: `tests/webchat/harness/test_tool_gate.py`

**Interfaces:**
- Produces: `DealershipResolution(location: DealerLocation | None, candidates: tuple[DealerLocation, ...])`
- Produces: `resolve_dealership_reference(dealer: DealerAdapter, *, dealership_id: str | None, dealership_query: str | None, selected_id: str | None) -> DealershipResolution`
- Resolution order: exact ID, exact case-insensitive full name, exact case-insensitive town; zero or multiple matches remain unresolved.

- [ ] **Step 1: Write failing resolver and workflow tests**

Cover canonical IDs, `Liverpool`, `Northstar Liverpool`, unknown text, no reference, and an hours/details handler using the resolved canonical ID:

```python
resolution = await resolve_dealership_reference(
    dealer,
    dealership_id=None,
    dealership_query="Liverpool",
    selected_id=None,
)
self.assertEqual(resolution.location.id, "northstar-liverpool")
```

Assert unresolved references render clarification plus stable dealership choices and make no details/hours mutation.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.webchat.harness.test_reads tests.webchat.harness.test_preparations tests.webchat.harness.test_tool_gate -v`

Expected: imports/schema assertions fail because the resolver and `dealership_query` do not exist.

- [ ] **Step 3: Implement the pure resolver and semantic fields**

Add nullable `dealership_query` beside `dealership_id` on dealership-bound tools. Describe it as customer-facing location wording and instruct the planner to use it when no stable ID is present in context. Treat an unrecognized `dealership_id` such as `Manchester` as a query for backward compatibility. Use only records returned by `list_dealerships()` or `list_workshop_locations()`.

- [ ] **Step 4: Integrate resolution consistently**

Resolve before dealership details/hours, vehicle dealership filters, test-drive/workshop slot searches, sales enquiry, callback, part exchange, and dealership message preparation. Persist only the resolved stable ID. For unresolved/ambiguous input, return location choices and a concise clarification rather than guessing or calling the mutation.

- [ ] **Step 5: Verify GREEN and focused live improvement**

Run:

```powershell
python -m unittest tests.webchat.harness.test_reads tests.webchat.harness.test_preparations tests.webchat.harness.test_tool_gate tests.webchat.harness.test_runtime -v
python -m evals.verify --only corpus --lane live-provider --scenario dealer-05
```

Expected: `dealer-05` calls `list_dealerships`, resolves Liverpool, calls `get_dealership`, and renders the authoritative parts contact record in one turn.

- [ ] **Step 6: Commit**

```powershell
git add webchat/harness/workflows/references.py webchat/harness/tool_gate.py webchat/harness/workflows tests/webchat/harness
git commit -m "Resolve natural dealership references"
```

### Task 3: Intent-Level Supporting Reads

**Files:**
- Modify: `webchat/harness/runtime.py`
- Modify: `webchat/harness/workflows/sales.py`
- Modify: `webchat/harness/workflows/workshop.py`
- Modify: `webchat/harness/workflows/vehicles.py`
- Modify: `evals/schema.py`
- Modify: `evals/run.py`
- Modify: `evals/corpus.json`
- Test: `tests/webchat/harness/test_runtime.py`
- Test: `tests/conversations/test_runtime_answers.py`
- Test: `tests/evals/test_corpus.py`
- Test: `tests/evals/test_runner.py`

**Interfaces:**
- Adds: `TurnExpectation.required_calls: tuple[str, ...]`
- Behaviour: `find_test_drive_slots` owns `get_vehicle_availability` before `list_test_drive_slots` when a vehicle is known.

- [ ] **Step 1: Write failing prerequisite-read tests**

Assert available vehicles produce availability then slot calls, reserved/sold vehicles produce policy recovery without a slot call, and the corpus can require adapter calls separately from semantic commands:

```python
self.assertEqual(
    [call.operation for call in fake.calls],
    ["get_vehicle_availability", "list_test_drive_slots"],
)
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.webchat.harness.test_runtime tests.conversations.test_runtime_answers tests.evals.test_corpus tests.evals.test_runner -v`

Expected: no availability call and no `required_calls` corpus field.

- [ ] **Step 3: Implement bounded orchestration**

Pass the injected `PolicyEngine` to read handlers through their uniform runtime interface. In the test-drive slot handler, resolve the vehicle, read availability, call `require_test_drive_eligible`, render deterministic recovery when blocked, and retrieve slots only when eligible. Add finance/part-exchange business-information reads inside their preparation handlers and render the authoritative qualification alongside the confirmation without adding another model call.

- [ ] **Step 4: Separate semantic commands from adapter-call expectations**

Parse, serialize, and score `required_calls`. Update test-drive corpus turns to require one semantic `find_test_drive_slots` command plus both adapter calls. Keep prohibited calls and mutations unchanged. Update finance and part-exchange scenarios to require their authoritative supporting read as an adapter call rather than a second planner command.

- [ ] **Step 5: Verify GREEN and focused live improvement**

Run:

```powershell
python -m unittest tests.webchat.harness.test_runtime tests.conversations.test_runtime_answers tests.evals.test_corpus tests.evals.test_runner -v
python -m evals.verify --only corpus --lane live-provider --scenario sales-04
```

Expected: `sales-04` uses one model call and one semantic slot command, but evidence shows availability was checked before slots.

- [ ] **Step 6: Commit**

```powershell
git add webchat/harness/runtime.py webchat/harness/workflows evals tests
git commit -m "Orchestrate required dealership reads"
```

### Task 4: State-Aware Preparation Reduction

**Files:**
- Modify: `webchat/harness/workflows/common.py`
- Modify: `webchat/harness/workflows/sales.py`
- Modify: `webchat/harness/workflows/workshop.py`
- Modify: `webchat/harness/tool_gate.py`
- Test: `tests/webchat/harness/test_preparations.py`
- Test: `tests/webchat/harness/test_runtime.py`
- Test: `tests/conversations/test_workflows.py`

**Interfaces:**
- Produces: `first_known(*values: Any) -> Any | None`, preserving `0` and ignoring only `None`/empty string.
- Adds workshop amendment arguments: nullable `slot_ordinal` integer and existing date/dealer/service search fields needed to resolve a fresh choice.

- [ ] **Step 1: Write failing state-merge tests**

Assert null planner fields retain customer, registration, mileage, selected slots and authorised booking IDs from structured state. Assert an explicit validated correction wins. Assert ordinal amendment selection performs safe slot discovery and prepares, but does not execute, one amendment.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.webchat.harness.test_preparations tests.webchat.harness.test_runtime tests.conversations.test_workflows -v`

Expected: workshop booking reports missing mileage and ordinal amendment cannot prepare.

- [ ] **Step 3: Implement state reduction**

Merge in this precedence order: explicit non-empty argument, selected entity, workflow gathered field, customer state. Run validation and missing-field checks after merging. Persist corrected identity values only after validation. For amendment ordinals, list bounded matching workshop slots, resolve the ordinal from returned order, and store its stable slot ID in the pending action.

- [ ] **Step 4: Verify GREEN and focused live improvement**

Run:

```powershell
python -m unittest tests.webchat.harness.test_preparations tests.webchat.harness.test_runtime tests.conversations.test_workflows -v
python -m evals.verify --only corpus --lane live-provider --scenario workshop-06
```

Expected: the first turn prepares a complete workshop request from persisted state; `confirm` performs exactly one workshop booking with zero additional model calls.

- [ ] **Step 5: Commit**

```powershell
git add webchat/harness/workflows webchat/harness/tool_gate.py tests/webchat/harness tests/conversations
git commit -m "Merge structured state into preparations"
```

### Task 5: Safe Planner-Failure Diagnostics and Guidance

**Files:**
- Modify: `webchat/providers/base.py`
- Modify: `webchat/providers/openai.py`
- Modify: `webchat/harness/planning.py`
- Modify: `evals/driver.py`
- Modify: `evals/run.py`
- Modify: `evals/reporting.py`
- Test: `tests/webchat/providers/test_openai.py`
- Test: `tests/webchat/harness/test_planning.py`
- Test: `tests/evals/test_driver.py`
- Test: `tests/evals/test_runner.py`
- Test: `tests/evals/test_reporting.py`

**Interfaces:**
- Produces: `PlanningOutputErrorKind` with `INVALID_JSON` and `INVALID_PLAN`.
- Produces: `PlanningOutputError(kind: PlanningOutputErrorKind)` containing no raw output.
- Adds: `ObservedTurn.planner_failure: str | None` and `FailureCategory.PLANNER_FAILURE`.

- [ ] **Step 1: Write failing classification and redaction tests**

Assert transport/HTTP/refusal remain provider failures, malformed JSON becomes `INVALID_JSON`, rejected plan semantics become `INVALID_PLAN`, and reports contain only the stable code. Poisoned reasoning/response IDs must still be rejected.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.webchat.providers.test_openai tests.webchat.harness.test_planning tests.evals.test_driver tests.evals.test_runner tests.evals.test_reporting -v`

Expected: invalid planner output is currently collapsed into `ProviderErrorKind.INVALID_RESPONSE`.

- [ ] **Step 3: Implement safe error separation**

Catch JSON decoding and `PlanValidationError` separately in the provider adapter and raise stable `PlanningOutputError` values without chaining customer-visible raw content. Capture this separately in the driver, score it as `PLANNER_FAILURE`, and serialize only its enum value.

- [ ] **Step 4: Strengthen bounded planner instructions**

Update provider instructions and semantic descriptions with four exact rules: use `dealership_query` for customer location wording; choose an intent tool even when handlers must collect missing fields; never add prerequisite reads owned by a semantic handler; emit at most one preparation command and pair it with `action_prepared`. Do not add examples tied to corpus wording.

- [ ] **Step 5: Verify GREEN and focused live diagnosis**

Run:

```powershell
python -m unittest tests.webchat.providers.test_openai tests.webchat.harness.test_planning tests.evals.test_driver tests.evals.test_runner tests.evals.test_reporting -v
python -m evals.verify --only corpus --lane live-provider --scenario dealer-06
```

Expected: the scenario either passes or produces a stable `PLANNER_FAILURE` code with no traceback or sensitive content. If it produces `INVALID_PLAN`, use the recorded validation class and the existing plan contract to correct that specific rule before rerunning this same scenario.

- [ ] **Step 6: Commit**

```powershell
git add webchat/providers webchat/harness/planning.py webchat/harness/tool_gate.py evals tests
git commit -m "Separate planner output failures"
```

### Task 6: Full Verification and Audit

**Files:**
- Modify only if verification exposes a reproducible regression covered by a new failing test.
- Generated, ignored: `artifacts/evals/reviewer-report.json`

**Interfaces:**
- Acceptance commands remain `python -m evals.verify` and `python -m evals.verify --only corpus --lane live-provider`.

- [ ] **Step 1: Run all key-free verification**

Run: `python -m evals.verify`

Expected: authored and supplied tests pass; scripted corpus remains 60/60 scenarios and 74/74 turns.

- [ ] **Step 2: Run the full live-provider corpus**

Run: `python -m evals.verify --only corpus --lane live-provider`

Expected: all 60 scenarios execute without traceback. Inspect every remaining first divergence; do not reinterpret a genuine missing command, unsafe action, wrong state, invented fact, or missing recovery as a pass.

- [ ] **Step 3: Repeat the critical subset three times**

Run the existing scenario filter for reserved/sold vehicles, stale slots, confirmation, workshop verification, ordinal reference, scope redirects, adjacent advice, mixed scope, and API recovery. Each run must preserve zero mutations where prohibited and exactly one mutation after confirmation.

- [ ] **Step 4: Audit repository state**

Run:

```powershell
git diff --check
git status --short
git ls-files .env artifacts
python -m compileall -q webchat server evals tests
```

Expected: no secrets/generated reports tracked, no supplied platform changes, no syntax failures, and only intentional source/test/doc changes.

- [ ] **Step 5: Commit any final tested correction**

Use one focused imperative commit only when Step 2 exposed a reproducible defect with a red-green test. Do not commit the live report or a promotional baseline.
