"""The confidence gate: what's rejected, what needs an explicit confirm, and the Comms chips."""
from app.comms import Plan, below_floor, clear_enough, confirm_heard, is_confirm, needs_explicit, reason


def plan(action, mode="command"):
    return Plan(mode, action, None, "", lambda: None)


def test_floor_rejects_low_confidence_speech():
    assert below_floor("mic", 0.39)
    assert not below_floor("mic", 0.40)
    assert not below_floor("mic", None)        # an engine that reports no confidence isn't gated here
    assert not below_floor("typed", None)


def test_tiered_verbs_need_strict_confidence():
    assert needs_explicit(plan("approve"), "mic", 0.6)
    assert not needs_explicit(plan("approve"), "mic", 0.75)
    assert needs_explicit(plan("reply", mode="reply"), "mic", 0.69)   # anything sent to a session
    assert not needs_explicit(plan("status"), "mic", 0.5)             # a read-only command isn't tiered
    assert not needs_explicit(plan("approve"), "panel", None)         # typed / clicked input


def test_clear_enough_typed_vs_spoken():
    assert clear_enough("panel", None)
    assert not clear_enough("mic", None)
    assert clear_enough("mic", 0.7)


def test_confirm_words():
    for w in ("Confirm.", "send it", "Yes, send it", "confirm it"):
        assert is_confirm(w), w
    assert not is_confirm("thanks")
    # unmistakable confirms pass at the floor; a bare "yes" still needs strict confidence
    assert confirm_heard("Confirm.", "mic", 0.45)
    assert not confirm_heard("Yes.", "mic", 0.52)
    assert confirm_heard("Yes.", "mic", 0.8)


def test_reason_chips():
    assert reason({"status": "rejected", "outcome": "rejected: low confidence (12% < 40%)"}) == "rejected-low-conf"
    assert reason({"status": "rejected", "outcome": "rejected: unintelligible"}) == "rejected-unintelligible"
    assert reason({"status": "ignored", "outcome": "ignored while speaking"}) == "ignored-speaking"
    assert reason({"status": "ignored", "outcome": "session muted"}) == "ignored-muted"
    assert reason({"status": "dispatched", "outcome": "ok"}) == "dispatched"
