"""Test runs a session made: spotted in its Bash calls, read from their output. Nothing is run by Vario.

A Bash command that looks like a test runner (pytest, vitest, jest, go test, cargo test, npm test, ...) opens a
run; its tool result closes it. The summary line of the output gives the counts and the failing test names.
Unknown output still records the run, as passed or failed by the command's exit status.
"""
import re
import time

MAX_RUNS = 20          # kept per session
OUTPUT_TAIL = 8000     # characters of output kept per run

_RUNNER = re.compile(
    r"(?:^|[\s;&|(/])(?:"
    r"pytest|py\.test|vitest|jest|mocha|ava|playwright\s+test|cypress\s+run|go\s+test|cargo\s+(?:test|nextest)|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test[\w:-]*|bun\s+test|deno\s+test|make\s+test[\w-]*|"
    r"rspec|rails\s+test|mix\s+test|phpunit|pest|swift\s+test|xcodebuild\b.*\btest|dotnet\s+test|"
    r"(?:\./)?gradlew?\s+(?:\S+\s+)*test|mvn\s+(?:\S+\s+)*test|tox|nox|ctest|unittest|nose2|bats"
    r")(?=$|[\s;&|)])")


def _code_only(cmd: str) -> str:
    """The command without heredoc bodies or quoted strings, so text that merely mentions a runner doesn't count."""
    c = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?(.*?)\n.*?\n\1\b", r"\2", cmd, flags=re.S)
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", c)


def is_test_command(cmd: str) -> bool:
    return bool(cmd) and bool(_RUNNER.search(_code_only(cmd)))


def _n(m, g) -> int:
    v = m.group(g) if m else None
    return int(v) if v else 0


def parse(text: str) -> dict:
    """Counts and failing names from a test runner's output. Missing counts stay None."""
    out = {"framework": None, "passed": None, "failed": None, "skipped": None, "failures": []}
    t = text or ""

    def counts(fw, passed, failed, skipped=0):
        out.update(framework=fw, passed=passed, failed=failed, skipped=skipped)

    # pytest: "== 2 failed, 21 passed, 1 skipped in 1.2s ==" (also -q: "21 passed in 0.5s")
    for line in reversed(t.splitlines()):
        if re.search(r"\b\d+ (passed|failed|error|errors)\b", line) and re.search(r"\bin [\d.]+s\b", line):
            g = lambda w, line=line: sum(int(x) for x in re.findall(rf"(\d+) {w}\b", line))
            counts("pytest", g("passed"), g("failed") + g("errors?"), g("skipped") + g("xfailed"))
            out["failures"] = re.findall(r"(?m)^(?:FAILED|ERROR) (\S+)", t)
            return out
    # jest: "Tests:       1 failed, 2 skipped, 40 passed, 43 total"
    m = re.search(r"(?m)^Tests:\s+(.*\d+ total)", t)
    if m:
        s = m.group(1)
        g = lambda w: int((re.search(rf"(\d+) {w}", s) or [0, 0])[1])
        counts("jest", g("passed"), g("failed"), g("skipped") + g("todo"))
        out["failures"] = re.findall(r"(?m)^\s*● (.+?)\s*$", t)
        return out
    # vitest: " Tests  2 failed | 40 passed (42)"
    m = re.search(r"(?m)^\s*Tests\s+(.*)\((\d+)\)\s*$", t)
    if m:
        s = m.group(1)
        g = lambda w: int((re.search(rf"(\d+) {w}", s) or [0, 0])[1])
        counts("vitest", g("passed"), g("failed"), g("skipped") + g("todo"))
        out["failures"] = re.findall(r"(?m)^\s*(?:FAIL|×|✗)\s+(.+?)\s*$", t)
        return out
    # cargo: "test result: FAILED. 12 passed; 1 failed; 2 ignored" (one per test binary)
    rs = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", t)
    if rs:
        counts("cargo", *(sum(int(r[i]) for r in rs) for i in range(3)))
        out["failures"] = re.findall(r"(?m)^test (\S+) \.\.\. FAILED", t)
        return out
    # go: "ok  pkg 0.1s" / "FAIL pkg" lines; "--- FAIL: TestX"
    oks, fails = re.findall(r"(?m)^ok\s+\S+", t), re.findall(r"(?m)^FAIL\s+\S+\s", t)
    if oks or fails or re.search(r"(?m)^--- (PASS|FAIL):", t):
        named_fail = re.findall(r"(?m)^\s*--- FAIL: (\S+)", t)
        named_pass = re.findall(r"(?m)^\s*--- PASS: ", t)
        counts("go", len(named_pass) or len(oks), len(named_fail) or len(fails),
               len(re.findall(r"(?m)^\s*--- SKIP: ", t)))
        out["failures"] = named_fail or [f.split()[1] for f in fails]
        return out
    # rspec: "42 examples, 1 failure, 2 pending"
    m = re.search(r"(\d+) examples?, (\d+) failures?(?:, (\d+) pending)?", t)
    if m:
        ex, f, p = _n(m, 1), _n(m, 2), _n(m, 3)
        counts("rspec", ex - f - p, f, p)
        out["failures"] = re.findall(r"(?m)^rspec \S+ # (.+)$", t)
        return out
    # mocha: "40 passing", "2 failing", "1 pending"
    m = re.search(r"(?m)^\s*(\d+) passing", t)
    if m:
        counts("mocha", _n(m, 1), _n(re.search(r"(?m)^\s*(\d+) failing", t), 1),
               _n(re.search(r"(?m)^\s*(\d+) pending", t), 1))
        out["failures"] = re.findall(r"(?m)^\s*\d+\) (.+)$", t)
        return out
    # bun: " 40 pass" / " 2 fail"; deno: "ok | 40 passed | 2 failed"
    m = re.search(r"(?m)^\s*(\d+) pass\s*$", t)
    if m:
        counts("bun", _n(m, 1), _n(re.search(r"(?m)^\s*(\d+) fail\s*$", t), 1),
               _n(re.search(r"(?m)^\s*(\d+) skip\s*$", t), 1))
        out["failures"] = re.findall(r"(?m)^\(fail\) (.+?)(?: \[[\d.]+m?s\])?$", t)
        return out
    m = re.search(r"(?:ok|FAILED) \| (\d+) passed(?: \((\d+) steps?\))? \| (\d+) failed(?: \| (\d+) ignored)?", t)
    if m:
        counts("deno", _n(m, 1), _n(m, 3), _n(m, 4))
        return out
    # XCTest / swift test: "Executed 12 tests, with 1 failure"
    m = re.findall(r"Executed (\d+) tests?, with (\d+) failures?", t)
    if m:
        total, f = int(m[-1][0]), int(m[-1][1])
        counts("xctest", total - f, f)
        out["failures"] = re.findall(r"error: -\[(\S+ \S+)\]", t)
        return out
    # dotnet: "Failed!  - Failed: 1, Passed: 40, Skipped: 0, Total: 41"
    m = re.search(r"Failed:\s+(\d+), Passed:\s+(\d+), Skipped:\s+(\d+)", t)
    if m:
        counts("dotnet", _n(m, 2), _n(m, 1), _n(m, 3))
        return out
    # phpunit: "OK (40 tests, 80 assertions)" / "Tests: 40, Assertions: 80, Failures: 1, Errors: 1."
    m = re.search(r"OK \((\d+) tests?", t)
    if m:
        counts("phpunit", _n(m, 1), 0)
        return out
    m = re.search(r"Tests: (\d+), Assertions: \d+(?:, Errors: (\d+))?(?:, Failures: (\d+))?(?:, Skipped: (\d+))?", t)
    if m:
        f = _n(m, 2) + _n(m, 3)
        counts("phpunit", _n(m, 1) - f - _n(m, 4), f, _n(m, 4))
        return out
    # unittest: "Ran 12 tests in 0.1s" then "OK" or "FAILED (failures=1, errors=1)"
    m = re.search(r"Ran (\d+) tests? in", t)
    if m:
        bad = sum(int(x) for x in re.findall(r"(?:failures|errors)=(\d+)", t))
        counts("unittest", _n(m, 1) - bad, bad, sum(int(x) for x in re.findall(r"skipped=(\d+)", t)))
        out["failures"] = re.findall(r"(?m)^(?:FAIL|ERROR): (\S+ \(\S+\))", t)
        return out
    return out


def _cmd_label(cmd: str) -> str:
    """The test part of a compound command: 'cd web && npm ci && npm test 2>&1 | tail' -> 'npm test 2>&1 | tail'."""
    parts = [p.strip() for p in re.split(r"&&|;|\n", _code_only(cmd)) if p.strip()]
    c = next((p for p in parts if is_test_command(p)), cmd.strip()).lstrip("(")
    if c.count(")") > c.count("("):
        c = c[:c.rindex(")")].rstrip()
    return c if len(c) <= 140 else c[:140] + "…"


class TestTracker:
    """Per session: test commands in flight and the last MAX_RUNS finished runs."""

    def __init__(self):
        self.pending: dict[str, dict] = {}
        self.runs: list[dict] = []

    def started(self, tid: str | None, name: str, inp: dict | None, ts: float | None):
        cmd = str((inp or {}).get("command") or "")
        if tid and name == "Bash" and is_test_command(cmd):
            self.pending[tid] = {"id": tid, "cmd": _cmd_label(cmd), "started": ts or time.time()}

    def finished(self, tid: str | None, text: str | None, is_error: bool, ts: float | None):
        run = self.pending.pop(tid, None) if tid else None
        if not run:
            return
        text = text or ""
        p = parse(text)
        exit_failed = bool(is_error) or bool(re.match(r"\s*Exit code [1-9]", text))
        if p["failed"]:
            status = "failed"
        elif p["passed"] is not None and not exit_failed:
            status = "passed"
        elif re.search(r"(?i)interrupted|timed out|command timed out", text[-400:]):
            status = "interrupted"
        else:
            status = "failed" if exit_failed else ("passed" if p["passed"] else "unknown")
        failures = list(dict.fromkeys(f.strip() for f in p["failures"] if f.strip()))[:25]
        run.update(ended=ts or time.time(), status=status, framework=p["framework"], passed=p["passed"],
                   failed=p["failed"], skipped=p["skipped"], failures=failures,
                   output=text[-OUTPUT_TAIL:], truncated=len(text) > OUTPUT_TAIL)
        self.runs.append(run)
        del self.runs[:-MAX_RUNS]

    def summary(self) -> dict | None:
        """The latest run (compact), whether one is running now, and how many there have been."""
        running = max(self.pending.values(), key=lambda r: r["started"]) if self.pending else None
        last = self.runs[-1] if self.runs else None
        if not (running or last):
            return None
        out = {"runs": len(self.runs), "running": running and {"cmd": running["cmd"], "started": running["started"]}}
        if last:
            out["last"] = {k: last[k] for k in ("id", "cmd", "status", "framework", "passed", "failed", "skipped",
                                                "started", "ended")}
            out["last"]["failures"] = last["failures"][:3]
        return out

    def all(self) -> list[dict]:
        return list(reversed(self.runs))
