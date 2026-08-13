"""Two-lane conversational evaluator and semantic answer scorer."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import math
from typing import Any, Protocol

from evals.schema import Corpus, CorpusTurn, Scenario, TurnExpectation, TurnInput
from webchat.harness.contracts import HarnessCommand, PreparationCommand, ReadCommand


class FailureCategory(StrEnum):
    MODEL_REASONING = "MODEL_REASONING"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    CONTEXT_MISSING = "CONTEXT_MISSING"
    BAD_TOOL_SCHEMA = "BAD_TOOL_SCHEMA"
    STATE_ERROR = "STATE_ERROR"
    POLICY_MISSING = "POLICY_MISSING"
    ADAPTER_ERROR = "ADAPTER_ERROR"
    AMBIGUITY = "AMBIGUITY"
    ANSWER_QUALITY = "ANSWER_QUALITY"
    RECOVERY_ERROR = "RECOVERY_ERROR"


class LaneKind(StrEnum):
    SCRIPTED = "scripted"
    LIVE_PROVIDER = "live-provider"


@dataclass(frozen=True, slots=True)
class EvaluationLane:
    kind: LaneKind
    corpus_version: str
    fixture_version: str
    scoring_version: str
    clock: datetime
    runtime_policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LaneKind):
            raise ValueError("evaluation lane kind must be a LaneKind")
        if self.clock.tzinfo is None or self.clock.utcoffset() is None:
            raise ValueError("evaluation clock must be timezone-aware")

    def parity_fingerprint(self) -> tuple[str, str, str, str, str]:
        return (
            self.corpus_version,
            self.fixture_version,
            self.scoring_version,
            self.clock.isoformat(),
            self.runtime_policy_version,
        )


def assert_lane_parity(first: EvaluationLane, second: EvaluationLane) -> None:
    if {first.kind, second.kind} != {LaneKind.SCRIPTED, LaneKind.LIVE_PROVIDER}:
        raise ValueError("lane parity requires one scripted and one live-provider lane")
    if first.parity_fingerprint() != second.parity_fingerprint():
        raise ValueError("lane parity inputs differ beyond the planning source")


@dataclass(frozen=True, slots=True)
class ObservedAnswer:
    strategy: str = ""
    block_types: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    notice_keys: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    text: str = ""
    direct: bool = False


@dataclass(frozen=True, slots=True)
class ObservedTurn:
    commands: tuple[HarnessCommand, ...] = ()
    external_calls: tuple[str, ...] = ()
    mutations: tuple[str, ...] = ()
    state: dict[str, Any] | None = None
    side_effects: tuple[str, ...] = ()
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    answer: ObservedAnswer | None = None
    provider_failure: str | None = None
    adapter_failure: str | None = None
    blocks: tuple[dict[str, Any], ...] = ()
    state_changes: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.commands, tuple) or not all(
            isinstance(command, (ReadCommand, PreparationCommand))
            for command in self.commands
        ):
            raise ValueError("observed commands must be a tuple of HarnessCommand values")
        for field_name in ("model_calls", "input_tokens", "output_tokens"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class Divergence:
    category: FailureCategory
    path: str
    expected: Any
    actual: Any


@dataclass(frozen=True, slots=True)
class TurnScore:
    scenario_id: str
    turn_id: str
    passed: bool
    divergence: Divergence | None = None
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    corpus_version: str
    scoring_version: str
    results: tuple[TurnScore, ...]

    @property
    def total_turns(self) -> int:
        return len(self.results)

    @property
    def passed_turns(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def failed_turns(self) -> int:
        return self.total_turns - self.passed_turns

    @property
    def total_model_calls(self) -> int:
        return sum(result.model_calls for result in self.results)

    @property
    def total_input_tokens(self) -> int:
        return sum(result.input_tokens for result in self.results)

    @property
    def total_output_tokens(self) -> int:
        return sum(result.output_tokens for result in self.results)

    @property
    def total_latency_ms(self) -> float:
        return sum(result.latency_ms for result in self.results)

    def failure_counts(self) -> dict[str, int]:
        counts = Counter(
            result.divergence.category.value
            for result in self.results
            if result.divergence is not None
        )
        return dict(sorted(counts.items()))


@dataclass(frozen=True, slots=True)
class EvaluatedTurn:
    scenario_id: str
    category: str
    title: str
    input: TurnInput
    expectation: TurnExpectation
    observed: ObservedTurn
    score: TurnScore


@dataclass(frozen=True, slots=True)
class DetailedEvaluationReport:
    corpus_version: str
    fixture_version: str
    scoring_version: str
    lane: LaneKind
    records: tuple[EvaluatedTurn, ...]

    @property
    def total_turns(self) -> int:
        return len(self.records)

    @property
    def passed_turns(self) -> int:
        return sum(record.score.passed for record in self.records)

    @property
    def failed_turns(self) -> int:
        return self.total_turns - self.passed_turns

    def category_counts(self) -> dict[str, dict[str, int]]:
        categories: dict[str, dict[str, int]] = {}
        for record in self.records:
            counts = categories.setdefault(record.category, {"passed": 0, "failed": 0})
            counts["passed" if record.score.passed else "failed"] += 1
        return dict(sorted(categories.items()))


class ConversationDriver(Protocol):
    async def execute_turn(
        self,
        scenario: Scenario,
        turn: CorpusTurn,
    ) -> ObservedTurn: ...


def _failed(
    scenario_id: str,
    turn_id: str,
    category: FailureCategory,
    path: str,
    expected: Any,
    actual: Any,
) -> TurnScore:
    return TurnScore(
        scenario_id=scenario_id,
        turn_id=turn_id,
        passed=False,
        divergence=Divergence(
            category=category,
            path=path,
            expected=expected,
            actual=actual,
        ),
    )


def _first_state_difference(
    expected: dict[str, Any],
    actual: dict[str, Any],
    path: str = "state",
) -> tuple[str, Any, Any] | None:
    for key in sorted(expected):
        expected_value = expected[key]
        key_path = f"{path}.{key}"
        if key not in actual:
            return key_path, expected_value, None
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict):
                return key_path, expected_value, actual_value
            nested = _first_state_difference(expected_value, actual_value, key_path)
            if nested is not None:
                return nested
        elif expected_value != actual_value:
            return key_path, expected_value, actual_value
    return None


def _missing(required: tuple[str, ...], actual: tuple[str, ...]) -> list[str]:
    return sorted(set(required) - set(actual))


_CASE_INSENSITIVE_ARGUMENTS = frozenset(
    {
        "availability",
        "body_style",
        "condition",
        "currency",
        "department",
        "enquiry_type",
        "fuel_type",
        "make",
        "model",
        "refinement",
        "sort",
        "transmission",
    }
)


def _arguments_satisfy(
    name: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> bool:
    del name
    for field, expected_value in expected.items():
        if expected_value is None:
            continue
        if field not in actual or actual[field] is None:
            return False
        actual_value = actual[field]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict) or not _arguments_satisfy(
                field,
                expected_value,
                actual_value,
            ):
                return False
            continue
        if (
            field in _CASE_INSENSITIVE_ARGUMENTS
            and isinstance(expected_value, str)
            and isinstance(actual_value, str)
        ):
            if expected_value.casefold() != actual_value.casefold():
                return False
            continue
        if expected_value != actual_value:
            return False
    return True


def _score_turn(
    scenario_id: str,
    turn: CorpusTurn,
    observed: ObservedTurn,
) -> TurnScore:
    expectation = turn.expectation
    if observed.provider_failure is not None:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.PROVIDER_FAILURE,
            "provider",
            None,
            observed.provider_failure,
        )
    if observed.adapter_failure is not None:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.ADAPTER_ERROR,
            "adapter",
            None,
            observed.adapter_failure,
        )
    if observed.answer is None:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.ANSWER_QUALITY,
            "answer.missing",
            "customer-visible answer",
            None,
        )
    if observed.model_calls > expectation.max_model_calls:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.MODEL_REASONING,
            "model_calls",
            expectation.max_model_calls,
            observed.model_calls,
        )
    observed_command_names = tuple(command.name.value for command in observed.commands)
    missing_commands = _missing(expectation.required_commands, observed_command_names)
    if missing_commands:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.MODEL_REASONING,
            "commands.required",
            list(expectation.required_commands),
            list(observed_command_names),
        )
    permitted_commands = set(expectation.required_commands) | set(
        expectation.allowed_commands
    )
    unexpected_commands = sorted(set(observed_command_names) - permitted_commands)
    if unexpected_commands:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.MODEL_REASONING,
            "commands.unexpected",
            sorted(permitted_commands),
            list(observed_command_names),
        )
    if turn.scripted_plan is not None:
        for index, expected_command in enumerate(turn.scripted_plan.commands):
            matching = [
                command
                for command in observed.commands
                if command.name is expected_command.name
            ]
            if matching and not any(
                _arguments_satisfy(
                    expected_command.name.value,
                    expected_command.to_dict()["arguments"],
                    command.to_dict()["arguments"],
                )
                for command in matching
            ):
                return _failed(
                    scenario_id,
                    turn.id,
                    FailureCategory.MODEL_REASONING,
                    f"commands[{index}].arguments",
                    expected_command.to_dict()["arguments"],
                    matching[0].to_dict()["arguments"],
                )
    missing_calls = _missing(expectation.required_calls, observed.external_calls)
    if missing_calls:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.ADAPTER_ERROR,
            "calls.required",
            list(expectation.required_calls),
            list(observed.external_calls),
        )
    prohibited_calls = set(expectation.prohibited_calls)
    offending_calls = (
        sorted(observed.external_calls)
        if "*" in prohibited_calls
        else sorted(prohibited_calls & set(observed.external_calls))
    )
    if offending_calls:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.POLICY_MISSING,
            "calls.prohibited",
            list(expectation.prohibited_calls),
            list(observed.external_calls),
        )
    offending_mutations = sorted(
        set(expectation.prohibited_mutations) & set(observed.mutations)
    )
    if offending_mutations:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.POLICY_MISSING,
            "mutations.prohibited",
            list(expectation.prohibited_mutations),
            list(observed.mutations),
        )
    state_difference = _first_state_difference(
        expectation.expected_state,
        observed.state or {},
    )
    if state_difference is not None:
        path, expected, actual = state_difference
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.STATE_ERROR,
            path,
            expected,
            actual,
        )
    if tuple(observed.side_effects) != expectation.side_effects:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.STATE_ERROR,
            "side_effects",
            list(expectation.side_effects),
            list(observed.side_effects),
        )

    answer = observed.answer
    answer_expectation = expectation.answer
    if answer.strategy not in answer_expectation.strategies:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.ANSWER_QUALITY,
            "answer.strategy",
            list(answer_expectation.strategies),
            answer.strategy,
        )
    semantic_checks = (
        (
            "answer.block_types",
            answer_expectation.required_block_types,
            answer.block_types,
        ),
        ("answer.entity_ids", answer_expectation.entity_ids, answer.entity_ids),
        ("answer.facts", answer_expectation.required_facts, answer.facts),
        (
            "answer.notice_keys",
            answer_expectation.required_notice_keys,
            answer.notice_keys,
        ),
        (
            "answer.next_steps",
            answer_expectation.required_next_steps,
            answer.next_steps,
        ),
    )
    for path, required, actual in semantic_checks:
        if _missing(required, actual):
            return _failed(
                scenario_id,
                turn.id,
                FailureCategory.ANSWER_QUALITY,
                path,
                list(required),
                list(actual),
            )
    forbidden_claims = sorted(
        set(answer_expectation.forbidden_claims) & set(answer.claims)
    )
    if forbidden_claims:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.ANSWER_QUALITY,
            "answer.forbidden_claims",
            [],
            forbidden_claims,
        )
    if answer_expectation.exact_text is not None:
        if answer.text != answer_expectation.exact_text:
            return _failed(
                scenario_id,
                turn.id,
                FailureCategory.ANSWER_QUALITY,
                "answer.text",
                answer_expectation.exact_text,
                answer.text,
            )
    if answer_expectation.direct and not answer.direct:
        return _failed(
            scenario_id,
            turn.id,
            FailureCategory.ANSWER_QUALITY,
            "answer.direct",
            True,
            False,
        )
    return TurnScore(scenario_id=scenario_id, turn_id=turn.id, passed=True)


def score_turn(
    scenario_id: str,
    turn: CorpusTurn,
    observed: ObservedTurn,
) -> TurnScore:
    score = _score_turn(scenario_id, turn, observed)
    return replace(
        score,
        model_calls=observed.model_calls,
        input_tokens=observed.input_tokens,
        output_tokens=observed.output_tokens,
        latency_ms=float(observed.latency_ms),
    )


async def run_evaluation(
    corpus: Corpus,
    driver: ConversationDriver,
) -> EvaluationReport:
    results: list[TurnScore] = []
    for scenario in corpus.scenarios:
        for turn in scenario.turns:
            observed = await driver.execute_turn(scenario, turn)
            results.append(score_turn(scenario.id, turn, observed))
    return EvaluationReport(
        corpus_version=corpus.corpus_version,
        scoring_version=corpus.scoring_version,
        results=tuple(results),
    )


async def run_detailed_evaluation(
    corpus: Corpus,
    driver: ConversationDriver,
    *,
    lane: LaneKind,
) -> DetailedEvaluationReport:
    if not isinstance(lane, LaneKind):
        raise ValueError("lane must be a LaneKind")
    records: list[EvaluatedTurn] = []
    for scenario in corpus.scenarios:
        for turn in scenario.turns:
            observed = await driver.execute_turn(scenario, turn)
            records.append(
                EvaluatedTurn(
                    scenario_id=scenario.id,
                    category=scenario.category,
                    title=scenario.title,
                    input=turn.input,
                    expectation=turn.expectation,
                    observed=observed,
                    score=score_turn(scenario.id, turn, observed),
                )
            )
    return DetailedEvaluationReport(
        corpus_version=corpus.corpus_version,
        fixture_version=corpus.fixture_version,
        scoring_version=corpus.scoring_version,
        lane=lane,
        records=tuple(records),
    )
