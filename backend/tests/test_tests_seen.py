from app.info import SessionInfo
from app.tests_seen import is_test_command, parse


def test_spots_test_commands():
    yes = ["pytest -q", "cd web && npm test", "uv run pytest tests/", "npx vitest run", "go test ./...",
           "cargo test", "pnpm run test:unit", "make test", "bundle exec rspec", "python -m unittest", "bun test",
           "cat > t.py <<'EOF'\nx = 1\nEOF\nmake test 2>&1 | tail -15"]
    no = ["ls tests/", "cat pytest.ini", "npm install", "git commit -m 'add tests'", "grep -r jestConfig .", "make check",
          "cat > t.py <<'EOF'\nyes = ['npm test', 'go test ./...']\nEOF", "echo \"run pytest later\""]
    assert all(is_test_command(c) for c in yes), [c for c in yes if not is_test_command(c)]
    assert not any(is_test_command(c) for c in no), [c for c in no if is_test_command(c)]


def test_parses_runners():
    py = parse("FAILED tests/test_a.py::test_x - AssertionError\n==== 1 failed, 23 passed, 2 skipped in 1.31s ====")
    assert (py["framework"], py["passed"], py["failed"], py["skipped"]) == ("pytest", 23, 1, 2)
    assert py["failures"] == ["tests/test_a.py::test_x"]
    assert parse("24 passed in 0.51s")["passed"] == 24
    jest = parse("  ● Cart › adds item\n\nTests:       1 failed, 2 skipped, 40 passed, 43 total")
    assert (jest["passed"], jest["failed"], jest["skipped"], jest["failures"]) == (40, 1, 2, ["Cart › adds item"])
    vi = parse(" FAIL  src/a.test.ts > sums\n Test Files  1 failed (3)\n      Tests  1 failed | 40 passed (41)")
    assert (vi["framework"], vi["passed"], vi["failed"]) == ("vitest", 40, 1)
    cargo = parse("test a::b ... FAILED\ntest result: FAILED. 12 passed; 1 failed; 2 ignored; 0 measured")
    assert (cargo["passed"], cargo["failed"], cargo["skipped"], cargo["failures"]) == (12, 1, 2, ["a::b"])
    go = parse("--- FAIL: TestSum (0.00s)\nFAIL\nFAIL\texample.com/m\t0.01s\nok  \texample.com/n\t0.02s")
    assert (go["framework"], go["failed"], go["failures"]) == ("go", 1, ["TestSum"])
    assert parse("42 examples, 1 failure, 2 pending")["passed"] == 39
    ut = parse("Ran 12 tests in 0.1s\n\nFAILED (failures=1, errors=1)")
    assert (ut["framework"], ut["passed"], ut["failed"]) == ("unittest", 10, 2)
    assert parse("nothing useful here")["framework"] is None


def test_session_info_tracks_a_run():
    i = SessionInfo()
    i.tool("Bash", {"command": "cd /x && pytest -q"}, "t1", 100.0)
    assert i.to_dict()["tests"]["running"]["cmd"] == "pytest -q"
    i.tool("Bash", {"command": "cd web && npm ci && npm test 2>&1 | tail -20"}, "t0", 99.0)
    assert i.tests.pending["t0"]["cmd"] == "npm test 2>&1 | tail -20"
    i.tests.pending.pop("t0")
    i.result("t1", 105.0, "FAILED tests/test_a.py::test_x\n1 failed, 3 passed in 0.2s", True)
    t = i.to_dict()["tests"]
    assert t["running"] is None and t["runs"] == 1
    assert (t["last"]["status"], t["last"]["passed"], t["last"]["failed"]) == ("failed", 3, 1)
    i.tool("Bash", {"command": "npm test"}, "t2", 110.0)
    i.result("t2", 111.0, "Exit code 1\nsomething broke", True)
    assert i.tests.all()[0]["status"] == "failed"
    i.tool("Bash", {"command": "ls"}, "t3", 112.0)
    i.result("t3", 113.0, "a b c", False)
    assert len(i.tests.all()) == 2
