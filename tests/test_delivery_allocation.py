import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from capabilities.task_catalog.allocation import counts_for
from pipelines.benchmark.design.production_allocation import step_allocations
from pipelines.benchmark.execution.delivery_contract import verify_delivery
from pipelines.benchmark.design.production_steps import complete_production_steps
from pipelines.benchmark.execution.runner import run


class DeliveryAllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_shards_must_cover_plan_and_each_be_packaged(self):
        selection = [{"template_ids": ["C01-T01"], "count": 2}]
        steps = [
            {"id": "one", "uses": "templates.produce", "arguments": {"selections": selection[:]}},
            {"id": "pack", "uses": "data.package", "arguments": {"dataset": "$one.dataset"}},
        ]
        self.assertEqual(step_allocations(steps, selection)["one"], dict(counts_for(selection)))
        steps[0]["arguments"]["selections"] = [{"template_ids": ["C01-T01"], "count": 1}]
        with self.assertRaisesRegex(ValueError, "exact"):
            step_allocations(steps, selection)
        steps += [
            {"id": "two", "uses": "templates.produce", "arguments": steps[0]["arguments"]},
            {"id": "pack_two", "uses": "data.package", "arguments": {"dataset": "$two.dataset"}},
        ]
        self.assertEqual(len(step_allocations(steps, selection)), 2)

    def test_delivery_reads_items_instead_of_trusting_producer_report(self):
        (self.root / "questions.jsonl").write_text('{"template_id":"C01-T01"}\n')
        plan = {"inputs": {"selections": [{"template_ids": ["C01-T01"], "count": 2}]}}
        outputs = [{"uses": "data.package", "result": {"dataset": str(self.root), "accepted": 2}}]
        with self.assertRaisesRegex(ValueError, "active S1"):
            verify_delivery(plan, outputs, "S5")

    def test_mismatched_saved_plan_stops_before_production(self):
        steps = complete_production_steps([
            {"id": "questions", "uses": "templates.produce", "arguments": {
                "evidence": "$inputs.evidence",
                "selections": [{"template_ids": ["C01-T01"], "count": 1}]}},
            {"id": "package", "uses": "data.package", "arguments": {"dataset": "$questions.dataset"}},
        ])
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"steps": steps, "assessment": {"execution": ["S1", "S2", "S3", "S4", "S5"]},
            "inputs": {"selections": [{"template_ids": ["C01-T01"], "count": 2}]}}))
        with patch("pipelines.benchmark.execution.runner.load_policy", return_value={"execution_mode": "production"}), \
             patch("pipelines.benchmark.execution.runner.produce") as producer:
            result = run({"plan": str(plan), "run": str(self.root / "run"), "_workspace": str(self.root)}, self.root)
        self.assertEqual(result["state"], "needs_plan_revision")
        self.assertIn("exact per-template S1 plan", result["error"])
        producer.assert_not_called()
