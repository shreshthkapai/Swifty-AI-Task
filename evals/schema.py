"""Strict schema for the reviewer-visible conversational corpus."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, Mapping

from webchat.harness.contracts import (
    HarnessCommand,
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
    command_from_dict,
)


CORPUS_SCHEMA_VERSION = 1
SCRIPTED_DECISION_SCHEMA_VERSION = 2
CATEGORIES = {
    "vehicle_discovery",
    "sales_test_drive",
    "workshop",
    "dealership_contact",
    "state_reference",
    "scope_resilience",
}
COMMAND_NAMES = {item.value for item in ReadCommandName} | {
    item.value for item in PreparationCommandName
}


class ScriptedScope(StrEnum):
    IN_DOMAIN = "in_domain"
    DEALERSHIP_ADJACENT = "dealership_adjacent"
    MIXED = "mixed"
    OUT_OF_SCOPE = "out_of_scope"


class ResponseStrategy(StrEnum):
    """Eval-only labels used to score the observable response shape."""

    SEARCH_RESULTS = "search_results"
    VEHICLE_DETAILS = "vehicle_details"
    COMPARISON = "comparison"
    AVAILABILITY_RESULT = "availability_result"
    OFFER_RESULTS = "offer_results"
    SLOT_RESULTS = "slot_results"
    WORKSHOP_DETAILS = "workshop_details"
    BOOKING_DETAILS = "booking_details"
    DEALERSHIP_DETAILS = "dealership_details"
    BUSINESS_INFORMATION = "business_information"
    MISSING_INFORMATION = "missing_information"
    ACTION_PREPARED = "action_prepared"
    GENERAL_GUIDANCE = "general_guidance"
    DOMAIN_REDIRECT = "domain_redirect"
    RECOVERY = "recovery"
    ACKNOWLEDGEMENT = "acknowledgement"


@dataclass(frozen=True, slots=True)
class ScriptedDecision:
    """Hermetic provider fixture; never consumed by the production harness."""

    scope: ScriptedScope
    commands: tuple[HarnessCommand, ...]
    response_strategy: ResponseStrategy
    response_mode: str
    clarification_question: str | None = None
    schema_version: int = SCRIPTED_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCRIPTED_DECISION_SCHEMA_VERSION:
            raise ValueError(f"unsupported scripted decision schema version: {self.schema_version}")
        if len(self.commands) > 2:
            raise ValueError("a scripted decision may contain at most two commands")
        if sum(isinstance(command, PreparationCommand) for command in self.commands) > 1:
            raise ValueError("a scripted decision may contain at most one preparation command")
        if not all(isinstance(command, (ReadCommand, PreparationCommand)) for command in self.commands):
            raise ValueError("scripted decision commands must be semantic commands")
        if self.scope is ScriptedScope.OUT_OF_SCOPE and self.commands:
            raise ValueError("an out-of-scope scripted decision cannot contain dealer commands")
        if self.scope is ScriptedScope.OUT_OF_SCOPE and self.response_strategy is not ResponseStrategy.DOMAIN_REDIRECT:
            raise ValueError("an out-of-scope scripted decision must use domain_redirect")
        if self.scope is not ScriptedScope.OUT_OF_SCOPE and self.response_strategy is ResponseStrategy.DOMAIN_REDIRECT:
            raise ValueError("domain_redirect is only valid for out-of-scope decisions")
        if self.response_strategy is ResponseStrategy.MISSING_INFORMATION:
            if not isinstance(self.clarification_question, str) or not self.clarification_question.strip():
                raise ValueError("missing_information requires clarification_question")
        elif self.clarification_question is not None:
            raise ValueError("clarification_question requires missing_information strategy")

    @classmethod
    def from_data(cls, value: object) -> "ScriptedDecision":
        data = _object(value, "scripted decision")
        allowed = {
            "schema_version", "scope", "commands", "response_strategy",
            "response_mode", "clarification_question",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown scripted decision fields: {sorted(unknown)}")
        commands = data.get("commands")
        if not isinstance(commands, list):
            raise ValueError("scripted decision commands must be an array")
        try:
            scope = ScriptedScope(data.get("scope"))
            strategy = ResponseStrategy(data.get("response_strategy"))
        except (TypeError, ValueError) as exc:
            raise ValueError("scripted decision contains an unknown scope or strategy") from exc
        mode = data.get("response_mode")
        if not isinstance(mode, str) or not mode:
            raise ValueError("scripted decision response_mode must be a string")
        version = data.get("schema_version")
        if type(version) is not int:
            raise ValueError("scripted decision schema_version must be an integer")
        clarification = data.get("clarification_question")
        if clarification is not None and not isinstance(clarification, str):
            raise ValueError("clarification_question must be a string")
        return cls(
            scope=scope,
            commands=tuple(command_from_dict(command) for command in commands),
            response_strategy=strategy,
            response_mode=mode,
            clarification_question=clarification,
            schema_version=version,
        )

    def to_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "scope": self.scope.value,
            "commands": [command.to_dict() for command in self.commands],
            "response_strategy": self.response_strategy.value,
            "response_mode": self.response_mode,
        }
        if self.clarification_question is not None:
            result["clarification_question"] = self.clarification_question
        return result


class CorpusValidationError(ValueError):
    """Raised when evaluation data is incomplete, ambiguous, or unsupported."""


def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CorpusValidationError(f"{path} must be an object")
    return value


def _fields(
    data: Mapping[str, Any],
    *,
    path: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = required - set(data)
    unknown = set(data) - required - optional
    if missing:
        raise CorpusValidationError(f"{path} missing fields: {sorted(missing)}")
    if unknown:
        raise CorpusValidationError(f"{path} unknown fields: {sorted(unknown)}")


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorpusValidationError(f"{path} must be a non-empty string")
    return value


def _strings(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CorpusValidationError(f"{path} must be an array")
    result = tuple(_text(item, f"{path}[]") for item in value)
    if len(result) != len(set(result)):
        raise CorpusValidationError(f"{path} contains duplicates")
    return result


def _json_object(value: object, path: str) -> dict[str, Any]:
    data = _object(value, path)
    try:
        return json.loads(json.dumps(data, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise CorpusValidationError(f"{path} must contain JSON values") from exc


@dataclass(frozen=True, slots=True)
class TurnInput:
    kind: str
    value: str
    reference: str | None = None

    @classmethod
    def from_data(cls, value: object, path: str) -> TurnInput:
        data = _object(value, path)
        _fields(
            data,
            path=path,
            required={"kind", "value"},
            optional={"reference"},
        )
        kind = _text(data["kind"], f"{path}.kind")
        if kind not in {"message", "action"}:
            raise CorpusValidationError(f"{path}.kind must be message or action")
        reference = data.get("reference")
        if reference is not None:
            reference = _text(reference, f"{path}.reference")
        if kind == "message" and reference is not None:
            raise CorpusValidationError(f"{path}.reference is only valid for actions")
        return cls(
            kind=kind,
            value=_text(data["value"], f"{path}.value"),
            reference=reference,
        )

    def to_data(self) -> dict[str, Any]:
        result = {"kind": self.kind, "value": self.value}
        if self.reference is not None:
            result["reference"] = self.reference
        return result


@dataclass(frozen=True, slots=True)
class AnswerExpectation:
    strategies: tuple[str, ...]
    required_block_types: tuple[str, ...]
    entity_ids: tuple[str, ...]
    required_facts: tuple[str, ...]
    required_notice_keys: tuple[str, ...]
    required_next_steps: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    exact_text: str | None
    direct: bool

    @classmethod
    def from_data(cls, value: object, path: str) -> AnswerExpectation:
        data = _object(value, path)
        required = {"strategies", "direct"}
        optional = {
            "required_block_types",
            "entity_ids",
            "required_facts",
            "required_notice_keys",
            "required_next_steps",
            "forbidden_claims",
            "exact_text",
        }
        _fields(data, path=path, required=required, optional=optional)
        strategies = _strings(data["strategies"], f"{path}.strategies")
        if not strategies:
            raise CorpusValidationError(f"{path}.strategies cannot be empty")
        unknown_strategies = set(strategies) - {item.value for item in ResponseStrategy}
        if unknown_strategies:
            raise CorpusValidationError(
                f"{path}.strategies contains unknown values: {sorted(unknown_strategies)}"
            )
        exact_text = data.get("exact_text")
        if exact_text is not None:
            exact_text = _text(exact_text, f"{path}.exact_text")
        direct = data["direct"]
        if not isinstance(direct, bool):
            raise CorpusValidationError(f"{path}.direct must be a boolean")
        result = cls(
            strategies=strategies,
            required_block_types=_strings(
                data.get("required_block_types", []), f"{path}.required_block_types"
            ),
            entity_ids=_strings(data.get("entity_ids", []), f"{path}.entity_ids"),
            required_facts=_strings(
                data.get("required_facts", []), f"{path}.required_facts"
            ),
            required_notice_keys=_strings(
                data.get("required_notice_keys", []), f"{path}.required_notice_keys"
            ),
            required_next_steps=_strings(
                data.get("required_next_steps", []), f"{path}.required_next_steps"
            ),
            forbidden_claims=_strings(
                data.get("forbidden_claims", []), f"{path}.forbidden_claims"
            ),
            exact_text=exact_text,
            direct=direct,
        )
        if not (
            result.required_block_types
            or result.required_facts
            or result.required_next_steps
            or result.exact_text
        ):
            raise CorpusValidationError(
                f"{path} must declare observable answer assertions"
            )
        return result

    def to_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "strategies": list(self.strategies),
            "required_block_types": list(self.required_block_types),
            "entity_ids": list(self.entity_ids),
            "required_facts": list(self.required_facts),
            "required_notice_keys": list(self.required_notice_keys),
            "required_next_steps": list(self.required_next_steps),
            "forbidden_claims": list(self.forbidden_claims),
            "direct": self.direct,
        }
        if self.exact_text is not None:
            result["exact_text"] = self.exact_text
        return result


@dataclass(frozen=True, slots=True)
class TurnExpectation:
    required_commands: tuple[str, ...]
    allowed_commands: tuple[str, ...]
    required_calls: tuple[str, ...]
    prohibited_calls: tuple[str, ...]
    prohibited_mutations: tuple[str, ...]
    expected_state: dict[str, Any]
    side_effects: tuple[str, ...]
    max_model_calls: int
    answer: AnswerExpectation

    @classmethod
    def from_data(cls, value: object, path: str) -> TurnExpectation:
        data = _object(value, path)
        required = {"max_model_calls", "answer"}
        optional = {
            "required_commands",
            "allowed_commands",
            "required_calls",
            "prohibited_calls",
            "prohibited_mutations",
            "expected_state",
            "side_effects",
        }
        _fields(data, path=path, required=required, optional=optional)
        required_commands = _strings(
            data.get("required_commands", []), f"{path}.required_commands"
        )
        allowed_commands = _strings(
            data.get("allowed_commands", []), f"{path}.allowed_commands"
        )
        unknown_commands = (set(required_commands) | set(allowed_commands)) - COMMAND_NAMES
        if unknown_commands:
            raise CorpusValidationError(
                f"{path} contains unknown commands: {sorted(unknown_commands)}"
            )
        max_model_calls = data["max_model_calls"]
        if not isinstance(max_model_calls, int) or isinstance(max_model_calls, bool):
            raise CorpusValidationError(f"{path}.max_model_calls must be an integer")
        if max_model_calls not in {0, 1, 2}:
            raise CorpusValidationError(
                f"{path}.max_model_calls must be zero, one, or two"
            )
        return cls(
            required_commands=required_commands,
            allowed_commands=allowed_commands,
            required_calls=_strings(
                data.get("required_calls", []), f"{path}.required_calls"
            ),
            prohibited_calls=_strings(
                data.get("prohibited_calls", []), f"{path}.prohibited_calls"
            ),
            prohibited_mutations=_strings(
                data.get("prohibited_mutations", []), f"{path}.prohibited_mutations"
            ),
            expected_state=_json_object(
                data.get("expected_state", {}), f"{path}.expected_state"
            ),
            side_effects=_strings(
                data.get("side_effects", []), f"{path}.side_effects"
            ),
            max_model_calls=max_model_calls,
            answer=AnswerExpectation.from_data(data["answer"], f"{path}.answer"),
        )

    def to_data(self) -> dict[str, Any]:
        return {
            "required_commands": list(self.required_commands),
            "allowed_commands": list(self.allowed_commands),
            "required_calls": list(self.required_calls),
            "prohibited_calls": list(self.prohibited_calls),
            "prohibited_mutations": list(self.prohibited_mutations),
            "expected_state": self.expected_state,
            "side_effects": list(self.side_effects),
            "max_model_calls": self.max_model_calls,
            "answer": self.answer.to_data(),
        }


@dataclass(frozen=True, slots=True)
class CorpusTurn:
    id: str
    input: TurnInput
    scripted_plan: ScriptedDecision | None
    expectation: TurnExpectation

    @classmethod
    def from_data(cls, value: object, path: str) -> CorpusTurn:
        data = _object(value, path)
        _fields(
            data,
            path=path,
            required={"id", "input", "scripted_plan", "expect"},
        )
        turn_input = TurnInput.from_data(data["input"], f"{path}.input")
        expectation = TurnExpectation.from_data(data["expect"], f"{path}.expect")
        raw_plan = data["scripted_plan"]
        if raw_plan is None:
            plan = None
        else:
            try:
                plan = ScriptedDecision.from_data(raw_plan)
            except ValueError as exc:
                raise CorpusValidationError(f"{path}.scripted_plan: {exc}") from exc
        if turn_input.kind == "action" and plan is not None:
            raise CorpusValidationError(f"{path} action turns cannot have a scripted plan")
        if expectation.max_model_calls == 0 and plan is not None:
            raise CorpusValidationError(f"{path} zero-call turns cannot have a scripted plan")
        if plan is not None:
            planned_names = {command.name.value for command in plan.commands}
            permitted = set(expectation.required_commands) | set(
                expectation.allowed_commands
            )
            if not planned_names.issubset(permitted):
                raise CorpusValidationError(
                    f"{path}.scripted_plan uses commands outside expectations"
                )
            if not set(expectation.required_commands).issubset(planned_names):
                raise CorpusValidationError(
                    f"{path}.scripted_plan omits required commands"
                )
            if plan.response_strategy.value not in expectation.answer.strategies:
                raise CorpusValidationError(
                    f"{path}.scripted_plan strategy is outside answer expectations"
                )
        return cls(
            id=_text(data["id"], f"{path}.id"),
            input=turn_input,
            scripted_plan=plan,
            expectation=expectation,
        )

    def to_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "input": self.input.to_data(),
            "scripted_plan": (
                self.scripted_plan.to_data() if self.scripted_plan is not None else None
            ),
            "expect": self.expectation.to_data(),
        }


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    category: str
    title: str
    tags: tuple[str, ...]
    initial_state_fixture: str
    initial_page: dict[str, Any]
    dealer_fixture: str
    turns: tuple[CorpusTurn, ...]

    @classmethod
    def from_data(cls, value: object, path: str) -> Scenario:
        data = _object(value, path)
        _fields(
            data,
            path=path,
            required={
                "id",
                "category",
                "title",
                "tags",
                "initial",
                "dealer_fixture",
                "turns",
            },
        )
        category = _text(data["category"], f"{path}.category")
        if category not in CATEGORIES:
            raise CorpusValidationError(f"{path}.category is unsupported")
        initial = _object(data["initial"], f"{path}.initial")
        _fields(
            initial,
            path=f"{path}.initial",
            required={"state_fixture", "page"},
        )
        raw_turns = data["turns"]
        if not isinstance(raw_turns, list) or not raw_turns:
            raise CorpusValidationError(f"{path}.turns must be a non-empty array")
        turns = tuple(
            CorpusTurn.from_data(item, f"{path}.turns[{index}]")
            for index, item in enumerate(raw_turns)
        )
        turn_ids = [turn.id for turn in turns]
        if len(turn_ids) != len(set(turn_ids)):
            raise CorpusValidationError(f"{path}.turn ids must be unique")
        return cls(
            id=_text(data["id"], f"{path}.id"),
            category=category,
            title=_text(data["title"], f"{path}.title"),
            tags=_strings(data["tags"], f"{path}.tags"),
            initial_state_fixture=_text(
                initial["state_fixture"], f"{path}.initial.state_fixture"
            ),
            initial_page=_json_object(initial["page"], f"{path}.initial.page"),
            dealer_fixture=_text(
                data["dealer_fixture"], f"{path}.dealer_fixture"
            ),
            turns=turns,
        )

    def to_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "tags": list(self.tags),
            "initial": {
                "state_fixture": self.initial_state_fixture,
                "page": self.initial_page,
            },
            "dealer_fixture": self.dealer_fixture,
            "turns": [turn.to_data() for turn in self.turns],
        }


@dataclass(frozen=True, slots=True)
class Corpus:
    corpus_version: str
    fixture_version: str
    scoring_version: str
    scenarios: tuple[Scenario, ...]
    schema_version: int = CORPUS_SCHEMA_VERSION

    def category_counts(self) -> dict[str, int]:
        counts = Counter(scenario.category for scenario in self.scenarios)
        return dict(sorted(counts.items()))

    def to_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "corpus_version": self.corpus_version,
            "fixture_version": self.fixture_version,
            "scoring_version": self.scoring_version,
            "scenarios": [scenario.to_data() for scenario in self.scenarios],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_data(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def load_corpus(path: str | Path) -> Corpus:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusValidationError(f"cannot load corpus: {exc}") from exc
    data = _object(raw, "corpus")
    _fields(
        data,
        path="corpus",
        required={
            "schema_version",
            "corpus_version",
            "fixture_version",
            "scoring_version",
            "scenarios",
        },
    )
    version = data["schema_version"]
    if type(version) is not int or version != CORPUS_SCHEMA_VERSION:
        raise CorpusValidationError(f"unsupported corpus schema version: {version}")
    raw_scenarios = data["scenarios"]
    if not isinstance(raw_scenarios, list):
        raise CorpusValidationError("corpus.scenarios must be an array")
    scenarios = tuple(
        Scenario.from_data(item, f"corpus.scenarios[{index}]")
        for index, item in enumerate(raw_scenarios)
    )
    scenario_ids = [scenario.id for scenario in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise CorpusValidationError("scenario ids must be unique")
    return Corpus(
        schema_version=version,
        corpus_version=_text(data["corpus_version"], "corpus.corpus_version"),
        fixture_version=_text(data["fixture_version"], "corpus.fixture_version"),
        scoring_version=_text(data["scoring_version"], "corpus.scoring_version"),
        scenarios=scenarios,
    )
