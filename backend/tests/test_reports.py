"""Error dedupe: identical errors collapse, the third escalates to a decision."""
import time

from app.reports import err_sig, reports


def test_err_sig_blurs_ids_numbers_and_paths():
    a = err_sig("API Error: Connection dropped (ECONNRESET) req_011CUxYz9")
    b = err_sig("API Error: Connection dropped (ECONNRESET) req_0abc123")
    assert a == b
    assert err_sig("failed at /Users/someone/x.py line 12") == err_sig("failed at /home/other/y.py line 99")
    assert err_sig("API Error: 529 Overloaded") != err_sig("API Error: Connection dropped")


async def test_three_identical_errors_collapse_and_escalate():
    sid = f"test-{time.time()}"
    seen = []
    for i in range(3):
        r = await reports.error(project="demo", summary="error, API Error: 529 Overloaded",
                                body=f"API Error: 529 Overloaded (request req_{i}x9)", session_id=sid, key=f"e{i}:{sid}")
        seen.append(r)
    assert len({r["id"] for r in seen}) == 1
    assert seen[-1]["repeat"] == 3
    assert seen[-1]["type"] == "decision_needed"
    assert "failed the same way 3 times" in seen[-1]["summary"]
