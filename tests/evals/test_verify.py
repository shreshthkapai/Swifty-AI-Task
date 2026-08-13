from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest

from evals.verify import (
    SuiteResult,
    SuiteSpec,
    build_report,
    load_examples,
    parse_unittest_summary,
    run_suite,
)


class ReviewerVerificationTests(unittest.TestCase):
    def test_committed_examples_cover_assignment_risks_and_preserve_utf8(self) -> None:
        path = Path(__file__).parents[2] / "evals" / "examples.json"

        examples = load_examples(path)

        risks = {item["risk"] for item in examples["examples"]}
        self.assertEqual(
            risks,
            {
                "adjacent-advice",
                "business-truth",
                "scope",
                "stale-state",
                "verification",
            },
        )
        price = next(item for item in examples["examples"] if item["id"] == "price-on-request")
        encoded = json.dumps(price, ensure_ascii=False).encode("utf-8")
        self.assertIn("Price on request".encode(), encoded)

    def test_unittest_summary_parses_success_and_failure_counts(self) -> None:
        successful = parse_unittest_summary(
            "Ran 14 tests in 0.125s\n\nOK (skipped=2)\n",
            return_code=0,
        )
        failed = parse_unittest_summary(
            "FAIL: test_policy (tests.test_policy.PolicyTests.test_policy)\n"
            "Ran 9 tests in 0.500s\n\nFAILED (failures=1, errors=2, skipped=1)\n",
            return_code=1,
        )

        self.assertEqual(
            successful,
            {"tests": 14, "failures": 0, "errors": 0, "skipped": 2},
        )
        self.assertEqual(
            failed,
            {"tests": 9, "failures": 1, "errors": 2, "skipped": 1},
        )

    def test_run_suite_executes_real_unittest_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_example.py").write_text(
                "import unittest\n"
                "class ExampleTests(unittest.TestCase):\n"
                "    def test_real_process(self):\n"
                "        self.assertEqual(2 + 2, 4)\n",
                encoding="utf-8",
            )
            result = run_suite(
                SuiteSpec(
                    name="example",
                    command=(
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        ".",
                        "-p",
                        "test_*.py",
                    ),
                    cwd=root,
                )
            )

        self.assertTrue(result.passed)
        self.assertEqual(result.tests, 1)
        self.assertEqual(result.failures, 0)
        self.assertEqual(result.errors, 0)
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_examples_schema_rejects_secret_or_pii_bearing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "examples.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "lane": "scripted",
                        "fixture": "hermetic-dealer",
                        "evaluated_commit": "abc123",
                        "examples": [
                            {
                                "id": "scope-moon",
                                "title": "Moon redirect",
                                "risk": "scope",
                                "source_test": "tests.test_scope.test_moon",
                                "input": {"current_input": "What size is the moon?"},
                                "actual": {
                                    "model_calls": 0,
                                    "executed_commands": [],
                                    "blocks": [
                                        {
                                            "kind": "text",
                                            "payload": {"text": "Call 07700900123"},
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "sensitive"):
                load_examples(path)

    def test_report_contains_reproducible_suite_corpus_and_example_stats(self) -> None:
        examples = {
            "schema_version": 1,
            "lane": "scripted",
            "fixture": "hermetic-dealer",
            "evaluated_commit": "abc123",
            "examples": [
                {
                    "id": "scope-moon",
                    "title": "Moon redirect",
                    "risk": "scope",
                    "source_test": "tests.test_scope.test_moon",
                    "input": {"current_input": "What size is the moon?"},
                    "actual": {
                        "model_calls": 0,
                        "executed_commands": [],
                        "blocks": [{"kind": "text", "payload": {"text": "Redirect"}}],
                    },
                }
            ],
        }
        suite = SuiteResult(
            name="empty",
            command=(sys.executable, "-m", "unittest", "tests.empty"),
            tests=0,
            failures=0,
            errors=0,
            skipped=0,
            duration_ms=0,
            passed=True,
        )

        report = build_report(
            suites=(suite,),
            examples=examples,
            examples_sha256="0" * 64,
            evaluated_commit="abc123",
            generated_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            python_version="3.12.0",
            corpus_metadata={
                "schema_version": 1,
                "corpus_version": "2026-08-13.v1",
                "fixture_version": "northstar-v1",
                "scoring_version": "1",
                "scenarios": 60,
                "turns": 74,
                "categories": {"workshop": 16},
            },
        )

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["evaluated_commit"], "abc123")
        self.assertEqual(report["totals"]["tests"], 0)
        self.assertEqual(report["examples"]["count"], 1)
        self.assertEqual(report["examples"]["fixture"], "hermetic-dealer")
        self.assertEqual(report["examples"]["by_risk"], {"scope": 1})
        self.assertEqual(
            report["examples"]["cases"],
            [
                {
                    "id": "scope-moon",
                    "risk": "scope",
                    "source_test": "tests.test_scope.test_moon",
                    "model_calls": 0,
                    "executed_commands": [],
                    "block_types": ["text"],
                }
            ],
        )
        self.assertEqual(report["corpus"]["scenarios"], 60)
        self.assertNotIn("output", report["suites"][0])


if __name__ == "__main__":
    unittest.main()
