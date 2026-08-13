"""Canonical, sanitized serialization for exhaustive evaluation evidence."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from evals.run import DetailedEvaluationReport, EvaluatedTurn


REPORT_SCHEMA_VERSION = 2
_REDACTED = "[REDACTED]"
_PII_KEYS = frozenset({
    "email", "phone", "first_name", "last_name", "registration",
})
_FORBIDDEN_KEY_PARTS = (
    "api_key", "authorization", "cookie", "password", "secret", "access_token",
    "reasoning", "encrypted_content", "response_id",
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"\b(?:\+44\s?\d{10}|0\d{10})\b")
_LABELLED_PHONE = re.compile(r"(?i)(phone\s+)(?:\+?\d[\d ]{2,})")
_SECRET_VALUE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.IGNORECASE)


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key is not None and any(part in key.casefold() for part in _FORBIDDEN_KEY_PARTS):
        raise ValueError(f"report contains forbidden sensitive key: {key}")
    if key is not None and key.casefold() in _PII_KEYS and value is not None:
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(item_key): _sanitize(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        if _SECRET_VALUE.search(value):
            raise ValueError("report contains a credential-like value")
        cleaned = _EMAIL.sub(_REDACTED, value)
        cleaned = _PHONE.sub(_REDACTED, cleaned)
        return _LABELLED_PHONE.sub(lambda match: f"{match.group(1)}{_REDACTED}", cleaned)
    return value


def _answer_data(record: EvaluatedTurn) -> dict[str, Any] | None:
    answer = record.observed.answer
    if answer is None:
        return None
    return {
        "strategy": answer.strategy,
        "block_types": list(answer.block_types),
        "entity_ids": list(answer.entity_ids),
        "facts": list(answer.facts),
        "notice_keys": list(answer.notice_keys),
        "next_steps": list(answer.next_steps),
        "claims": list(answer.claims),
        "text": answer.text,
        "direct": answer.direct,
    }


def _turn_data(record: EvaluatedTurn) -> dict[str, Any]:
    observed = record.observed
    divergence = record.score.divergence
    return {
        "scenario_id": record.scenario_id,
        "category": record.category,
        "title": record.title,
        "input": record.input.to_data(),
        "expectation": record.expectation.to_data(),
        "actual": {
            "commands": [command.to_dict() for command in observed.commands],
            "external_calls": list(observed.external_calls),
            "mutations": list(observed.mutations),
            "state": observed.state,
            "state_changes": observed.state_changes or {},
            "side_effects": list(observed.side_effects),
            "model_calls": observed.model_calls,
            "input_tokens": observed.input_tokens,
            "output_tokens": observed.output_tokens,
            "latency_ms": observed.latency_ms,
            "blocks": list(observed.blocks),
            "answer": _answer_data(record),
            "provider_failure": observed.provider_failure,
            "adapter_failure": observed.adapter_failure,
        },
        "score": {
            "passed": record.score.passed,
            "first_divergence": None if divergence is None else {
                "category": divergence.category.value,
                "path": divergence.path,
                "expected": divergence.expected,
                "actual": divergence.actual,
            },
        },
    }


def detailed_report_data(
    *,
    evaluation: DetailedEvaluationReport,
    suites: Sequence[Any],
    evaluated_commit: str,
    generated_at: datetime,
    python_version: str,
    total_scenarios: int,
    partial: bool,
) -> dict[str, Any]:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    scenarios = tuple(dict.fromkeys(record.scenario_id for record in evaluation.records))
    failures: dict[str, int] = {}
    for record in evaluation.records:
        divergence = record.score.divergence
        if divergence is not None:
            key = divergence.category.value
            failures[key] = failures.get(key, 0) + 1
    suite_data = [suite.to_data() for suite in suites]
    category_counts = evaluation.category_counts()
    suite_totals = {
        "tests": sum(suite.tests for suite in suites),
        "failures": sum(suite.failures for suite in suites),
        "errors": sum(suite.errors for suite in suites),
        "skipped": sum(suite.skipped for suite in suites),
        "duration_ms": round(sum(suite.duration_ms for suite in suites), 3),
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "evaluated_commit": evaluated_commit,
        "python_version": python_version,
        "suites": suite_data,
        "suite_totals": suite_totals,
        "corpus_run": {
            "lane": evaluation.lane.value,
            "partial": partial,
            "versions": {
                "corpus": evaluation.corpus_version,
                "fixtures": evaluation.fixture_version,
                "scoring": evaluation.scoring_version,
                "runtime_policy": "harness-v1",
            },
            "defined_scenarios": total_scenarios,
            "executed_scenarios": len(scenarios),
            "executed_turns": evaluation.total_turns,
            "passed_turns": evaluation.passed_turns,
            "failed_turns": evaluation.failed_turns,
            "success_rate": (
                round(evaluation.passed_turns / evaluation.total_turns, 6)
                if evaluation.total_turns
                else None
            ),
            "metrics": {
                "model_calls": sum(record.score.model_calls for record in evaluation.records),
                "input_tokens": sum(record.score.input_tokens for record in evaluation.records),
                "output_tokens": sum(record.score.output_tokens for record in evaluation.records),
                "latency_ms": round(
                    sum(record.score.latency_ms for record in evaluation.records), 3
                ),
            },
            "by_category": category_counts,
            "success_rate_by_category": {
                category: round(counts["passed"] / sum(counts.values()), 6)
                for category, counts in category_counts.items()
            },
            "failure_categories": dict(sorted(failures.items())),
            "turns": [_turn_data(record) for record in evaluation.records],
        },
        "passed": all(suite.passed for suite in suites) and evaluation.failed_turns == 0,
    }
    return _sanitize(report)


def write_report_atomic(path: str | Path, data: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
