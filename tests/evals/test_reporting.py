from datetime import UTC, datetime
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from evals.driver import ScriptedConversationDriver
from evals.fixtures import FixtureRegistry
from evals.reporting import detailed_report_data, write_report_atomic
from evals.run import LaneKind, run_detailed_evaluation
from evals.schema import Corpus, load_corpus


NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class DetailedReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_contains_every_selected_turn_and_redacts_pii(self) -> None:
        corpus = load_corpus("evals/corpus.json")
        scenario = next(item for item in corpus.scenarios if item.id == "sales-05")
        selected = Corpus(
            corpus.corpus_version, corpus.fixture_version, corpus.scoring_version,
            (scenario,), corpus.schema_version,
        )
        evaluation = await run_detailed_evaluation(
            selected,
            ScriptedConversationDriver(FixtureRegistry(now=NOW)),
            lane=LaneKind.SCRIPTED,
        )

        report = detailed_report_data(
            evaluation=evaluation,
            suites=(),
            evaluated_commit="abc123",
            generated_at=NOW,
            python_version="3.12.0",
            total_scenarios=60,
            partial=True,
        )
        encoded = json.dumps(report, sort_keys=True)

        self.assertEqual(len(report["corpus_run"]["turns"]), 1)
        self.assertIn("blocks", report["corpus_run"]["turns"][0]["actual"])
        self.assertIn("state_changes", report["corpus_run"]["turns"][0]["actual"])
        self.assertEqual(report["corpus_run"]["metrics"]["model_calls"], 1)
        self.assertGreater(report["corpus_run"]["metrics"]["input_tokens"], 0)
        self.assertGreaterEqual(report["corpus_run"]["metrics"]["latency_ms"], 0)
        self.assertEqual(report["corpus_run"]["success_rate"], 1.0)
        self.assertEqual(
            report["corpus_run"]["success_rate_by_category"]["sales_test_drive"],
            1.0,
        )
        self.assertEqual(report["suite_totals"]["tests"], 0)
        self.assertNotIn("sam@example.com", encoded)
        self.assertNotIn('"123"', encoded)
        self.assertIn("[REDACTED]", encoded)

    async def test_atomic_writer_emits_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            write_report_atomic(path, {"z": 1, "a": 2})

            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "a": 2,\n  "z": 1\n}\n')
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    async def test_report_rejects_nested_credentials_or_reasoning_payloads(self) -> None:
        corpus = load_corpus("evals/corpus.json")
        scenario = next(item for item in corpus.scenarios if item.id == "scope-01")
        selected = Corpus(
            corpus.corpus_version, corpus.fixture_version, corpus.scoring_version,
            (scenario,), corpus.schema_version,
        )
        evaluation = await run_detailed_evaluation(
            selected,
            ScriptedConversationDriver(FixtureRegistry(now=NOW)),
            lane=LaneKind.SCRIPTED,
        )
        observed = evaluation.records[0].observed
        poisoned = replace(
            evaluation,
            records=(
                replace(
                    evaluation.records[0],
                    observed=replace(
                        observed,
                        blocks=({"kind": "text", "payload": {"northstar_api_key": "bad"}},),
                    ),
                ),
            ),
        )

        with self.assertRaisesRegex(ValueError, "sensitive key"):
            detailed_report_data(
                evaluation=poisoned,
                suites=(),
                evaluated_commit="abc123",
                generated_at=NOW,
                python_version="3.12.0",
                total_scenarios=60,
                partial=True,
            )


if __name__ == "__main__":
    unittest.main()
