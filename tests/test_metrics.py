import os
import tempfile
import unittest
from pathlib import Path

from app.charts import render_chart
from app.db import connect, database_ready, fetch, import_seed
from app.metrics import annotate_prep, build_dashboard


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = str(Path(self.tmp.name) / "app.db")
        self.seed = Path(__file__).resolve().parents[1] / "seed"

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        with connect() as connection:
            counts = import_seed(connection, self.seed)
            prep = fetch(connection, "prep")
            weld = fetch(connection, "weld")
            docs = fetch(connection, "docs")
        return counts, prep, weld, docs

    def test_seed_matches_the_control_example_and_full_period(self):
        counts, prep, weld, docs = self.load()
        self.assertEqual(counts, {"prep": 9, "weld": 8, "docs": 6})

        full = build_dashboard(prep, weld, docs, "2026-10-05", "2026-10-14")
        self.assertEqual(full["prep"]["fact"], 290)
        self.assertEqual(full["prep"]["plan"], 400)
        self.assertEqual(full["prep"]["used"], 4)
        self.assertEqual(full["prep"]["excluded"], 5)
        self.assertAlmostEqual(full["prep"]["ratio"], 290 / 400)
        self.assertEqual(full["weld"]["fact"], 290)
        self.assertEqual(full["weld"]["need"], 320)
        self.assertEqual(full["weld"]["blocked_batches"], ["LOT-03", "LOT-04", "LOT-05", "LOT-07"])
        self.assertEqual(full["docs"]["fact"], 18)
        self.assertEqual(full["docs"]["plan"], 26)
        self.assertEqual([problem["code"] for problem in full["problems"]], ["P-003", "P-004", "P-005", "P-007"])

        daily_fact = sum(day["prep"]["fact"] for day in full["by_date"] if day["prep"]["fact"] is not None)
        self.assertEqual(daily_fact, 290)

        control = build_dashboard(prep, weld, docs, "2026-10-05", "2026-10-06")
        self.assertEqual(control["prep"]["fact"], 190)
        self.assertEqual(control["prep"]["plan"], 200)
        self.assertEqual(control["prep"]["ratio"], 0.95)
        self.assertEqual(control["problems"], [])
        self.assertEqual(control["weld"]["blocked_batches"], [])

        chart_values = [bar["value"] for group in full["prep_chart"] for bar in group["bars"]]
        self.assertNotIn(1200, chart_values)
        lot_03 = next(group for group in full["weld_chart"] if group["label"] == "LOT-03")
        self.assertIsNone(lot_03["bars"][1]["value"])
        svg = render_chart(full["weld_chart"], ["#b0893e", "#0e7c66"])
        self.assertIn("LOT-03", svg)
        self.assertIn("н/д", svg)
        self.assertNotIn("1200", svg)

    def test_fixing_prep_changes_welding_and_leaves_docs(self):
        _counts, prep, weld, docs = self.load()
        for row in prep:
            if row["record_id"] == "P-003":
                row["fact_qty"] = 100
        model = build_dashboard(prep, weld, docs, "2026-10-05", "2026-10-14")
        self.assertEqual(model["prep"]["fact"], 390)
        self.assertEqual(model["prep"]["plan"], 500)
        self.assertEqual(model["weld"]["fact"], 390)
        self.assertEqual(model["weld"]["need"], 450)
        self.assertNotIn("LOT-03", model["weld"]["blocked_batches"])
        self.assertEqual(model["docs"]["fact"], 18)
        self.assertEqual(model["docs"]["plan"], 26)

    def test_removing_one_duplicate_returns_the_batch_to_the_total(self):
        _counts, prep, weld, docs = self.load()
        removed = False
        kept = []
        for row in prep:
            if row["record_id"] == "P-005" and not removed:
                removed = True
                continue
            kept.append(row)
        model = build_dashboard(kept, weld, docs, "2026-10-05", "2026-10-14")
        self.assertEqual(model["prep"]["fact"], 370)
        self.assertEqual(model["prep"]["plan"], 500)
        self.assertNotIn("LOT-05", model["weld"]["blocked_batches"])
        self.assertEqual(model["docs"]["plan"], 26)

    def test_boundary_zero_and_missing_fact(self):
        rows = [
            self.row("A", 100, 200, "L1"),
            self.row("B", 100, 201, "L2"),
            self.row("C", 100, 0, "L3"),
            self.row("D", 100, None, "L4"),
            self.row("E", 100, -1, "L5"),
        ]
        annotated = {row["record_id"]: row for row in annotate_prep(rows)}
        self.assertTrue(annotated["A"]["reliable"])
        self.assertFalse(annotated["B"]["reliable"])
        self.assertTrue(annotated["C"]["reliable"])
        self.assertFalse(annotated["D"]["reliable"])
        self.assertFalse(annotated["E"]["reliable"])

    def test_failed_import_does_not_mark_the_database_ready(self):
        with connect() as connection:
            with self.assertRaises(FileNotFoundError):
                import_seed(connection, Path(self.tmp.name) / "missing")
        with connect() as connection:
            self.assertFalse(database_ready(connection))

    @staticmethod
    def row(record_id, plan, fact, batch):
        return {
            "id": 1,
            "record_id": record_id,
            "date": "2026-10-01",
            "batch_id": batch,
            "employee": "Анна",
            "plan_qty": plan,
            "fact_qty": fact,
        }
