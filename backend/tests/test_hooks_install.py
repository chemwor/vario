"""The wizard's hook install shows an exact diff, needs consent to that diff, keeps your own settings and hooks,
and uninstall removes exactly what it added."""
import json

from app import firstrun

USER_SETTINGS = {"model": "sonnet", "permissions": {"allow": ["Bash(npm test:*)"]},
                 "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/usr/local/bin/my-hook.sh"}]}]}}


def write(d):
    firstrun.SETTINGS.write_text(json.dumps(d, indent=2))


def read():
    return json.loads(firstrun.SETTINGS.read_text())


def test_install_and_uninstall_round_trip():
    write(USER_SETTINGS)
    plan = firstrun.hooks_plan()
    assert plan["ok"] and plan["changes"]
    assert any("speak.sh" in line for line in plan["diff"] if line.startswith("+"))
    # consent is to a specific diff: a different one is refused and nothing is written
    refused = firstrun.install_hooks(["not the diff you saw"])
    assert not refused["ok"] and read() == USER_SETTINGS
    done = firstrun.install_hooks(plan["diff"])
    assert done["ok"] and done["backup"]
    after = read()
    assert after["model"] == "sonnet" and after["permissions"] == USER_SETTINGS["permissions"]
    stop_cmds = [h["command"] for g in after["hooks"]["Stop"] for h in g["hooks"]]
    assert stop_cmds[0] == "/usr/local/bin/my-hook.sh" and stop_cmds[1].endswith("/speak.sh")
    assert firstrun.installed_version()["version"] == plan["version"]
    assert (firstrun.HOOK_DIR / "speak.sh").exists()
    # installing again changes nothing
    assert not firstrun.hooks_plan()["changes"]
    # uninstall: your settings come back exactly, your own hook stays
    gone = firstrun.uninstall_hooks()
    assert read() == USER_SETTINGS
    assert gone["backup"]
    assert not (firstrun.HOOK_DIR / "speak.sh").exists()


def test_uninstall_keeps_hooks_added_after_install():
    write({})
    firstrun.install_hooks(firstrun.hooks_plan()["diff"])
    s = read()
    s["hooks"]["PreToolUse"] = [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/guard.sh"}]}]
    write(s)
    firstrun.uninstall_hooks()
    assert read() == {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/guard.sh"}]}]}}


def test_invalid_settings_json_is_never_overwritten():
    firstrun.SETTINGS.write_text("{ not json")
    plan = firstrun.hooks_plan()
    assert not plan["ok"] and "isn't valid JSON" in plan["error"]
    assert firstrun.SETTINGS.read_text() == "{ not json"


def test_uninstall_removes_a_settings_file_setup_created():
    firstrun.SETTINGS.unlink(missing_ok=True)
    plan = firstrun.hooks_plan()
    assert not plan["settings_exists"]
    firstrun.install_hooks(plan["diff"])
    assert firstrun.SETTINGS.exists()
    firstrun.uninstall_hooks()
    assert not firstrun.SETTINGS.exists()
