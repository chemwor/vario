"""The platform layer picks a tool or degrades to None; it never raises."""
import os

from app import host


def test_inhibit_is_tied_to_our_pid():
    cmd = host.inhibit_sleep_cmd(os.getpid())
    if cmd:
        assert str(os.getpid()) in " ".join(cmd)
        assert cmd[0] in ("caffeinate", "systemd-inhibit")


def test_notify_escapes_quotes():
    cmd = host.notify_cmd('say "hi"', 'a "quoted" body')
    if cmd and cmd[0] == "osascript":
        assert '\\"quoted\\"' in cmd[2]
    elif cmd:
        assert cmd[0] == "notify-send"


def test_capabilities_shape():
    caps = host.capabilities()
    assert set(caps) >= {"os", "chime", "keep_awake", "notifications", "fallback_speech", "kokoro"}
