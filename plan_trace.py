"""plan_trace — build a human-readable execution trace for a plan run.

A sandboxed run proves what happened only if a human can read it back.
This is the trace half of the trustworthy-agentic-ai pipeline: it takes
an InspectablePlan and the hash-chained JSONL run log that
plan-sandbox-run wrote, and turns them into a timeline a reviewer can
check in a minute — every step, what it did, how long it took, and the
evidence behind it.

Multimodal evidence: a step's evidence is not just a log line. Each
step can carry text evidence (a note, a receipt, a word count) and
image evidence (a chart, a screenshot) with a caption saying what the
image shows. A trace is only as trustworthy as its evidence, so the
checker flags steps with none, images with no caption, and — with
--check-files — image files that are not actually there.

Checks the trace runs before it calls anything clean:

* the run log's hash chain is intact (same entry shape and hashing as
  plan-audit-log and plan-sandbox-run) — a tampered entry is caught,
  not silently rendered;
* the log's plan version matches this exact plan (content hash);
* every plan step is accounted for: executed, blocked, or never run;
* timestamps run forward, so a duration is never negative;
* every executed step has at least one evidence item;
* log entries that name a step the plan does not have are flagged,
  not dropped.

Usage:

    python3 plan_trace.py examples/plan.json --run examples/run.jsonl \\
        --evidence examples/evidence.json --check-files --out examples/trace.md

Exit codes: 0 trace clean, 1 problems found, 2 input error (a file
that cannot be read, or a malformed log line).
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

GENESIS = "GENESIS"
STEP_RE = re.compile(r"^([^:]+):\s")


def plan_hash(plan):
    """Content hash of a plan — the version pin, same as plan-approve."""
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def entry_hash(entry):
    """sha256 of a log entry's canonical JSON, excluding its own hash."""
    payload = {k: v for k, v in entry.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_run_log(path):
    """Parse a run-log JSONL file; a malformed line is an input error."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"run log {path} line {n}: invalid JSON: {exc}")
            if not isinstance(entry, dict):
                raise ValueError(
                    f"run log {path} line {n}: not a JSON object")
            entries.append(entry)
    return entries


def _parse_ts(value):
    """Parse an ISO timestamp, or None when it is missing/unparseable."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def verify_chain(entries):
    """Check the log's hash chain; returns a list of problem strings."""
    problems = []
    if not entries:
        return ["run log is empty"]
    prev_stored = GENESIS
    for i, entry in enumerate(entries):
        seq = entry.get("seq")
        if seq != i + 1:
            problems.append(
                f"entry {i + 1}: seq is {seq!r}, expected {i + 1} "
                "(gap, replay, or out-of-order entry)")
        stored = entry.get("hash")
        if stored is None:
            problems.append(f"entry {i + 1}: no hash recorded")
        elif entry_hash(entry) != stored:
            problems.append(
                f"entry {seq}: stored hash does not match its content "
                "(entry was tampered with after it was written)")
        if entry.get("prev_hash") != prev_stored:
            problems.append(
                f"entry {seq}: prev_hash does not link to the previous "
                "entry (chain is broken here)")
        if isinstance(stored, str):
            prev_stored = stored
    return problems


def _step_id_from_detail(detail):
    """The step id a log detail starts with, e.g. 'step-2' in
    'step-2: Summarise ...' — None when the detail names no step."""
    if not isinstance(detail, str):
        return None
    match = STEP_RE.match(detail)
    return match.group(1) if match else None


def build_trace(plan, entries, evidence=None, check_files=False,
                evidence_dir=None):
    """Build the trace report for a plan and its run log.

    evidence maps step ids to lists of {"kind": "text"|"image",
    "ref": ..., "caption": ...}. With check_files, an image ref is
    resolved against evidence_dir and must exist on disk.
    """
    evidence = evidence or {}
    version = plan_hash(plan)
    plan_id = plan.get("id")
    steps = {s["id"]: s for s in plan.get("steps", [])}
    problems = []

    report = {"plan": plan_id, "plan_version": version,
              "verdict": None, "entries_checked": len(entries),
              "steps": [], "problems": problems}

    problems.extend(verify_chain(entries))

    logged_versions = {e.get("version") for e in entries}
    if logged_versions and logged_versions != {version}:
        problems.append(
            "run log is for a different plan version than this plan "
            f"(log: {sorted(logged_versions)}, plan: {version[:12]}...)")
    logged_plans = {e.get("plan") for e in entries}
    if logged_plans and logged_plans != {plan_id}:
        problems.append(
            f"run log is for plan(s) {sorted(logged_plans)}, "
            f"not {plan_id!r}")

    # Timestamps must run forward; parse once, flag what cannot parse.
    stamps = []
    for entry in entries:
        ts = _parse_ts(entry.get("ts"))
        if ts is None:
            problems.append(
                f"entry {entry.get('seq')}: timestamp "
                f"{entry.get('ts')!r} is missing or unparseable")
        stamps.append(ts)
    for a, b in zip(stamps, stamps[1:]):
        if a is not None and b is not None and b < a:
            problems.append(
                "timestamps run backwards in the log "
                f"({a.isoformat()} after {b.isoformat()})")

    # What the log says each step did.
    step_events = {}
    outcome = None
    for entry in entries:
        action = entry.get("action")
        if action == "run_completed":
            outcome = "completed"
            continue
        if action == "run_blocked":
            outcome = "blocked"
            continue
        if action not in ("step_executed", "step_blocked"):
            problems.append(
                f"entry {entry.get('seq')}: unknown log action "
                f"{action!r}")
            continue
        step_id = _step_id_from_detail(entry.get("detail"))
        if step_id not in steps:
            problems.append(
                f"entry {entry.get('seq')}: log names step "
                f"{step_id!r}, which this plan does not have")
            continue
        step_events.setdefault(step_id, []).append(entry)
    if outcome is None:
        problems.append("run log has no outcome entry "
                        "(neither run_completed nor run_blocked)")

    # One timeline row per plan step, in plan order.
    for step_id, step in steps.items():
        events = step_events.get(step_id, [])
        executed = [e for e in events if e.get("action") == "step_executed"]
        blocked = [e for e in events if e.get("action") == "step_blocked"]
        if executed:
            status, result = "executed", executed[-1].get("detail", "")
        elif blocked:
            status, result = "blocked", blocked[-1].get("detail", "")
        else:
            status, result = "not run", ""
        duration = None
        if executed:
            idx = entries.index(executed[-1])
            if idx + 1 < len(entries):
                start, end = stamps[idx], stamps[idx + 1]
                if start is not None and end is not None:
                    duration = round((end - start).total_seconds(), 3)
        items = evidence.get(step_id, [])
        step_problems = []
        if status == "executed" and not items:
            step_problems.append(
                f"step {step_id} executed but has no evidence attached")
        for item in items:
            kind, ref = item.get("kind"), item.get("ref")
            if kind not in ("text", "image"):
                step_problems.append(
                    f"step {step_id}: evidence kind {kind!r} is neither "
                    "'text' nor 'image'")
            elif not ref:
                step_problems.append(
                    f"step {step_id}: evidence item has no ref")
            elif kind == "image":
                if not item.get("caption"):
                    step_problems.append(
                        f"step {step_id}: image evidence {ref!r} has no "
                        "caption saying what it shows")
                if check_files and evidence_dir is not None:
                    if not (Path(evidence_dir) / ref).is_file():
                        step_problems.append(
                            f"step {step_id}: image evidence file "
                            f"{ref!r} does not exist")
        problems.extend(step_problems)
        report["steps"].append({
            "id": step_id, "action": step.get("action", ""),
            "status": status, "result": result,
            "duration_s": duration, "evidence": items,
            "problems": step_problems,
        })

    report["outcome"] = outcome
    report["verdict"] = "clean" if not problems else "problems"
    return report


def render_markdown(report):
    """Render the trace report as a Markdown timeline."""
    lines = [f"# Trace — {report['plan']}",
             "",
             f"Verdict: **{report['verdict']}** · "
             f"outcome: {report['outcome'] or 'unknown'} · "
             f"{report['entries_checked']} log entries checked · "
             f"plan version `{report['plan_version'][:12]}`",
             "",
             "| Step | Action | Status | Took | Evidence |",
             "| --- | --- | --- | --- | --- |"]
    for step in report["steps"]:
        ev = "; ".join(
            f"{item.get('kind')}: {item.get('ref')}"
            for item in step["evidence"]) or "—"
        took = (f"{step['duration_s']}s"
                if step["duration_s"] is not None else "—")
        lines.append(f"| {step['id']} | {step['action']} | "
                     f"{step['status']} | {took} | {ev} |")
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    any_evidence = False
    for step in report["steps"]:
        for item in step["evidence"]:
            any_evidence = True
            caption = item.get("caption") or "(no caption)"
            lines.append(f"- **{step['id']}** ({item.get('kind')}): "
                         f"`{item.get('ref')}` — {caption}")
    if not any_evidence:
        lines.append("No evidence attached to any step.")
    lines.append("")
    lines.append("## Problems")
    lines.append("")
    if report["problems"]:
        for problem in report["problems"]:
            lines.append(f"- {problem}")
    else:
        lines.append("None — the chain verifies, every step is accounted "
                     "for, and every executed step has evidence.")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a human-readable execution trace for a plan run.")
    parser.add_argument("plan", help="InspectablePlan JSON file")
    parser.add_argument("--run", required=True,
                        help="run log JSONL file (from plan-sandbox-run)")
    parser.add_argument("--evidence",
                        help="evidence manifest JSON file (step id -> "
                        "list of {kind, ref, caption})")
    parser.add_argument("--check-files", action="store_true",
                        help="check that image evidence files exist")
    parser.add_argument("--out", help="where to write the Markdown trace")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        plan = read_json(args.plan)
        entries = read_run_log(args.run)
        evidence = read_json(args.evidence) if args.evidence else {}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    if not isinstance(evidence, dict):
        print("input error: evidence file must be a JSON object keyed "
              "by step id", file=sys.stderr)
        return 2

    evidence_dir = (str(Path(args.evidence).parent)
                    if args.evidence else None)
    report = build_trace(plan, entries, evidence,
                         check_files=args.check_files,
                         evidence_dir=evidence_dir)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(render_markdown(report))
        print(f"trace written to {args.out}")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['plan']}: verdict {report['verdict']} "
              f"({report['outcome'] or 'no outcome'}, "
              f"{len(report['steps'])} steps, "
              f"{len(report['problems'])} problems)")
        for step in report["steps"]:
            ev = f", {len(step['evidence'])} evidence" \
                if step["evidence"] else ", no evidence"
            print(f"  {step['status']:>8} {step['id']}{ev}")
        for problem in report["problems"]:
            print(f"  ! {problem}")
    return 0 if report["verdict"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())
