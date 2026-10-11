"""Package maintained source and verification artifacts, excluding private config."""

import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    names = [
        "velvetstream.py",
        "VelvetStreamStandalone.py",
        "README.md",
        "VALIDATION.md",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-build.txt",
        "pyproject.toml",
        "pytest.ini",
        ".gitignore",
        ".env.example",
    ]
    files = [root / name for name in names]
    for folder in ("velvetstream", "tests", "tools", "docs", "assets"):
        files.extend(
            path
            for path in (root / folder).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in (".zip", ".pyc", ".ini")
        )
    destination = root / "VelvetStream-source.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(root).as_posix())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Source archive failed CRC validation")
        for name in archive.namelist():
            if name.endswith(".py"):
                compile(archive.read(name), name, "exec")
        print("Source files:", len(archive.namelist()))
        print("Archive:", destination)


if __name__ == "__main__":
    main()
