# Full Corpus Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `python -m evals.verify` execute and score all 60 scripted conversations and write every one of their 74 turn results to sanitized JSON.

**Architecture:** A corpus driver creates isolated named state/dealer fixtures, feeds each corpus plan through the real `HarnessRuntime`, derives observations only from runtime/dealer outputs, and passes those observations to the existing scorer. Reporting serializes expectations, observations, scores and first divergences; the verifier composes test suites and corpus lanes without treating corpus loading as execution.

**Tech Stack:** Python 3.12+, frozen dataclasses, `unittest`, existing dealer-domain contracts, `HarnessRuntime`, `PlanningProvider`, canonical JSON.

## Global Constraints

- Work directly on `main`; do not create a PR, worktree or subagent.
- The default command is deterministic and key-free; live-provider execution is explicit.
- Scripted plans may supply planner decisions only, never expected downstream results.
- Every corpus fixture name must resolve or fail before execution; no skips.
- Dealer facts come from fixture dealer results and runtime blocks, never turn expectations.
- Generated reports stay under gitignored `artifacts/evals/` and exclude credentials, cookies, provider envelopes, hidden reasoning and persistent customer data.
- Every behavioural change follows a witnessed red/green test cycle.

---

### Task 1: Detailed evaluation records

**Files:**
- Modify: `evals/run.py`
- Modify: `tests/evals/test_runner.py`

**Interfaces:**
- Consumes: `Scenario`, `CorpusTurn`, `ObservedTurn`, `TurnScore`, `score_turn()`.
- Produces: `EvaluatedTurn`, `DetailedEvaluationReport`, and `run_detailed_evaluation(corpus, driver, *, lane)`.

- [ ] **Step 1: Write failing result-contract tests**

Add literal assertions proving a detailed result retains category/title, input, expectation,
observation, score and first divergence, and proving report totals/per-category rates derive from
scores rather than corpus size.

```python
record = EvaluatedTurn(scenario, turn, observed, score)
self.assertEqual(record.scenario_id, "sales-02")
self.assertFalse(record.score.passed)
self.assertEqual(record.score.divergence.path, "answer.facts")
```

- [ ] **Step 2: Run the focused tests and witness the missing-contract failure**

```powershell
python -m unittest tests.evals.test_runner -v
```

- [ ] **Step 3: Add immutable detailed records without changing the existing scorer**

`run_detailed_evaluation` must execute turns in corpus order and retain the exact observation that
was scored. Preserve `run_evaluation` for current consumers by implementing it as a projection of
the detailed result.

- [ ] **Step 4: Run focused evaluator tests**

```powershell
python -m unittest tests.evals.test_runner -v
```

---

### Task 2: Exhaustive state and dealer fixtures

**Files:**
- Create: `evals/fixtures.py`
- Create: `tests/evals/test_fixtures.py`

**Interfaces:**
- Produces: `FixtureRegistry`, `RecordingDealer`, `FixtureSession`,
  `build_state(name, *, now)`, and `build_dealer(name, *, now)`.
- Consumes: immutable domain models and `ConversationState`; it must not import Northstar HTTP
  implementation types.

- [ ] **Step 1: Write failing fixture-coverage and isolation tests**

Derive the required names from `evals/corpus.json` and compare them to registry keys. The exact
state registry must cover:

```text
empty, customer_known, selected_vehicle_slot_customer, confirmed_test_drive_pending,
vehicles_001_002_presented, selected_veh_019, bmw_search_under_40000,
selected_veh_001, selected_veh_007, selected_veh_013, selected_vehicle_and_slot,
failed_test_drive_idempotency_conflict, selected_veh_001_customer_known,
service_full_selected, mot_manchester_selected, full_service_bolton_selected,
workshop_slot_selected_no_customer, workshop_slot_customer_known,
confirmed_workshop_pending, verified_booking_001_new_slot_selected,
unverified_booking_id_known, verified_cancelled_booking, verified_booking_001,
confirmed_cancellation_pending, workshop_booking_pending, restored_selected_veh_001,
workshop_stockport_search, vehicle_results_page_1,
test_drive_slots_presented_customer_known, test_drive_booking_pending, selected_x3
```

The dealer registry must cover:

```text
seeded, bank_holiday, no_matching_vehicles, stale_test_drive_slot,
idempotent_test_drive, idempotency_conflict, bolton_first_week_empty,
stale_workshop_slot, cancelled_booking, idempotent_cancellation,
lookup_amend_cancel_sequence, vehicle_test_drive_sequence, seeded_x3,
hours_temporary_failure_after_vehicle_search
```

Also prove two sessions do not share call records, mutable counters, slots or side effects.

- [ ] **Step 2: Run fixture tests and witness unknown-fixture failures**

```powershell
python -m unittest tests.evals.test_fixtures -v
```

- [ ] **Step 3: Implement a complete protocol-shaped recording dealer**

Implement all `DealerAdapter` operations with immutable fixture records. Reads return seeded pages,
vehicles, offers, locations, hours, slots and bookings. Mutations record one named side effect and
honour idempotency. Named variants inject reserved/sold state, stale slots, verification failure,
cancelled bookings, no availability, idempotency conflict and temporary failure at the exact
operation required by the corpus.

- [ ] **Step 4: Implement state builders**

Use one fixed aware clock and stable IDs. Pending actions must contain typed payloads, expiry and
idempotency identity. Presentation groups must preserve ordinals and action references. Verified
fixtures must use explicit unexpired grants; unverified fixtures must not.

- [ ] **Step 5: Run fixture and dealer-protocol tests**

```powershell
python -m unittest tests.evals.test_fixtures tests.webchat.test_dealer -v
```

---

### Task 3: Observation extraction without expectation leakage

**Files:**
- Create: `evals/observe.py`
- Create: `tests/evals/test_observe.py`

**Interfaces:**
- Produces: `observe_turn(turn_result, *, before_state, dealer_snapshot, planning_strategy)`.
- Returns: `ObservedTurn` populated from real commands, calls, state, blocks, side effects and
  usage.

- [ ] **Step 1: Write failing table-driven extraction tests**

Use literal `MessageBlock`, `ConversationState` and dealer snapshots to cover all ten block kinds,
all three notice keys, all side-effect names, deterministic redirects, confirmations, recovery,
selection, pagination and mixed requests. Assert that an unrelated expectation object cannot alter
the observation.

```python
observed = observe_turn(result, before_state=before, dealer_snapshot=dealer.snapshot())
self.assertEqual(observed.answer.block_types, ("notice", "slot_choices"))
self.assertIn("slot_unavailable", observed.answer.facts)
self.assertIn("select_new_slot", observed.answer.next_steps)
```

- [ ] **Step 2: Run extraction tests and witness the missing extractor failure**

```powershell
python -m unittest tests.evals.test_observe -v
```

- [ ] **Step 3: Implement deterministic observation rules**

Derive entity IDs and business facts from typed payload values, state deltas and dealer calls.
Derive next-step labels from inert action types and missing-information codes. Derive forbidden
claims only when output actually contains a prohibited assertion. Do not import or accept
`TurnExpectation` in this module.

- [ ] **Step 4: Add explicit coverage for every corpus vocabulary token**

Compare corpus-required block types, facts, notices and next steps to the extractor's declared
vocabulary. This catches a future corpus label that cannot be observed.

- [ ] **Step 5: Run observation and scorer tests**

```powershell
python -m unittest tests.evals.test_observe tests.evals.test_runner -v
```

---

### Task 4: Scripted conversation driver

**Files:**
- Create: `evals/driver.py`
- Create: `tests/evals/test_driver.py`

**Interfaces:**
- Produces: `ScriptedConversationDriver` implementing `ConversationDriver` and
  `LiveProviderConversationDriver` using the same session mechanics.
- Consumes: `FixtureRegistry`, `HarnessRuntime`, `PlanningEngine`, `observe_turn()`.

- [ ] **Step 1: Write failing driver tests for public-runtime execution**

Prove a free-text turn calls the scripted provider once, an action turn calls it zero times,
sequential turns reuse reduced state, a new scenario resets state/dealer calls, abstract action
references resolve to runtime references, and a fake driver cannot mark a missing answer passed.

- [ ] **Step 2: Run driver tests and witness failure**

```powershell
python -m unittest tests.evals.test_driver -v
```

- [ ] **Step 3: Implement scenario sessions and scripted provider**

The provider queue receives only `turn.scripted_plan`. The driver constructs `TurnRequest` from the
corpus input, current state, page observation and fixed time, calls `HarnessRuntime.handle`, updates
session state, takes the dealer delta, and invokes `observe_turn`.

- [ ] **Step 4: Implement live-provider parity**

The live driver receives a configured `PlanningProvider`; it must not read scripted plans. Add a
test comparing scripted/live fixture, clock and runtime-policy fingerprints and rejecting any
difference beyond provider identity.

- [ ] **Step 5: Run driver tests**

```powershell
python -m unittest tests.evals.test_driver -v
```

---

### Task 5: Full JSON report

**Files:**
- Create: `evals/reporting.py`
- Create: `tests/evals/test_reporting.py`
- Modify: `evals/verify.py`

**Interfaces:**
- Produces: `detailed_report_data(...)`, `write_report_atomic(path, data)`, and canonical JSON for
  all selected suite and corpus results.

- [ ] **Step 1: Write failing serialization tests**

Assert the report contains commit/time/version/lane metadata, suite totals, overall and per-domain
rates, all selected scenarios and turns, inputs, expectations, observed blocks, command arguments,
state deltas, side effects, usage, score and structured divergence. Assert stable key ordering and
that a failed evaluation still serializes.

- [ ] **Step 2: Write failing sanitization and atomic-write tests**

Reject keys or values containing credentials, authorization, cookies, provider envelopes or hidden
reasoning. Write through a sibling temporary file and `replace()` it so interruption cannot leave a
partial report.

- [ ] **Step 3: Run reporting tests and witness failure**

```powershell
python -m unittest tests.evals.test_reporting -v
```

- [ ] **Step 4: Implement reporting and split detailed logic out of `evals/verify.py`**

Keep `verify.py` as CLI/composition. Preserve `--example`. Generated reports remain under
`artifacts/evals/reviewer-report.json` unless `--output` is supplied.

- [ ] **Step 5: Run report and existing verifier tests**

```powershell
python -m unittest tests.evals.test_reporting tests.evals.test_verify -v
```

---

### Task 6: Unified CLI selection and exit semantics

**Files:**
- Modify: `evals/verify.py`
- Modify: `tests/evals/test_verify.py`
- Modify: `README.md`

**Interfaces:**
- Default: authored + platform + full scripted corpus.
- Options: `--only {authored,platform,corpus}`, `--scenario ID`,
  `--lane {scripted,live-provider}`, `--output PATH`, and existing `--example ID`.

- [ ] **Step 1: Write failing CLI tests with injected suite/corpus runners**

Cover default selection, each `--only` value, scenario validation, unknown scenario failure,
explicit live-provider configuration failure, non-zero suite/corpus exit, JSON creation on failure,
and concise stdout counts that distinguish defined scenarios from executed/passed scenarios.

- [ ] **Step 2: Run verifier tests and witness argument/runner failures**

```powershell
python -m unittest tests.evals.test_verify -v
```

- [ ] **Step 3: Implement CLI composition**

Load provider configuration only for `--lane live-provider`. Never silently skip or replace a live
run. For `--scenario`, execute all turns of exactly that scenario and label the report partial.

- [ ] **Step 4: Update reviewer instructions**

Document the default command, focused commands, report path, key-free/live distinction and exit
semantics without embedding run results in tracked prose.

- [ ] **Step 5: Run focused verifier tests**

```powershell
python -m unittest tests.evals.test_verify -v
```

---

### Task 7: Execute corpus, correct divergences, audit and commit

**Files:**
- Modify only the layer proven responsible by first-divergence evidence.
- Update: `execution.md` Phase 7 checkbox only for work actually completed.

**Interfaces:**
- Produces the final key-free reviewer command and gitignored detailed report.

- [ ] **Step 1: Run one focused scenario**

```powershell
python -m evals.verify --only corpus --scenario scope-01
```

Expected: one scenario is executed, every turn is recorded, and the report is marked partial.

- [ ] **Step 2: Run the complete scripted corpus**

```powershell
python -m evals.verify --only corpus
```

For each failure, use the recorded first divergence. Fix state/context/schema/policy/adapter/
recovery/renderer/fixture/scoring at its owning layer; never weaken a correct expectation merely to
obtain green output.

- [ ] **Step 3: Run the unified reviewer command**

```powershell
python -m evals.verify
```

Expected: authored tests, supplied platform tests and all 60 scenarios/74 turns execute; the JSON
contains one record per turn and the process status matches aggregate pass/fail.

- [ ] **Step 4: Perform the mandatory line audit**

Review every changed line against `docs/design/FULL-CORPUS-EVALUATION.md`, verify no supplied
platform/website changes, scan tracked files for secrets/PII/generated artifacts, run
`python -m compileall -q webchat server evals tests`, and run `git diff --check`.

- [ ] **Step 5: Commit focused implementation changes on `main`**

```powershell
git add evals tests README.md execution.md webchat
git commit -m "Execute full conversation corpus"
```
