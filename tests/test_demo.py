"""Tests for the agent-trajectory auditor demo. Run: pytest"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_trajectory_demo as demo  # noqa: E402


def corpus():
    demo.RNG.seed(7)  # deterministic build regardless of test order
    items = demo.build_corpus()
    for it in items:
        demo.audit(it)
    return items


def fresh(builder):
    demo.RNG.seed(1)
    it = builder(0)
    return demo.audit(it)


def test_corpus_builds_with_all_modes():
    items = corpus()
    assert len(items) == 41
    modes = {i.failure_mode for i in items}
    assert modes == {None, "FABRICATED", "LOOP", "VACUOUS"}


def test_each_failure_mode_routes_to_its_verdict():
    assert fresh(demo.make_fabricated).predicted == "FABRICATED"
    assert fresh(demo.make_loop).predicted == "LOOP"
    assert fresh(demo.make_vacuous).predicted == "VACUOUS"
    assert fresh(demo.make_genuine).predicted == "GENUINE"


def test_fabrication_is_grounded_in_absence_from_results():
    it = fresh(demo.make_fabricated)
    assert "fabricated" in " ".join(it.fired)


def test_reformatted_value_is_not_a_false_fabrication():
    # answer prefixes the raw digit result (INC-12345 vs 12345) — must stay GENUINE
    assert fresh(demo.make_reformatted_genuine).predicted == "GENUINE"


def test_detection_is_strong_but_not_perfect():
    p, r, f1 = demo.prf1(corpus())
    assert 0.85 <= f1 < 1.0, "honest demo: strong but not a suspicious 1.000"
    assert p < 1.0 and r < 1.0, "the planted hard cases should cost some precision and recall"


def test_clear_modes_route_fully():
    route = demo.routing(corpus())
    assert route["FABRICATED"] == (6, 6)
    assert route["LOOP"] == (6, 6)
    hit, tot = route["VACUOUS"]
    assert hit == tot - 1  # the parrot-vacuous is the one honest miss


def test_disagreement_queue_recovers_every_planted_error():
    items = corpus()
    planted = [i for i in items if i.failure_mode][:4]
    for it in planted:
        it.given_label = "GENUINE"  # mark a failed run "completed"
    disagreements = [i for i in items if i.predicted != i.given_label]
    recovered = [i for i in disagreements if i in planted]
    assert len(recovered) == len(planted) == 4
    # and the queue stays small — the whole point is cheap re-reading
    assert len(disagreements) < len(items) // 3
