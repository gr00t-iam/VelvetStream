"""Make a verified public Windows archive without local credentials or settings."""

import hashlib
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    binary = root / "dist" / "single-video" / "VelvetStream"
    destination = root / "release"
    destination.mkdir(exist_ok=True)
    archive_path = destination / "VelvetStream-Windows-x64.zip"
    if not (binary / "VelvetStream.exe").is_file():
        raise RuntimeError("Build the Windows executable first")
    # Exact local-key checks never print the value or embed it in any artifact.
    from velvetstream.fallback import load_application_key

    key = load_application_key()
    secret = key.encode() if key else None
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(binary.rglob("*")):
            if not file.is_file():
                continue
            if file.name == ".env" or file.suffix.lower() in {".env", ".ini", ".log"}:
                continue
            relative = file.relative_to(binary)
            data = (
                (root / "README.md").read_bytes()
                if relative.as_posix() == "README.md"
                else file.read_bytes()
            )
            if secret and secret in data:
                raise RuntimeError("Private configuration found in public package")
            archive.writestr("VelvetStream/" + relative.as_posix(), data)
        archive.write(root / ".env.example", "VelvetStream/.env.example")
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip():
            raise RuntimeError("Release CRC check failed")
        assert "VelvetStream/VelvetStream.exe" in archive.namelist()
        assert not any(name.endswith("/.env") for name in archive.namelist())
    source = root / "VelvetStream-source.zip"
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if (
                name.endswith("/.env")
                or name == ".env"
                or (secret and secret in archive.read(name))
            ):
                raise RuntimeError("Private configuration found in source archive")
    checksums = []
    for file in (archive_path, source):
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksums.append(f"{digest}  {file.name}")
    (destination / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(f"Verified public Windows ZIP: {archive_path.stat().st_size / 1024**2:.1f} MiB")
    print("Private configuration excluded; archive CRC and source key checks passed")


if __name__ == "__main__":
    main()
