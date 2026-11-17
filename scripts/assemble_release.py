"""Check both build artifacts, assemble a single checksum manifest and notes."""
import ast
import hashlib
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def version():
    module = ast.parse((ROOT / "devcache.py").read_text())
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("missing VERSION")


def main():
    tag, artifacts, destination = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    if tag != "v" + version():
        raise SystemExit("tag must match devcache.py VERSION")
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for arch in ("arm64", "x86_64"):
        name = f"devcache-macos-{arch}.tar.gz"
        archive = artifacts / name
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        if (artifacts / (name + ".sha256")).read_text().strip() != f"{checksum}  {name}":
            raise SystemExit(f"build checksum mismatch: {name}")
        shutil.copy2(archive, destination / name)
        entries.append(f"{checksum}  {name}")
    shutil.copy2(ROOT / "install.sh", destination / "install.sh")
    entries.append(f"{hashlib.sha256((destination / 'install.sh').read_bytes()).hexdigest()}  install.sh")
    (destination / "SHA256SUMS").write_text("\n".join(entries) + "\n")
    changelog = (ROOT / "CHANGELOG.md").read_text()
    start = changelog.index(f"## [{version()}]")
    end = changelog.find("\n## [", start + 1)
    notes = changelog[start:end if end != -1 else None].strip()
    (destination.parent / "release-notes.md").write_text(notes + "\n\n"
        "Install (macOS Apple Silicon / Intel, no Python required):\n\n```sh\n"
        f"curl -fsSL https://raw.githubusercontent.com/abbhb/devcache/{tag}/install.sh | bash\n```\n")


if __name__ == "__main__":
    main()
