from app.info import context_window


def test_context_window():
    assert context_window("claude-opus-4[1m]", 10) == 1_000_000
    assert context_window("claude-sonnet-4-5", 250_000) == 1_000_000     # already past 200k: must be the 1M variant
    assert context_window("claude-haiku-4-5", 10) in (200_000, 1_000_000)
