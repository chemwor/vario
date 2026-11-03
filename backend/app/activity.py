"""Turn a tool call into a short human phrase: "running tests", "editing auth.py"."""
import os
import re

TEST_RE = re.compile(r"\b(pytest|jest|vitest|mocha|rspec|go test|cargo test|npm (run )?test|pnpm (run )?test|yarn test|bun test|unittest|phpunit|make test)\b")


def gerund(phrase: str) -> str:
    """Bash descriptions are imperative ("Run the build"); speak them as "running the build"."""
    words = phrase.split()
    if not words:
        return phrase
    w = words[0].lower()
    if w.endswith("ing") or not w.isalpha():
        return phrase[0].lower() + phrase[1:]
    if w.endswith("ie"):
        g = w[:-2] + "ying"
    elif w.endswith("e") and not w.endswith(("ee", "ye", "oe")):
        g = w[:-1] + "ing"
    elif re.fullmatch(r"[^aeiou]*[aeiou][^aeiouwxy]", w) and len(w) <= 4:
        g = w + w[-1] + "ing"          # run -> running, set -> setting
    else:
        g = w + "ing"
    return " ".join([g] + words[1:])


def _base(p) -> str:
    return os.path.basename(str(p).rstrip("/")) or str(p)


def describe(tool: str, inp: dict | None) -> str:
    inp = inp or {}
    if tool == "Bash":
        cmd = str(inp.get("command", ""))
        if TEST_RE.search(cmd):
            return "running tests"
        if re.search(r"\b(npm|pnpm|yarn|bun) (run )?build\b|\bmake\b|\bcargo build\b|\btsc\b", cmd):
            return "building"
        if re.match(r"\s*git\b", cmd):
            return "running git " + (cmd.split()[1] if len(cmd.split()) > 1 else "")
        if re.search(r"\b(pip|uv|npm|pnpm|yarn|brew) (install|add)\b", cmd):
            return "installing packages"
        desc = inp.get("description")
        if desc:
            return gerund(str(desc)[:60].rstrip("."))
        first = cmd.strip().split()[0] if cmd.strip() else "a command"
        return f"running {_base(first)}"
    if tool in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"editing {_base(inp.get('file_path') or inp.get('notebook_path', 'a file'))}"
    if tool == "Write":
        return f"writing {_base(inp.get('file_path', 'a file'))}"
    if tool == "Read":
        return f"reading {_base(inp.get('file_path', 'a file'))}"
    if tool in ("Grep", "Glob"):
        pat = inp.get("pattern", "")
        return f"searching for {pat[:30]}" if pat else "searching the code"
    if tool in ("WebFetch", "WebSearch"):
        return "browsing the web"
    if tool in ("Task", "Agent"):
        return f"running a subagent: {str(inp.get('description', ''))[:40]}".rstrip(": ")
    if tool == "TodoWrite":
        return "updating the plan"
    if tool.startswith("mcp__"):
        return f"using {tool.split('__')[-1].replace('_', ' ')}"
    return f"using {tool}"
