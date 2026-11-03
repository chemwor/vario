"""`vario demo` seeds its own folder and refuses to write anywhere else."""
import json
import os

from app import demo
from tests.conftest import tmp_root


def test_seed_writes_only_inside_the_demo_home():
    home = tmp_root() / "demo-home"
    demo.seed(home)
    assert (home / "data" / "dashboard.sqlite3").exists()
    transcripts = list((home / "projects").glob("*/*.jsonl"))
    assert len(transcripts) == 4
    first = json.loads(transcripts[0].read_text().splitlines()[1])
    assert first["cwd"].startswith("/demo/")
    # the test process's own database is a different file
    from app.config import DB_PATH
    assert not str(DB_PATH).startswith(str(home))
    assert os.environ["VARIO_HOME"] != str(home)
