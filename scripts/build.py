"""Build a native one-directory release on its target macOS architecture."""
import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != args.arch:
        parser.error("build on the requested macOS architecture; cross-compilation is not supported")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "devcache", "--target-architecture", args.arch,
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"), str(ROOT / "devcache.py"),
    ], cwd=ROOT, check=True)
    bundle = ROOT / "dist/devcache"
    for name in ("LICENSE", "README.md", "README.en.md"):
        shutil.copy2(ROOT / name, bundle / name)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle / "devcache")], check=True)
    subprocess.run([str(bundle / "devcache"), "--version"], check=True)
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    archive = release / f"devcache-macos-{args.arch}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname="devcache")
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{checksum}  {archive.name}\n")
    print(f"Built {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
