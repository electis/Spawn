"""Builds a fake kiro-cli executable for contract tests (no network).

Behavior is steered by environment variables:
- FAKE_LOG: file that receives {"argv": [...], "stdin": "..."} as JSON.
- FAKE_OUT: file whose contents are printed to stdout.
- FAKE_RC: exit code for a chat run (default 0).
- FAKE_DELETE_RC: exit code for --delete-session (default 0).
- FAKE_SLEEP: seconds to sleep before exiting (for timeout tests).
"""

import os
import sys
from pathlib import Path

FAKE_SCRIPT = """#!{python}
import json, os, sys, time
data = sys.stdin.buffer.read().decode("utf-8")
with open(os.environ["FAKE_LOG"], "w", encoding="utf-8") as handle:
    json.dump({{"argv": sys.argv[1:], "stdin": data, "cwd": os.getcwd()}}, handle)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
if "--delete-session" in sys.argv:
    sys.exit(int(os.environ.get("FAKE_DELETE_RC", "0")))
with open(os.environ["FAKE_OUT"], encoding="utf-8") as handle:
    sys.stdout.write(handle.read())
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


def make_fake_kiro(directory: Path) -> Path:
    path = Path(directory) / "fake-kiro-cli"
    path.write_text(FAKE_SCRIPT.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_env(directory: Path, out_text: str = "", **extra: str) -> dict[str, str]:
    out_file = Path(directory) / "out.jsonl"
    out_file.write_text(out_text, encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "FAKE_LOG": str(Path(directory) / "log.json"),
        "FAKE_OUT": str(out_file),
    }
    env.update(extra)
    return env
