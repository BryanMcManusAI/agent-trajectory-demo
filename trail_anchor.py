#!/usr/bin/env python3
"""
TRAIL anchor — run the UNMODIFIED agent-trajectory-demo detectors on the TRAIL benchmark
========================================================================================

TRAIL (Patronus AI, arXiv 2505.08638): 148 real agent execution traces (117 GAIA / 31
SWE Bench, smolagents + o3-mini, OpenInference spans) with 841 expert span-level error
annotations across a 21-category taxonomy.

This adapter converts each TRAIL trace into the demo's (kind, payload) step format and
runs `audit()` from agent_trajectory_demo.py — no detector code is modified and no
thresholds are changed. The category mapping, granularity choice, and expected failure
modes were PRE-REGISTERED before scoring (see MAPPING.md, committed alongside this file):

  demo FABRICATED  <- TRAIL {Tool-related, Language-only}   (Hallucination subtypes)
  demo LOOP        <- TRAIL {Resource Abuse}
  demo VACUOUS     <- (no TRAIL category matches; untestable for recall)
  everything else  -> out of scope (~80.6% of TRAIL's errors)

Granularity: TRACE-level detection (does this trace contain that failure?), because the
demo emits one verdict per trajectory and has no span output. NOT comparable to TRAIL's
span-level joint-accuracy metric (~11-18% for frontier LLM judges).

Honest numbers are the point. Usage:
    python3 trail_anchor.py --data /path/to/trail-benchmark/benchmarking
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

import agent_trajectory_demo as demo  # the unmodified detectors

# ── TRAIL's own canonical category list + normalizer (ported from the benchmark's
#    calculate_scores.py, MIT) ─────────────────────────────────────────────────────────
ALL_CATEGORIES = [
    "Language-only", "Tool-related", "Poor Information Retrieval", "Incorrect Memory Usage",
    "Tool Output Misinterpretation", "Incorrect Problem Identification", "Tool Selection Errors",
    "Formatting Errors", "Instruction Non-compliance", "Tool Definition Issues",
    "Environment Setup Errors", "Rate Limiting", "Authentication Errors", "Service Errors",
    "Resource Not Found", "Resource Exhaustion", "Timeout Issues", "Context Handling Failures",
    "Resource Abuse", "Goal Deviation", "Task Orchestration",
]


def normalize_category(category: str) -> str:
    if not category:
        return ""
    cat = category.lower().strip()
    flat = cat.replace(" ", "")
    for std in ALL_CATEGORIES:
        if cat == std.lower() or flat == std.lower().replace(" ", ""):
            return std
    for std in ALL_CATEGORIES:
        if flat in std.lower().replace(" ", ""):
            return std
    return category


# Pre-registered mapping (MAPPING.md) — ground-truth categories per demo verdict.
GT_MAP = {
    "FABRICATED": {"Tool-related", "Language-only"},
    "LOOP": {"Resource Abuse"},
    "VACUOUS": set(),  # no TRAIL twin — declared untestable
}

# ── loading ────────────────────────────────────────────────────────────────────────────


def load_lenient(path):
    """Some TRAIL annotation files carry trailing commas; strip them if strict parse fails."""
    text = open(path, encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def flat_spans(span):
    yield span
    for child in span.get("child_spans", []):
        yield from flat_spans(child)


def attr(span, key, default=None):
    return span.get("span_attributes", {}).get(key, default)


def llm_output_text(span):
    """Concatenated assistant output message content of an LLM span."""
    attrs = span.get("span_attributes", {})
    parts = []
    i = 0
    while f"llm.output_messages.{i}.message.content" in attrs:
        parts.append(str(attrs[f"llm.output_messages.{i}.message.content"]))
        i += 1
    if not parts and "output.value" in attrs:
        parts.append(str(attrs["output.value"]))
    return "\n".join(parts)


CODE_FENCE = re.compile(r"```(?:py|python)?\s*\n(.*?)```", re.S)


def convert(trace: dict, idx: int):
    """TRAIL trace -> demo Traj, per the frozen adapter rules in MAPPING.md."""
    spans = [s for root in trace["spans"] for s in flat_spans(root)]

    # task
    task = ""
    agent_spans = [s for s in spans if attr(s, "openinference.span.kind") == "AGENT"]
    if agent_spans:
        raw = attr(agent_spans[0], "input.value", "")
        try:
            task = str(json.loads(raw).get("task", raw))
        except Exception:
            task = str(raw)
    if not task:
        for s in spans:
            if attr(s, "openinference.span.kind") == "LLM":
                task = str(attr(s, "llm.input_messages.0.message.content", ""))
                break

    steps = []
    for s in spans:
        kind = attr(s, "openinference.span.kind")
        if kind == "LLM":
            text = llm_output_text(s)
            if text:
                steps.append(("think", text))
        elif kind == "TOOL":
            name = str(attr(s, "tool.name", s.get("span_name", "tool")))
            args = str(attr(s, "input.value", ""))
            steps.append(("call", (name, args)))
            out = attr(s, "output.value")
            if out is not None:
                steps.append(("result", str(out)))
        elif kind == "CHAIN":
            # a Step span: the action is the code its child LLM emitted; the observation
            # is the step's output.value (execution logs)
            child_llm = [c for c in s.get("child_spans", [])
                         if attr(c, "openinference.span.kind") == "LLM"]
            if child_llm:
                text = llm_output_text(child_llm[0])
                m = CODE_FENCE.search(text)
                steps.append(("call", ("python", (m.group(1) if m else text).strip())))
            out = attr(s, "output.value")
            if out is not None:
                steps.append(("result", str(out)))

    # answer
    answer = ""
    if agent_spans and attr(agent_spans[0], "output.value") is not None:
        answer = str(attr(agent_spans[0], "output.value"))
    else:
        for s in reversed(spans):
            if attr(s, "openinference.span.kind") == "LLM":
                text = llm_output_text(s)
                if text:
                    answer = text
                    break
    steps.append(("answer", answer))

    return demo.Traj(idx, task, steps, None)


# ── secondary sensitivity: each axis checked independently (same primitives, same
#    thresholds as audit(); this only removes the router's FABRICATED>LOOP>VACUOUS
#    priority so shadowing is visible). Declared in MAPPING.md before scoring. ─────────


def axis_fires(it):
    grounded = demo.grounded_cores(it)
    ans = " ".join(demo._steps(it, "answer"))
    ans_tokens = [m.group(1) for m in demo.TOKEN.finditer(ans)]
    fab = any(demo._digits(t) and demo._digits(t) not in grounded for t in ans_tokens)

    calls = demo._steps(it, "call")
    results = demo._steps(it, "result")
    sigs = [f"{tool}|{args}" for tool, args in calls]
    repeated = max((sigs.count(s) for s in set(sigs)), default=0)
    stuck = len(set(results)) < len(results) and len(results) > 1
    loop = repeated >= 3 or (repeated >= 2 and stuck)

    concl = demo.conclusion_text(it)
    references_value = any(demo._digits(m.group(1)) in grounded
                           for m in demo.TOKEN.finditer(concl))
    buzz = len(demo.BUZZ.findall(concl))
    vac = (not references_value) and buzz >= 2
    return {"FABRICATED": fab, "LOOP": loop, "VACUOUS": vac}


# ── stats ──────────────────────────────────────────────────────────────────────────────


def wilson(k, n, z=1.959964):
    """Wilson 95% CI for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def fmt_ci(k, n):
    p, lo, hi = wilson(k, n)
    return f"{k}/{n} = {p:.3f}  [95% CI {lo:.3f}-{hi:.3f}]"


# ── main ───────────────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data", required=True,
                    help="path to trail-benchmark/benchmarking (contains data/ and processed_annotations_*)")
    ap.add_argument("--json-out", default="", help="optional path to dump per-trace results as JSON")
    ap.add_argument("--limit", type=int, default=0, help="only first N traces per source (smoke test)")
    args = ap.parse_args()

    sources = [("GAIA", os.path.join(args.data, "data", "GAIA"),
                os.path.join(args.data, "processed_annotations_gaia")),
               ("SWE Bench", os.path.join(args.data, "data", "SWE Bench"),
                os.path.join(args.data, "processed_annotations_swe_bench"))]

    rows = []
    idx = 0
    total_errors = 0
    in_scope_errors = 0
    subcat_trace_ids = {"Tool-related": set(), "Language-only": set()}
    for source, tdir, adir in sources:
        files = sorted(glob.glob(os.path.join(tdir, "*.json")))
        if args.limit:
            files = files[: args.limit]
        for tf in files:
            tid = os.path.splitext(os.path.basename(tf))[0]
            ann_path = os.path.join(adir, tid + ".json")
            if not os.path.exists(ann_path):
                print(f"  ! no annotation for {tid}, skipping", file=sys.stderr)
                continue
            trace = json.load(open(tf, encoding="utf-8"))
            ann = load_lenient(ann_path)
            cats = [normalize_category(e.get("category", "")) for e in ann.get("errors", [])]
            total_errors += len(cats)
            in_scope_errors += sum(c in GT_MAP["FABRICATED"] | GT_MAP["LOOP"] for c in cats)
            for sub in subcat_trace_ids:
                if sub in cats:
                    subcat_trace_ids[sub].add(tid)

            it = convert(trace, idx)
            demo.audit(it)                     # the unmodified detector
            axes = axis_fires(it)              # sensitivity decomposition
            rows.append({
                "trace_id": tid, "source": source, "verdict": it.predicted,
                "fired": it.fired, "axes": axes, "gt_categories": sorted(set(cats)),
                "gt": {v: bool(set(cats) & gt) for v, gt in GT_MAP.items()},
                "n_steps": len(it.steps),
                "errors": [{"category": normalize_category(e.get("category", "")),
                            "evidence": str(e.get("evidence", ""))[:160]}
                           for e in ann.get("errors", [])],
            })
            idx += 1

    n = len(rows)
    print("=" * 74)
    print("TRAIL anchor — unmodified agent-trajectory-demo detectors on real traces")
    print("=" * 74)
    print(f"traces scored: {n}   annotated errors seen: {total_errors}")
    print(f"errors in categories the demo even attempts: {in_scope_errors}/{total_errors}"
          f" = {in_scope_errors / max(1, total_errors):.1%}   (pre-registered mapping)")
    print()

    verd = {}
    for r in rows:
        verd[r["verdict"]] = verd.get(r["verdict"], 0) + 1
    print("verdict distribution:", "  ".join(f"{k} {v}" for k, v in sorted(verd.items())))
    print()

    print("PRIMARY — trace-level, unmodified audit() single verdict")
    print("-" * 74)
    for v in ("FABRICATED", "LOOP"):
        gt_pos = [r for r in rows if r["gt"][v]]
        pred_pos = [r for r in rows if r["verdict"] == v]
        tp = [r for r in pred_pos if r["gt"][v]]
        print(f"  {v}  (GT prevalence {len(gt_pos)}/{n} traces)")
        print(f"     recall    {fmt_ci(len(tp), len(gt_pos))}")
        print(f"     precision {fmt_ci(len(tp), len(pred_pos))}")
    for sub, tids in subcat_trace_ids.items():
        hit = sum(1 for r in rows if r["trace_id"] in tids and r["verdict"] == "FABRICATED")
        print(f"  FABRICATED recall on {sub} subcategory alone: {fmt_ci(hit, len(tids))}")
    vac_pred = sum(r["verdict"] == "VACUOUS" for r in rows)
    print(f"  VACUOUS  no TRAIL ground-truth category exists (untestable); predicted on "
          f"{vac_pred}/{n} traces")
    print()

    print("SECONDARY — per-axis firing (router priority removed; same thresholds)")
    print("-" * 74)
    for v in ("FABRICATED", "LOOP"):
        gt_pos = [r for r in rows if r["gt"][v]]
        pred_pos = [r for r in rows if r["axes"][v]]
        tp = [r for r in pred_pos if r["gt"][v]]
        print(f"  {v} axis  recall    {fmt_ci(len(tp), len(gt_pos))}")
        print(f"  {v} axis  precision {fmt_ci(len(tp), len(pred_pos))}")
    print()

    bad_gt = [r for r in rows if r["gt_categories"]]
    bad_pred = [r for r in rows if r["verdict"] != "GENUINE"]
    tp = [r for r in bad_pred if r["gt_categories"]]
    print("BINARY bad-run detection (near-vacuous: base rate "
          f"{len(bad_gt)}/{n} = {len(bad_gt) / max(1, n):.0%})")
    print(f"  recall {fmt_ci(len(tp), len(bad_gt))}   precision {fmt_ci(len(tp), len(bad_pred))}")
    print()

    print("examples")
    print("-" * 74)
    shown = 0
    for r in rows:
        if r["verdict"] == "FABRICATED" and r["gt"]["FABRICATED"] and shown < 2:
            print(f"  HIT  {r['trace_id'][:8]} ({r['source']}): {r['fired'][0][:90]}")
            shown += 1
    shown = 0
    for r in rows:
        if r["gt"]["FABRICATED"] and r["verdict"] != "FABRICATED" and shown < 2:
            ev = next((e["evidence"] for e in r["errors"]
                       if e["category"] in GT_MAP["FABRICATED"]), "")[:90]
            print(f"  MISS {r['trace_id'][:8]} ({r['source']}): verdict={r['verdict']}; GT evidence: {ev}")
            shown += 1
    shown = 0
    for r in rows:
        if r["gt"]["LOOP"] and r["verdict"] != "LOOP" and shown < 1:
            print(f"  MISS {r['trace_id'][:8]} ({r['source']}): GT Resource Abuse; verdict={r['verdict']}"
                  f" (axis LOOP fired: {r['axes']['LOOP']})")
            shown += 1

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
        print(f"\nper-trace results -> {args.json_out}")


if __name__ == "__main__":
    main()
