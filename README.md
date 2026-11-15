# plan-trace

A sandboxed run proves what happened only if a human can read it
back. This is the trace half of the trustworthy-agentic-ai pipeline:
it takes an InspectablePlan and the hash-chained JSONL run log that
plan-sandbox-run wrote, and turns them into a timeline a reviewer can
check in a minute — every step, what it did, how long it took, and the
evidence behind it.

Part of the trustworthy-agentic-ai prototype for **BRJP26100123 —
Building Trustworthy AI Systems through Explainability and
Human-in-the-Loop Control**. Companion repos:
[trustworthy-agentic-ai](https://github.com/anubhav9369/trustworthy-agentic-ai)
(the InspectablePlan schema),
[plan-explainer](https://github.com/anubhav9369/plan-explainer)
(plain-language rendering + review checklist),
[plan-diff](https://github.com/anubhav9369/plan-diff)
(what changed between two plan versions),
[plan-risk-scan](https://github.com/anubhav9369/plan-risk-scan)
(what needs a human's attention before approval),
[plan-approve](https://github.com/anubhav9369/plan-approve)
(the human decision point),
[plan-refine](https://github.com/anubhav9369/plan-refine)
(applying the human's requested changes),
[plan-policy-check](https://github.com/anubhav9369/plan-policy-check)
(validating a plan against a policy pack),
[plan-audit-log](https://github.com/anubhav9369/plan-audit-log)
(verifying the hash-chained audit log), and
[plan-sandbox-run](https://github.com/anubhav9369/plan-sandbox-run)
(running an approved plan in a sandbox).

## What the trace checks before it calls anything clean

| Check | What it catches |
| --- | --- |
| The run log's hash chain is intact | An entry edited after it was written |
| The log's plan version matches this exact plan | A trace built from the wrong plan version |
| Every plan step is accounted for | A step that never ran, or ran and vanished from the story |
| Timestamps run forward | A doctored or mis-ordered timeline |
| Every executed step has evidence | A step nobody can show proof for |
| Image evidence has a caption, and exists on disk (`--check-files`) | A chart nobody explained, or a file that was never saved |
| Log entries naming steps the plan does not have | Actions the plan never asked for, smuggled into the log |

## Multimodal evidence

A step's evidence is not just a log line. Each step carries text
evidence (a note, a receipt, a word count) and image evidence (a
chart, a screenshot) with a caption saying what the image shows. The
example run attaches a chart to step-2 —
`examples/evidence/summary-chart.svg` — showing the draft at 212
words against the 300-word limit, so the reviewer sees the claim
instead of taking the log's word for it.

## Usage

```bash
python3 plan_trace.py examples/plan.json --run examples/run.jsonl \
    --evidence examples/evidence.json --check-files --out examples/trace.md
```

The example run is clean: 4 steps executed, every step evidenced,
exit 0, and the trace lands in `examples/trace.md`.

```bash
python3 plan_trace.py examples/plan.json \
    --run examples/broken-run.jsonl --evidence examples/evidence.json
```

The broken log is the same run with step-2's detail edited after the
fact ("212 words" → "999 words"). The trace catches it — entry 2's
stored hash no longer matches its content — and exits 1.

Exit codes: 0 trace clean, 1 problems found, 2 input error. `--json`
prints the full report (verdict, per-step status, durations, evidence,
problems) as JSON.

Python API:

```python
from plan_trace import build_trace, render_markdown

report = build_trace(plan, entries, evidence, check_files=True,
                     evidence_dir="examples")
report["verdict"]  # "clean" | "problems"
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

9 tests, all passing.
