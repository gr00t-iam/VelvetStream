"""Resolve publisher config and bundled tools without depending on the CWD."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    directory = Path(__file__).resolve().parent
    return directory.parent if directory.name == "velvetstream" else directory


def configure_runtime():
    root = Path(getattr(sys, "_MEIPASS", application_root()))
    tools = root / "bin"
    if tools.is_dir():
        os.environ["PATH"] = str(tools) + os.pathsep + os.environ.get("PATH", "")
        command = tools / ("fpcalc.exe" if os.name == "nt" else "fpcalc")
        if command.is_file():
            os.environ.setdefault("FPCALC", str(command))
