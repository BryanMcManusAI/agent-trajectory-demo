# Agent-Trajectory Auditor — Method Demo (synthetic data only)

[![CI](https://github.com/BryanMcManusAI/agent-trajectory-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/BryanMcManusAI/agent-trajectory-demo/actions/workflows/ci.yml)
&nbsp;·&nbsp; stdlib only &nbsp;·&nbsp; no install &nbsp;·&nbsp; no API key &nbsp;·&nbsp; deterministic

The same instrument that audits rubric annotations reads **agent trajectories** the same way,
because the failure shape is the same: a run that *performs* reasoning without doing any. This is a
runnable demonstration of that method — score the substance and faithfulness of an agent's run, not
the fact that it produced a fluent answer, and treat every "the run completed" label as a hypothesis
to re-check.

```bash
python3 agent_trajectory_demo.py     # standard library only, one command
```

## The three failure modes it routes

Each maps directly onto a data-quality failure the annotation-side detector already catches — the
correspondence is the point:

| verdict | the agent failure | annotation-side twin |
|---|---|---|
| **FABRICATED** | the run concludes on a value that never appeared in any tool result — it reasoned on a fabricated tool output | a claim the substance can't support |
| **LOOP** | the agent repeats the same action with no new information — each step looks fine, the set goes nowhere | a near-duplicate batch |
| **VACUOUS** | fluent, on-template reasoning that never touches a concrete result — the form of reasoning, none of the substance | a VACUOUS annotation |

The auditor decides each from the trajectory alone, on transparent axes — **grounding** (does the
conclusion trace to a real tool result?), **progress** (does the same action keep returning nothing
new?), **substance** (does the reasoning name a concrete result, or lean on template filler?).

## What it prints

```
41 fabricated trajectories  |  bad-run detection  precision 0.900  recall 0.947  F1 0.923
Routing:  FABRICATED 6/6   LOOP 6/6   VACUOUS 6/7
```

Not a suspicious 1.000 — the corpus plants hard cases on purpose. A **confirm-and-proceed re-query**
(query, re-query to double-check, proceed) is indistinguishable from a stuck loop on progress signal
alone, so it costs precision; a **value-parroting** run that names the result but reasons about
nothing slips the substance check, so it costs recall. Those honest misses are the point of the
second step.

## Disagreement-as-signal (the core idea)

A few failed runs are marked **GENUINE** — "it produced an answer, ship it," the one-directional
leniency a real gold set drifts into. The auditor never sees the labels; it just *disagrees* with
some of them. Every planted lenient error lands in a small disagreement queue, next to the auditor's
own hard-case misses — one human pass sorts which is which, far cheaper than re-reading every run.

```
Auditor disagreed with the labels on 7 run(s):
   id 19  label=GENUINE  auditor=FABRICATED  ← planted 'completed' error
   id 20  label=GENUINE  auditor=LOOP        ← planted 'completed' error
   ...
   id 40  label=VACUOUS  auditor=GENUINE     ← auditor's own miss (re-read)
Recovered 4/4 planted label errors.
```

## Gate a batch of runs in CI

`--gate` turns the auditor into a **pass/fail check with a real exit code**, so it drops into a
training-data pipeline or pre-merge hook and *blocks a batch when too many runs are bad*:

```bash
python3 agent_trajectory_demo.py --gate --max-bad-rate 0.45 --quiet
# exits 1 if more than 45% of the batch is flagged bad
```

The synthetic batch is ~half bad *by construction* (it's a detection demo), so this fails the gate
on purpose — the illustration is a dirty batch being blocked. Point `--max-bad-rate` and the runs at
your own collection to gate real training data. No install, it's stdlib.

## Anchored on real traces (TRAIL)

Run unmodified over TRAIL (Patronus, 2025) — 148 annotated agent traces, 841 errors — with the
category mapping pre-registered before scoring (`trail_anchor.py`):

- **LOOP transfers.** Recall 0.33, precision 0.74 against annotated Resource Abuse; its five false
  positives are all loop-adjacent bad traces (post-error retries), not noise.
- **FABRICATED does not** (recall 0.02), for a mechanistic reason worth knowing: CodeAgent executors
  echo the agent's own invented values back as observations, so token-grounding cannot separate
  fabrication from grounding.
- **VACUOUS has no counterpart** in TRAIL's 21-category taxonomy — the gap it names is real — while
  over-firing on short word-answer traces.
- Coverage: these three modes address **19.4%** of TRAIL's real-world errors. The synthetic F1s above
  are teaching numbers, not evidence; this section is the evidence.

TRAIL's own result — frontier LLM judges reach ~11% at span-level error localization — is the
argument for the architecture here: cheap transparent heuristics that route to a human, not an LLM
judge that scores everything.

## What this is *not*

A **clean-room reconstruction from a publicly described approach.** No proprietary code, no internal
or real data (every trajectory is fabricated in this file), no production feature set or tuning — the
axes are illustrative and human-readable, not the real weighting. The numbers are synthetic; their
job is to show the *architecture* — audit the trajectory, not the self-report; treat label
disagreements as hypotheses — separates a real run from a fluent-looking empty one.

## Siblings

The same audit-by-disagreement method on two other surfaces:
- **[detector-demo](https://github.com/BryanMcManusAI/detector-demo)** — rubric-annotation quality (the data that grades the agents).
- **[faithfulness-demo](https://github.com/BryanMcManusAI/faithfulness-demo)** — CoT monitorability (when the reasoning trace an oversight monitor reads stops being faithful).

Part of a broader evaluation-reliability program — [bryanmcmanus.ai](https://bryanmcmanus.ai).
