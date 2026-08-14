"""Reviewer-facing verification command and sanitized JSON report."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

import httpx

from evals.driver import ProviderConversationDriver, ScriptedConversationDriver
from evals.fixtures import FixtureRegistry
from evals.reporting import detailed_report_data, write_report_atomic
from evals.run import DetailedEvaluationReport, LaneKind, run_detailed_evaluation
from evals.schema import Corpus, load_corpus
from webchat.providers.openai import (
    OpenAIGroundedResponseProvider,
    OpenAIPlanningProvider,
    OpenAIProviderConfig,
)


REPORT_SCHEMA_VERSION = 1
EXAMPLE_SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_PATH = ROOT / "evals" / "corpus.json"
DEFAULT_EXAMPLES_PATH = ROOT / "evals" / "examples.json"
DEFAULT_REPORT_PATH = ROOT / "artifacts" / "evals" / "reviewer-report.json"
EVALUATION_CLOCK = datetime(2026, 8, 13, 12, tzinfo=UTC)

_RAN_PATTERN = re.compile(r"Ran (\d+) tests? in [0-9.]+s")
_COUNT_PATTERN = re.compile(r"(failures|errors|skipped)=(\d+)")
_NODE_COUNT_PATTERN = re.compile(
    r"^[^A-Za-z0-9]*?(tests|pass|fail|skipped)\s+(\d+)\s*$",
    re.MULTILINE,
)
_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "cookie", "password", "secret", "token")
_SECRET_VALUE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.IGNORECASE)
_PII_VALUE = re.compile(
    r"(?:\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\b(?:\+44\s?\d{10}|0\d{10})\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SuiteSpec:
    name: str
    command: tuple[str, ...]
    cwd: Path
    parser: str = "unittest"


@dataclass(frozen=True, slots=True)
class SuiteResult:
    name: str
    command: tuple[str, ...]
    tests: int
    failures: int
    errors: int
    skipped: int
    duration_ms: float
    passed: bool
    failed_tests: tuple[str, ...] = ()

    def to_data(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "tests": self.tests,
            "failures": self.failures,
            "errors": self.errors,
            "skipped": self.skipped,
            "duration_ms": round(self.duration_ms, 3),
            "passed": self.passed,
            "failed_tests": list(self.failed_tests),
        }


def parse_unittest_summary(output: str, *, return_code: int) -> dict[str, int]:
    ran = _RAN_PATTERN.search(output)
    if ran is None:
        raise ValueError("unittest output did not contain a run summary")
    counts = {"tests": int(ran.group(1)), "failures": 0, "errors": 0, "skipped": 0}
    for name, value in _COUNT_PATTERN.findall(output):
        counts[name] = int(value)
    if return_code != 0 and counts["failures"] == counts["errors"] == 0:
        raise ValueError("failed unittest process did not report failures or errors")
    return counts


def parse_node_test_summary(output: str, *, return_code: int) -> dict[str, int]:
    values = {name: int(value) for name, value in _NODE_COUNT_PATTERN.findall(output)}
    if "tests" not in values or "fail" not in values:
        raise ValueError("node test output did not contain a run summary")
    if return_code != 0 and values["fail"] == 0:
        raise ValueError("failed node test process did not report failures")
    return {
        "tests": values["tests"],
        "failures": values["fail"],
        "errors": 0,
        "skipped": values.get("skipped", 0),
    }


def _failed_test_names(output: str) -> tuple[str, ...]:
    names = []
    for line in output.splitlines():
        match = re.match(r"^(?:FAIL|ERROR):\s+([^\s(]+)", line)
        if match is not None:
            names.append(match.group(1))
    return tuple(dict.fromkeys(names))


def run_suite(spec: SuiteSpec) -> SuiteResult:
    started = time.perf_counter()
    completed = subprocess.run(
        spec.command,
        cwd=spec.cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    duration_ms = (time.perf_counter() - started) * 1_000
    output = f"{completed.stdout}\n{completed.stderr}"
    if spec.parser == "unittest":
        counts = parse_unittest_summary(output, return_code=completed.returncode)
    elif spec.parser == "node":
        counts = parse_node_test_summary(output, return_code=completed.returncode)
    else:
        raise ValueError(f"unknown suite parser: {spec.parser}")
    return SuiteResult(
        name=spec.name,
        command=spec.command,
        duration_ms=duration_ms,
        passed=completed.returncode == 0,
        failed_tests=_failed_test_names(output),
        **counts,
    )


def _reject_sensitive(value: object, path: str = "examples") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise ValueError(f"{path}.{key} contains sensitive data")
            _reject_sensitive(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive(item, f"{path}[{index}]")
    elif isinstance(value, str) and (_SECRET_VALUE.search(value) or _PII_VALUE.search(value)):
        raise ValueError(f"{path} contains sensitive data")


def load_examples(path: str | Path = DEFAULT_EXAMPLES_PATH) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load reviewer examples: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("reviewer examples must be an object")
    if set(value) != {"schema_version", "lane", "fixture", "evaluated_commit", "examples"}:
        raise ValueError("reviewer examples contain missing or unknown fields")
    if value["schema_version"] != EXAMPLE_SCHEMA_VERSION:
        raise ValueError("unsupported reviewer example schema version")
    if value["lane"] != "scripted":
        raise ValueError("committed reviewer examples must use the scripted lane")
    if value["fixture"] != "hermetic-dealer":
        raise ValueError("committed reviewer examples must name the hermetic dealer fixture")
    if not isinstance(value["evaluated_commit"], str) or not value["evaluated_commit"]:
        raise ValueError("reviewer examples require an evaluated commit")
    examples = value["examples"]
    if not isinstance(examples, list) or not examples:
        raise ValueError("reviewer examples must contain at least one example")
    required = {"id", "title", "risk", "source_test", "input", "actual"}
    identifiers: list[str] = []
    for index, example in enumerate(examples):
        if not isinstance(example, dict) or set(example) != required:
            raise ValueError(f"examples[{index}] contains missing or unknown fields")
        for name in ("id", "title", "risk", "source_test"):
            if not isinstance(example[name], str) or not example[name].strip():
                raise ValueError(f"examples[{index}].{name} must be non-empty text")
        if not isinstance(example["input"], dict) or not isinstance(example["actual"], dict):
            raise ValueError(f"examples[{index}] input and actual must be objects")
        actual = example["actual"]
        if set(actual) != {"model_calls", "executed_commands", "blocks"}:
            raise ValueError(f"examples[{index}].actual contains missing or unknown fields")
        if type(actual["model_calls"]) is not int or actual["model_calls"] < 0:
            raise ValueError(f"examples[{index}].actual.model_calls must be non-negative")
        if not isinstance(actual["executed_commands"], list) or not isinstance(
            actual["blocks"], list
        ):
            raise ValueError(f"examples[{index}].actual commands and blocks must be arrays")
        identifiers.append(example["id"])
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("reviewer example IDs must be unique")
    _reject_sensitive(value)
    return value


def corpus_metadata(corpus: Corpus) -> dict[str, Any]:
    return {
        "schema_version": corpus.schema_version,
        "corpus_version": corpus.corpus_version,
        "fixture_version": corpus.fixture_version,
        "scoring_version": corpus.scoring_version,
        "scenarios": len(corpus.scenarios),
        "turns": sum(len(scenario.turns) for scenario in corpus.scenarios),
        "categories": corpus.category_counts(),
    }


def build_report(
    *,
    suites: Sequence[SuiteResult],
    examples: Mapping[str, Any],
    examples_sha256: str,
    evaluated_commit: str,
    generated_at: datetime,
    python_version: str,
    corpus_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    suite_data = [suite.to_data() for suite in suites]
    example_items = examples["examples"]
    risk_counts = Counter(item["risk"] for item in example_items)
    example_cases = [
        {
            "id": item["id"],
            "risk": item["risk"],
            "source_test": item["source_test"],
            "model_calls": item["actual"]["model_calls"],
            "executed_commands": item["actual"]["executed_commands"],
            "block_types": [block["kind"] for block in item["actual"]["blocks"]],
        }
        for item in example_items
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "evaluated_commit": evaluated_commit,
        "python_version": python_version,
        "corpus": dict(corpus_metadata),
        "suites": suite_data,
        "totals": {
            "tests": sum(item.tests for item in suites),
            "failures": sum(item.failures for item in suites),
            "errors": sum(item.errors for item in suites),
            "skipped": sum(item.skipped for item in suites),
            "duration_ms": round(sum(item.duration_ms for item in suites), 3),
            "passed": all(item.passed for item in suites),
        },
        "examples": {
            "path": "evals/examples.json",
            "sha256": examples_sha256,
            "lane": examples["lane"],
            "fixture": examples["fixture"],
            "evaluated_commit": examples["evaluated_commit"],
            "count": len(example_items),
            "by_risk": dict(sorted(risk_counts.items())),
            "cases": example_cases,
        },
    }


def _git_commit() -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _suite_specs() -> tuple[SuiteSpec, ...]:
    return (
        SuiteSpec(
            "authored",
            (
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-t",
                ".",
                "-p",
                "test_*.py",
            ),
            ROOT,
        ),
        SuiteSpec(
            "supplied-platform",
            (
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
            ),
            ROOT / "dealership-platform",
        ),
        SuiteSpec(
            "web-ui",
            (
                "node",
                "--test",
                "tests/api.test.js",
                "tests/render.test.js",
                "tests/webchat.test.js",
            ),
            ROOT / "dealership-website",
            parser="node",
        ),
    )


def _select_corpus(corpus: Corpus, scenario_id: str | None) -> Corpus:
    if scenario_id is None:
        return corpus
    scenarios = tuple(item for item in corpus.scenarios if item.id == scenario_id)
    if not scenarios:
        raise ValueError(f"unknown corpus scenario: {scenario_id}")
    return Corpus(
        corpus_version=corpus.corpus_version,
        fixture_version=corpus.fixture_version,
        scoring_version=corpus.scoring_version,
        scenarios=scenarios,
        schema_version=corpus.schema_version,
    )


def _local_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


async def _run_corpus(corpus: Corpus, lane: LaneKind) -> DetailedEvaluationReport:
    registry = FixtureRegistry(now=EVALUATION_CLOCK)
    if lane is LaneKind.SCRIPTED:
        return await run_detailed_evaluation(
            corpus,
            ScriptedConversationDriver(registry),
            lane=lane,
        )

    values = _local_environment()
    api_key = values.get("OPENAI_API_KEY", "").strip()
    response_model = values.get("CHAT_MODEL", "").strip()
    planner_model = values.get("CHAT_PLANNER_MODEL", response_model).strip()
    if not api_key or not response_model or not planner_model:
        raise ValueError(
            "live-provider lane requires OPENAI_API_KEY and CHAT_MODEL in the environment or .env"
        )
    async with httpx.AsyncClient() as client:
        planning_provider_config = OpenAIProviderConfig(
            api_key=api_key,
            model=planner_model,
            base_url=values.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=float(values.get("CHAT_PROVIDER_TIMEOUT_SECONDS", "30")),
        )
        response_provider_config = OpenAIProviderConfig(
            api_key=api_key,
            model=response_model,
            base_url=values.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=float(values.get("CHAT_PROVIDER_TIMEOUT_SECONDS", "30")),
        )
        provider = OpenAIPlanningProvider(client, planning_provider_config)
        grounded_response = OpenAIGroundedResponseProvider(
            client,
            response_provider_config,
        )
        driver = ProviderConversationDriver(
            registry,
            lambda: provider,
            lambda: grounded_response,
        )
        return await run_detailed_evaluation(corpus, driver, lane=lane)


def _empty_evaluation(corpus: Corpus, lane: LaneKind) -> DetailedEvaluationReport:
    return DetailedEvaluationReport(
        corpus_version=corpus.corpus_version,
        fixture_version=corpus.fixture_version,
        scoring_version=corpus.scoring_version,
        lane=lane,
        records=(),
    )


def _print_summary(report: Mapping[str, Any], output_path: Path) -> None:
    if "corpus_run" in report:
        _print_detailed_summary(report, output_path)
        return
    print("Northstar reviewer verification")
    for suite in report["suites"]:
        status = "PASS" if suite["passed"] else "FAIL"
        print(
            f"{status:4} {suite['name']}: {suite['tests']} tests, "
            f"{suite['failures']} failures, {suite['errors']} errors, "
            f"{suite['duration_ms'] / 1000:.2f}s"
        )
    totals = report["totals"]
    print(
        f"TOTAL {totals['tests']} tests; {report['corpus']['scenarios']}-scenario corpus; "
        f"{report['examples']['count']} executed reviewer examples"
    )
    print(f"JSON  {output_path.relative_to(ROOT)}")


def _print_detailed_summary(report: Mapping[str, Any], output_path: Path) -> None:
    print("Northstar reviewer verification")
    for suite in report["suites"]:
        status = "PASS" if suite["passed"] else "FAIL"
        print(
            f"{status:4} {suite['name']}: {suite['tests']} tests, "
            f"{suite['failures']} failures, {suite['errors']} errors, "
            f"{suite['duration_ms'] / 1000:.2f}s"
        )
    corpus = report["corpus_run"]
    if corpus["executed_scenarios"]:
        status = "PASS" if corpus["failed_turns"] == 0 else "FAIL"
        print(
            f"{status:4} corpus ({corpus['lane']}): "
            f"{corpus['executed_scenarios']}/{corpus['defined_scenarios']} scenarios, "
            f"{corpus['passed_turns']}/{corpus['executed_turns']} turns"
        )
    tests = sum(item["tests"] for item in report["suites"])
    print(
        f"TOTAL {tests} tests; {corpus['executed_scenarios']}/"
        f"{corpus['defined_scenarios']} scenarios; "
        f"{corpus['executed_turns']} evaluated turns"
    )
    try:
        shown_path = output_path.relative_to(ROOT)
    except ValueError:
        shown_path = output_path
    print(f"JSON  {shown_path}")


def _print_example(examples: Mapping[str, Any], example_id: str) -> int:
    match = next((item for item in examples["examples"] if item["id"] == example_id), None)
    if match is None:
        available = ", ".join(item["id"] for item in examples["examples"])
        print(f"Unknown example {example_id!r}. Available: {available}", file=sys.stderr)
        return 2
    print(json.dumps(match, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--example", metavar="ID")
    parser.add_argument("--only", choices=("authored", "platform", "ui", "corpus"))
    parser.add_argument("--scenario", metavar="ID")
    parser.add_argument(
        "--lane",
        choices=tuple(item.value for item in LaneKind),
        default=LaneKind.SCRIPTED.value,
    )
    args = parser.parse_args(argv)

    if args.example:
        return _print_example(load_examples(DEFAULT_EXAMPLES_PATH), args.example)

    full_corpus = load_corpus(DEFAULT_CORPUS_PATH)
    lane = LaneKind(args.lane)
    run_corpus = args.only in {None, "corpus"}
    if args.scenario and not run_corpus:
        print("--scenario requires the corpus lane", file=sys.stderr)
        return 2
    try:
        selected_corpus = _select_corpus(full_corpus, args.scenario)
        evaluation = (
            asyncio.run(_run_corpus(selected_corpus, lane))
            if run_corpus
            else _empty_evaluation(full_corpus, lane)
        )
    except (OSError, ValueError) as exc:
        print(f"Cannot run corpus: {exc}", file=sys.stderr)
        return 2

    selected_suite_names = {
        None: {"authored", "supplied-platform", "web-ui"},
        "authored": {"authored"},
        "platform": {"supplied-platform"},
        "ui": {"web-ui"},
        "corpus": set(),
    }[args.only]
    suites = tuple(
        run_suite(spec) for spec in _suite_specs() if spec.name in selected_suite_names
    )
    report = detailed_report_data(
        evaluation=evaluation,
        suites=suites,
        evaluated_commit=_git_commit(),
        generated_at=datetime.now(UTC),
        python_version=platform.python_version(),
        total_scenarios=len(full_corpus.scenarios),
        partial=args.scenario is not None or args.only in {"authored", "platform", "ui"},
    )
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    write_report_atomic(output_path, report)
    _print_summary(report, output_path)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
