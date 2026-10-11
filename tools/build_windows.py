"""Reproducible x64 folder build with local audio tools and approved app icon."""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image


def main():
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise SystemExit("Build on 64-bit Windows with 64-bit Python")
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=root / "dist")
    options = parser.parse_args()
    dist_dir = options.dist_dir.resolve()
    build = root / "build"
    build.mkdir(exist_ok=True)
    icon = build / "velvetstream.ico"
    Image.open(root / "assets" / "velvetstream-icon.png").save(
        icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    arguments = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "VelvetStream",
        "--distpath",
        str(dist_dir),
        "--icon",
        str(icon),
        "--collect-all",
        "shazamio",
        "--collect-all",
        "shazamio_core",
        "--collect-all",
        "yt_dlp",
        "--collect-all",
        "yt_dlp_ejs",
        "--collect-data",
        "certifi",
        "--hidden-import",
        "soundcard.mediafoundation",
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "IPython",
    ]
    for tool in ("ffmpeg", "ffprobe", "node", "fpcalc"):
        path = (root / "bin" / "fpcalc.exe") if tool == "fpcalc" else Path(shutil.which(tool) or "")
        if not path.is_file():
            raise SystemExit(f"Missing build dependency: {tool}")
        arguments += ["--add-binary", str(path) + os.pathsep + "bin"]
    arguments.append(str(root / "velvetstream.py"))
    subprocess.run(arguments, cwd=root, check=True)
    destination = dist_dir / "VelvetStream"
    # Qt on Windows uses Microsoft's native ICU API (unversioned symbols).
    # PATH may contain Poppler's incompatible ICU with suffixed symbols. The
    # automatic dependency scanner can collect that DLL despite Qt expecting
    # the OS library. Omit it and let the loader resolve Windows' system ICU.
    for library in (destination / "_internal").glob("icu*.dll"):
        library.unlink()
    # Local testing config stays external to the executable and source package.
    if (root / ".env").is_file():
        shutil.copyfile(root / ".env", destination / ".env")
    shutil.copyfile(root / "README.md", destination / "README.md")
    (destination / "BUNDLED-TOOLS.txt").write_text(
        "VelvetStream Windows x64 local testing build.\n"
        "Keep this folder intact; start VelvetStream.exe.\n"
        "Local .env configuration is private. Do not share it.\n\n"
        "Bundled external tools and license/source information:\n"
        "FFmpeg and ffprobe: https://ffmpeg.org/legal.html and https://www.gyan.dev/ffmpeg/builds/\n"
        "Node.js: https://github.com/nodejs/node/blob/main/LICENSE\n"
        "Chromaprint: https://github.com/acoustid/chromaprint\n"
        "Qt/PySide6: https://www.qt.io/licensing/\n"
        "This is an unsigned local testing build, not a signed public release.\n",
        encoding="utf-8",
    )
    print("Built:", destination / "VelvetStream.exe")


if __name__ == "__main__":
    main()
