# PRE-REGISTERED MAPPING — agent-trajectory-demo vs TRAIL (arXiv 2505.08638)

Registered: 2026-07-18, BEFORE any detector was run on any TRAIL trace.
Data source: github.com/patronus-ai/trail-benchmark @ main (MIT), `benchmarking/data/` +
`benchmarking/processed_annotations_*/`. 148 traces (117 GAIA / 31 SWE Bench), 841
span-level errors (verified by count). The HF copy (PatronusAI/TRAIL) is gated; the
authors' own GitHub copy is public and is what we use. Data stays local, is not
committed anywhere, and is not redistributed.

## TRAIL schema (as found)

- Trace file: `{trace_id, spans}`; spans nested via `child_spans` (OpenTelemetry /
  OpenInference). Span kinds seen: AGENT (`CodeAgent.run` 147/148, plus 60 sub-agent
  `ToolCallingAgent.run`), LLM (`LiteLLMModel.__call__`, o3-mini), CHAIN (`Step N`,
  `output.value` = "Execution logs: ..."), TOOL (web_search, visit_page, final_answer, ...;
  `output.value` present except final_answer).
- Annotation file: `{errors: [{category, location(span_id), evidence, description,
  impact}], scores: [...]}`. Some files have trailing commas → lenient parse. Category
  strings have minor spelling variants → normalized with the benchmark's own
  `normalize_category` logic against its canonical 21-category list.

## Category mapping (by definition, not by score)

Demo detector definitions (README): FABRICATED = "concludes on a value that never appeared
in any tool result — reasoned on a fabricated tool output"; LOOP = "repeats the same action
with no new information"; VACUOUS = "fluent, on-template reasoning that never touches a
concrete result".

| demo verdict | TRAIL ground-truth categories | rationale |
|---|---|---|
| FABRICATED | **Tool-related** (53), **Language-only** (53 incl. spelling variants) | Both are TRAIL's Hallucination subtypes: "fabricating tool outputs/capabilities" and fabricated/ungrounded statements. Tool-related is the tightest definitional twin; Language-only is fabricated content not tied to a tool. Both jointly = primary GT; **recall will additionally be reported per subcategory** so the tighter mapping is visible. |
| LOOP | **Resource Abuse** (57) | "Called the tool excessively" = repeated same action, no new information. |
| VACUOUS | **(none — no TRAIL category matches)** | TRAIL has no substance/vacuity category. VACUOUS is untestable for recall on TRAIL; VACUOUS predictions count only against precision. This is reported as a coverage finding, not hidden. |

Judgment calls, declared now: `Timeout Issues` (2) and `Rate Limiting` (0 found) are NOT
counted as LOOP — they are infrastructure-side signatures, not a repeated-action pattern,
even though loops can cause them. `Incorrect Memory Usage` (2) is not mapped anywhere.

**Out of scope** (the demo does not attempt them): Formatting Errors, Instruction
Non-compliance, Goal Deviation, Task Orchestration, Tool Selection Errors, Context Handling
Failures, Poor Information Retrieval, Incorrect Problem Identification, Tool Output
Misinterpretation, Environment Setup Errors, Resource Not Found, Authentication Errors,
Tool Definition Issues, Resource Exhaustion, Service Errors, Timeout Issues, Incorrect
Memory Usage, Rate Limiting.

**Coverage: in-scope errors = 53 + 53 + 57 = 163 / 841 = 19.4%.** The demo attempts fewer
than one fifth of TRAIL's errors; the remaining 80.6% are outside its three failure modes.

## Granularity (declared choice)

**Trace-level detection, not span-level localization.** The demo's `audit()` returns one
verdict per trajectory; it has no span-id output, so TRAIL's span-localization metric is
outside its design. GT(category) for a trace = the trace contains >=1 error whose
normalized category is in the mapping above. This makes our numbers NOT directly
comparable to TRAIL's headline joint accuracy (~11-18% for frontier LLM judges, which is
span + category); we say so in the report.

- **Primary metric**: the unmodified `audit()` single verdict (priority FABRICATED > LOOP >
  VACUOUS, as shipped). Per mapped category: precision = P(GT_cat | verdict=cat), recall =
  P(verdict=cat | GT_cat), with Wilson 95% CIs.
- **Secondary (sensitivity, declared now)**: per-axis firing computed independently with the
  module's own primitives and identical thresholds, to expose router shadowing (a trace
  that both fabricates and loops gets only FABRICATED from the router). Reported
  separately, clearly labeled.
- Binary "bad-run detection" is reported but flagged as near-vacuous: ~144/148 traces
  contain at least one error of some category, so the binary base rate is ~97%.

## Adapter conversion rules (frozen before scoring)

Flatten spans depth-first in stored order. Then:
- task   = first AGENT span `input.value` JSON `task` field (fallback: raw value; fallback:
  first LLM user message).
- think  = each LLM span's output message content (`llm.output_messages.*.message.content`,
  fallback `output.value`).
- call   = TOOL span -> `(tool.name, input.value)`; CHAIN Step span -> `("python", code)`
  where code = first fenced code block in that step's child-LLM output, else that LLM
  output text in full.
- result = TOOL span `output.value` when present; CHAIN Step span `output.value`.
- answer = first AGENT span `output.value` (142/148); fallback: last LLM output content
  (6/148).

## Expected failure modes (predictions, written before scoring)

1. **Echo problem**: smolagents execution logs echo agent-authored constants back as
   observations, so a fabricated value can appear "grounded" -> FABRICATED recall down.
2. **Numeric noise**: real prose answers contain many 3-6 digit numbers (years, counts)
   that never appear verbatim in tool results -> FABRICATED fires broadly, precision down.
3. **Value-dense traces**: real reasoning text almost always references some grounded
   token -> VACUOUS ~never fires.

Honest numbers are the deliverable. Low numbers reported straight are a success.
