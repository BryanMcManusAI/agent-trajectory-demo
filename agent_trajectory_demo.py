#!/usr/bin/env python3
"""
Agent-Trajectory Auditor — public method demo (SYNTHETIC DATA ONLY)
==================================================================

The same instrument that audits rubric annotations reads agent trajectories the same way,
because the failure shape is the same: a sequence that *performs* reasoning without doing any.
This is the trajectory sibling of the bad-data detector — it scores the SUBSTANCE and
FAITHFULNESS of an agent's run, not the fact that it produced a fluent-looking answer, and it
treats every disagreement with the "completed / passed" label as a hypothesis to re-check.

CLEAN-ROOM reconstruction from a publicly described approach. NO proprietary code, NO internal
data, NO production feature set. Every trajectory below is fabricated for this demo. Standard
library only — `python3 agent_trajectory_demo.py`.

The three agent failure modes it routes, and their annotation-side twins:

  FABRICATED   the run concludes on a value that never appeared in any tool result — it reasoned
               on a fabricated tool output.        (annotation twin: a claim the substance can't support)
  LOOP         the agent repeats the same action with no new information — a run that each step
               looks fine but as a set goes nowhere.   (annotation twin: a near-duplicate batch)
  VACUOUS      fluent, on-template reasoning that never touches a concrete result — the form of
               reasoning with none of the substance.   (annotation twin: a VACUOUS annotation)

What it shows, end to end:
  1. A fabricated corpus of agent trajectories — genuine runs plus one of each failure mode.
  2. A transparent auditor that routes each to a verdict from the trajectory alone.
  3. The signature move: mark a few bad runs "completed" (the lenient label), then show the
     auditor's disagreements recover exactly those — the labels get audited, not the auditor.
"""
from __future__ import annotations
import argparse
import re
import sys
from dataclasses import dataclass, field

import random
RNG = random.Random(7)  # deterministic

# ──────────────────────────────────────────────────────────────────────────────
# 1. Synthetic trajectories. A trajectory is a list of steps; each step is a
#    (kind, payload) tuple. kinds: think / call / result / answer. The environment
#    "returns" values only in `result` steps and the task prompt — anything the run
#    concludes must trace back to one of those, or it was fabricated.
# ──────────────────────────────────────────────────────────────────────────────

TASKS = [
    ("Find the incident code for ticket {n}.", "INC-{c}"),
    ("Look up the SKU for order {n}.", "SKU-{c}"),
    ("Retrieve the change-request id for build {n}.", "CR-{c}"),
    ("Get the asset tag for host {n}.", "AST-{c}"),
]
GENUINE_THINK = [
    "I'll query the tracker for ticket {n}.",
    "Let me search the system of record for {n}.",
    "First I need to look {n} up in the registry.",
]
GENUINE_CONCLUDE = [
    "The tracker returns {code} for {n}; that's the answer.",
    "Record for {n} shows {code} — confirmed.",
    "{n} maps to {code} in the registry.",
]
# Vacuous reasoning: fluent, on-template, never names a concrete value.
VACUOUS_THINK = [
    "I'll comprehensively analyze the available information to derive a robust conclusion.",
    "Let me thoroughly review the results and synthesize a coherent, well-structured answer.",
    "I'll take a nuanced, effective approach to address the essential aspects of this task.",
]
VACUOUS_CONCLUDE = [
    "Based on a comprehensive review, the task is successfully addressed.",
    "The results effectively demonstrate a thorough and coherent outcome.",
    "Overall this robustly satisfies the relevant requirements.",
]


def _code(n):
    return f"{7000 + n * 13 % 900}{n % 10}{(n * 7) % 100:02d}"[:5]


@dataclass
class Traj:
    id: int
    task: str
    steps: list                 # list of (kind, payload)
    failure_mode: str | None    # None (GENUINE) | FABRICATED | LOOP | VACUOUS  (ground truth)
    given_label: str = ""       # what a lenient reviewer recorded (GENUINE / a failure)
    predicted: str = "GENUINE"
    fired: list = field(default_factory=list)


def _n(i):
    return 4400 + i * 3


def make_genuine(i):
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); code = codetmpl.format(c=_code(n))
    task = tmpl.format(n=n)
    tool = task.split()[1]
    steps = [
        ("think", RNG.choice(GENUINE_THINK).format(n=n)),
        ("call", (tool, str(n))),
        ("result", code),
        ("answer", RNG.choice(GENUINE_CONCLUDE).format(code=code, n=n)),
    ]
    return Traj(i, task, steps, None)


def make_fabricated(i):
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); real = codetmpl.format(c=_code(n))
    # concludes on a value that never appeared — a fabricated tool output
    fake = codetmpl.format(c=_code(n + 517))
    task = tmpl.format(n=n); tool = task.split()[1]
    steps = [
        ("think", RNG.choice(GENUINE_THINK).format(n=n)),
        ("call", (tool, str(n))),
        ("result", real),
        ("answer", RNG.choice(GENUINE_CONCLUDE).format(code=fake, n=n)),  # fabricated conclusion
    ]
    return Traj(i, task, steps, "FABRICATED")


def make_loop(i):
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); task = tmpl.format(n=n); tool = task.split()[1]
    steps = [("think", RNG.choice(GENUINE_THINK).format(n=n))]
    for _ in range(3):  # same call, same unhelpful result, no progress
        steps += [("call", (tool, str(n))), ("result", "no direct match; refine and retry")]
        steps += [("think", "No match yet, I'll search again.")]
    steps += [("answer", "Unable to determine after repeated attempts.")]
    return Traj(i, task, steps, "LOOP")


def make_vacuous(i):
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); code = codetmpl.format(c=_code(n)); task = tmpl.format(n=n); tool = task.split()[1]
    steps = [
        ("think", RNG.choice(VACUOUS_THINK)),
        ("call", (tool, str(n))),
        ("result", code),
        ("think", RNG.choice(VACUOUS_CONCLUDE)),   # never names the concrete result
        ("answer", RNG.choice(VACUOUS_CONCLUDE)),
    ]
    return Traj(i, task, steps, "VACUOUS")


# Hard cases — so the demo doesn't score a suspicious 1.000.
def make_terse_genuine(i):
    # correct and grounded, but no narration at all → tempts a VACUOUS misread
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); code = codetmpl.format(c=_code(n)); task = tmpl.format(n=n); tool = task.split()[1]
    steps = [("call", (tool, str(n))), ("result", code), ("answer", code)]
    return Traj(i, task, steps, None)


def make_reformatted_genuine(i):
    # grounded, but the answer reformats the value (adds the prefix the raw result omitted)
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); core = _code(n); task = tmpl.format(n=n); tool = task.split()[1]
    steps = [
        ("think", RNG.choice(GENUINE_THINK).format(n=n)),
        ("call", (tool, str(n))),
        ("result", core),                                   # raw digits only
        ("answer", codetmpl.format(c=core) + f" is the record for {n}."),  # prefixed form
    ]
    return Traj(i, task, steps, None)


def make_confirm_genuine(i):
    # genuine: queries, then RE-queries to confirm, gets the same value, proceeds. On progress
    # signal alone this is indistinguishable from a stuck loop — an honest false-positive case.
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); code = codetmpl.format(c=_code(n)); task = tmpl.format(n=n); tool = task.split()[1]
    steps = [
        ("think", RNG.choice(GENUINE_THINK).format(n=n)),
        ("call", (tool, str(n))), ("result", code),
        ("think", "Let me confirm that before I answer."),
        ("call", (tool, str(n))), ("result", code),
        ("answer", f"Confirmed: {n} maps to {code}."),
    ]
    return Traj(i, task, steps, None)


def make_parrot_vacuous(i):
    # vacuous, but parrots the concrete value once — enough to pass a substance check that keys
    # on "did it name a real result?" An honest false-negative: naming a value isn't reasoning.
    tmpl, codetmpl = RNG.choice(TASKS)
    n = _n(i); code = codetmpl.format(c=_code(n)); task = tmpl.format(n=n); tool = task.split()[1]
    steps = [
        ("think", RNG.choice(VACUOUS_THINK)),
        ("call", (tool, str(n))), ("result", code),
        ("answer", f"{code}. This comprehensively and effectively satisfies the requirement."),
    ]
    return Traj(i, task, steps, "VACUOUS")


def build_corpus():
    items = []
    for i in range(14):
        items.append(make_genuine(i))
    base = 14
    for k, mk in enumerate((make_fabricated, make_loop, make_vacuous)):
        for j in range(6):
            items.append(mk(base + k * 6 + j))
    hard = base + 18
    items += [make_terse_genuine(hard + i) for i in range(3)]
    items += [make_reformatted_genuine(hard + 3 + i) for i in range(3)]
    items += [make_confirm_genuine(hard + 6 + i) for i in range(2)]   # → honest false positives
    items += [make_parrot_vacuous(hard + 8 + i) for i in range(1)]    # → honest false negative
    RNG.shuffle(items)
    for it in items:                          # honest baseline: correct label == truth
        it.given_label = it.failure_mode or "GENUINE"
    return items


# ──────────────────────────────────────────────────────────────────────────────
# 2. The trajectory auditor. Transparent axes — grounding, progress, substance —
#    each a human-readable reason, never a black-box score.
# ──────────────────────────────────────────────────────────────────────────────

TOKEN = re.compile(r"\b([A-Z]{2,5}-[\dA-Z]+|\d{3,6})\b")
BUZZ = re.compile(r"\b(comprehensive|robust|nuanced|thorough|effective(ly)?|coherent|"
                  r"well-structured|high-quality|relevant|essential|synthesize)\b", re.I)


def _digits(tok):
    return re.sub(r"\D", "", tok)


def _steps(it, kind):
    return [p for k, p in it.steps if k == kind]


def grounded_cores(it):
    """Every value the environment actually provided: the task prompt + all tool results."""
    blob = it.task + " " + " ".join(_steps(it, "result"))
    return {_digits(m.group(1)) for m in TOKEN.finditer(blob)}


def conclusion_text(it):
    return " ".join(_steps(it, "answer") + _steps(it, "think"))


def audit(it):
    """Assign a verdict from the trajectory alone. Priority: fabrication > loop > vacuous."""
    fired = []
    grounded = grounded_cores(it)
    ans = " ".join(_steps(it, "answer"))
    ans_tokens = [m.group(1) for m in TOKEN.finditer(ans)]

    # axis 1 — grounding: a load-bearing value in the answer with no support in any result
    ungrounded = [t for t in ans_tokens if _digits(t) and _digits(t) not in grounded]
    if ungrounded:
        fired.append(f"answer cites {ungrounded[0]} — absent from every tool result (fabricated)")
        it.predicted, it.fired = "FABRICATED", fired
        return it

    # axis 2 — progress: the same action, repeated, returning nothing new
    calls = _steps(it, "call")
    results = _steps(it, "result")
    sigs = [f"{tool}|{args}" for tool, args in calls]
    repeated = max((sigs.count(s) for s in set(sigs)), default=0)
    stuck = len(set(results)) < len(results) and len(results) > 1
    if repeated >= 3 or (repeated >= 2 and stuck):
        fired.append(f"same action ×{repeated} with no new result (loop)")
        it.predicted, it.fired = "LOOP", fired
        return it

    # axis 3 — substance: reasoning that never touches a concrete value and leans on buzzwords
    concl = conclusion_text(it)
    references_value = any(_digits(m.group(1)) in grounded for m in TOKEN.finditer(concl))
    buzz = len(BUZZ.findall(concl))
    if not references_value and buzz >= 2:
        fired.append(f"reasoning names no concrete result; {buzz} template/buzz terms (vacuous)")
        it.predicted, it.fired = "VACUOUS", fired
        return it

    # otherwise: grounded, progressing, substantive
    if references_value or ans_tokens:
        fired.append("answer grounded in a tool result")
    it.predicted, it.fired = "GENUINE", fired
    return it


# ──────────────────────────────────────────────────────────────────────────────
# 3. Metrics + the disagreement-as-signal demonstration.
# ──────────────────────────────────────────────────────────────────────────────

def prf1(items, truth_attr="failure_mode"):
    """Binary bad-trajectory detection: BAD = any failure verdict vs a genuine run."""
    def is_bad_truth(i):
        return (getattr(i, truth_attr) not in (None, "GENUINE"))
    tp = sum(i.predicted != "GENUINE" and is_bad_truth(i) for i in items)
    fp = sum(i.predicted != "GENUINE" and not is_bad_truth(i) for i in items)
    fn = sum(i.predicted == "GENUINE" and is_bad_truth(i) for i in items)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f1


def routing(items):
    """Per-failure-mode: of the runs that truly failed this way, how many routed to that queue."""
    modes = ("FABRICATED", "LOOP", "VACUOUS")
    out = {}
    for m in modes:
        truth = [i for i in items if i.failure_mode == m]
        hit = sum(i.predicted == m for i in truth)
        out[m] = (hit, len(truth))
    return out


def _report(items):
    print("=" * 72)
    print("STEP 1 — Auditor vs. ground truth (synthetic)")
    print("=" * 72)
    p, r, f1 = prf1(items)
    print(f"  {len(items)} fabricated trajectories  |  bad-run detection  "
          f"precision {p:.3f}  recall {r:.3f}  F1 {f1:.3f}")
    print("  (Synthetic numbers — they show the substance/faithfulness axes separate the classes.)\n")
    print("  Routing each failure to the right queue (correct verdict / true count):")
    for mode, (hit, tot) in routing(items).items():
        print(f"     {mode:11} {hit}/{tot}")
    print()
    for mode in ("FABRICATED", "LOOP", "VACUOUS", None):
        ex = next((i for i in items if i.failure_mode == mode), None)
        if not ex:
            continue
        label = mode or "GENUINE"
        print(f"  [{label}] → {ex.predicted}   task: {ex.task}")
        print(f"     fired: {'; '.join(ex.fired) or '—'}")
    print()

    print("=" * 72)
    print("STEP 2 — Disagreement-as-signal: mark bad runs 'completed', watch them surface")
    print("=" * 72)
    planted = [i for i in items if i.failure_mode][:4]
    for it in planted:
        it.given_label = "GENUINE"     # the lenient (wrong) label: "the run passed"
    print(f"  Marked {len(planted)} failed runs as GENUINE — the same one-directional leniency")
    print("  ('it produced an answer, ship it') a real gold set drifts into.\n")

    disagreements = [i for i in items if i.predicted != i.given_label]
    recovered = [i for i in disagreements if i in planted]
    print(f"  Auditor disagreed with the labels on {len(disagreements)} run(s) — a small, cheap-to-")
    print("  re-read queue. Every planted lenient error surfaces, alongside the auditor's own hard-")
    print("  case misses; one human pass sorts which is which:\n")
    for it in disagreements:
        tag = "← planted 'completed' error" if it in planted else "← auditor's own miss (re-read)"
        print(f"     id {it.id:>2}  label={it.given_label:<11} auditor={it.predicted:<11} {tag}")
    print(f"\n  Recovered {len(recovered)}/{len(planted)} planted label errors. The point isn't that")
    print("  the auditor is always right — it's that the disagreement set is where the bad runs hide,")
    print("  and re-reading it is far cheaper than re-reading every trajectory.")
    print("\nDone. Method only; synthetic data only; no proprietary detail.")


def main():
    ap = argparse.ArgumentParser(
        description="Agent-trajectory auditor method demo; optionally gate a batch of runs on its bad-run rate.")
    ap.add_argument("--gate", action="store_true",
                    help="exit non-zero if the flagged bad-run rate exceeds the bar (drops into a training-data / CI gate)")
    ap.add_argument("--max-bad-rate", type=float, default=0.45,
                    help="gate: max share of a batch the auditor may flag as bad before the build fails (default 0.45)")
    ap.add_argument("--quiet", action="store_true", help="suppress the narrative; print only the GATE verdict")
    args = ap.parse_args()

    items = build_corpus()
    for it in items:
        audit(it)

    if not args.quiet:
        print(__doc__)
        _report(items)

    if args.gate:
        bad = sum(it.predicted != "GENUINE" for it in items)
        rate = bad / len(items)
        if rate > args.max_bad_rate:
            print(f"\nGATE: FAIL — flagged bad-run rate {rate:.0%} > max {args.max_bad_rate:.0%}"
                  f"  → blocking the batch from training.")
            sys.exit(1)
        print(f"\nGATE: PASS — flagged bad-run rate {rate:.0%} ≤ max {args.max_bad_rate:.0%}.")
        sys.exit(0)


if __name__ == "__main__":
    main()
