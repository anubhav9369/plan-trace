"""Tests for plan_trace — execution traces with multimodal evidence."""

import copy
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plan_trace import (GENESIS, build_trace, entry_hash, plan_hash,
                        read_run_log, render_markdown)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PLAN = json.loads((ROOT / "examples" / "plan.json").read_text())
RUN = read_run_log(str(ROOT / "examples" / "run.jsonl"))
EVIDENCE = json.loads((ROOT / "examples" / "evidence.json").read_text())
VERSION = plan_hash(PLAN)


def chained(specs):
    """Build a properly hash-chained log from (action, detail, ts) specs."""
    entries, prev = [], GENESIS
    for i, (action, detail, ts) in enumerate(specs, start=1):
        entry = {"seq": i, "ts": ts, "actor": "sandbox", "action": action,
                 "plan": "weekly-report", "version": VERSION,
                 "detail": detail, "prev_hash": prev}
        entry["hash"] = entry_hash(entry)
        entries.append(entry)
        prev = entry["hash"]
    return entries


CLEAN_SPECS = [
    ("step_executed", "step-1: Read the week's notes and list what happened. — read 2 sources", "2026-11-14T09:00:00Z"),
    ("step_executed", "step-2: Summarise the notes into a short draft under 300 words. — summary drafted (212 words)", "2026-11-14T09:00:01Z"),
    ("step_executed", "step-3: Confirm the recipient list and the exact message before sending. — recipient and message confirmed", "2026-11-14T09:00:02Z"),
    ("step_executed", "step-4: Send the approved summary to the team list. — sent (simulated)", "2026-11-14T09:00:03Z"),
    ("run_completed", "all 4 steps executed", "2026-11-14T09:00:04Z"),
]


class TestPlanTrace(unittest.TestCase):
    def test_clean_trace(self):
        report = build_trace(PLAN, RUN, EVIDENCE,
                             check_files=True, evidence_dir=str(ROOT / "examples"))
        self.assertEqual(report["verdict"], "clean")
        self.assertEqual(report["outcome"], "completed")
        self.assertEqual(report["problems"], [])
        self.assertEqual([s["status"] for s in report["steps"]],
                         ["executed"] * 4)
        self.assertEqual([s["duration_s"] for s in report["steps"]],
                         [1.0, 1.0, 1.0, 1.0])

    def test_tampered_entry_detected(self):
        broken = read_run_log(str(ROOT / "examples" / "broken-run.jsonl"))
        report = build_trace(PLAN, broken, EVIDENCE)
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("seq 2" in p or "entry 2" in p for p in report["problems"]),
                        report["problems"])
        self.assertTrue(any("tampered" in p for p in report["problems"]),
                        report["problems"])

    def test_version_mismatch(self):
        other = copy.deepcopy(PLAN)
        other["goal"] = "Send a completely different summary"
        report = build_trace(other, RUN, EVIDENCE)
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("different plan version" in p for p in report["problems"]),
                        report["problems"])

    def test_missing_evidence_flagged(self):
        partial = {k: v for k, v in EVIDENCE.items() if k != "step-3"}
        report = build_trace(PLAN, RUN, partial)
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("step-3" in p and "no evidence" in p
                            for p in report["problems"]), report["problems"])

    def test_image_without_caption_flagged(self):
        evidence = copy.deepcopy(EVIDENCE)
        evidence["step-2"][0] = {"kind": "image",
                                 "ref": "evidence/summary-chart.svg"}
        report = build_trace(PLAN, RUN, evidence)
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("no caption" in p for p in report["problems"]),
                        report["problems"])

    def test_missing_image_file_flagged_only_with_check_files(self):
        evidence = copy.deepcopy(EVIDENCE)
        evidence["step-1"].append(
            {"kind": "image", "ref": "evidence/no-such-file.png",
             "caption": "a chart that was never saved"})
        unchecked = build_trace(PLAN, RUN, evidence)
        self.assertEqual(unchecked["verdict"], "clean")
        checked = build_trace(PLAN, RUN, evidence, check_files=True,
                              evidence_dir=str(ROOT / "examples"))
        self.assertEqual(checked["verdict"], "problems")
        self.assertTrue(any("no-such-file.png" in p and "does not exist" in p
                            for p in checked["problems"]), checked["problems"])

    def test_out_of_order_timestamps(self):
        specs = list(CLEAN_SPECS)
        specs[1] = (specs[1][0], specs[1][1], "2026-11-14T09:00:02Z")
        specs[2] = (specs[2][0], specs[2][1], "2026-11-14T09:00:01Z")
        report = build_trace(PLAN, chained(specs), EVIDENCE)
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("backwards" in p for p in report["problems"]),
                        report["problems"])

    def test_unknown_step_in_log(self):
        specs = CLEAN_SPECS[:4] + [
            ("step_executed", "step-9: Do something the plan never asked for. — ran (simulated)",
             "2026-11-14T09:00:04Z"),
            ("run_completed", "all 4 steps executed", "2026-11-14T09:00:05Z"),
        ]
        report = build_trace(PLAN, chained(specs), EVIDENCE)
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("step-9" in p and "does not have" in p
                            for p in report["problems"]), report["problems"])

    def test_markdown_render(self):
        report = build_trace(PLAN, RUN, EVIDENCE)
        md = render_markdown(report)
        for step_id in ("step-1", "step-2", "step-3", "step-4"):
            self.assertIn(step_id, md)
        self.assertIn("clean", md)
        self.assertIn("212 words against the 300-word limit", md)
        self.assertIn("summary-chart.svg", md)


if __name__ == "__main__":
    unittest.main()
