"""Every test runs against throwaway folders, set before any app module is imported: never your real settings.json,
database, hook dir, transcripts or speakers."""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="vario-tests-"))
for k, v in {"VARIO_HOME": _TMP / "home", "CLAUDE_HOOK_DIR": _TMP / "hooks", "CLAUDE_SETTINGS": _TMP / "settings.json",
             "CLAUDE_PROJECTS_DIR": _TMP / "projects", "VARIO_DIR": _TMP / "drops", "DASHBOARD_API_PORT": "8799",
             "STT_PYTHON": str(_TMP / "no-voice-venv" / "bin" / "python"), "SCOUTS": "off"}.items():
    os.environ[k] = str(v)
os.environ.pop("DASHBOARD_DATA_DIR", None)
os.environ.pop("VARIO_CONFIG", None)
(_TMP / "hooks").mkdir(parents=True)
(_TMP / "projects").mkdir(parents=True)
# no speech, no desktop notifications from tests
(_TMP / "hooks" / "voice.conf").write_text("NOTIFY=off\nBUBO_SOCKET=/nonexistent/tts.sock\n")


def tmp_root() -> Path:
    return _TMP
