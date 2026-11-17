import hashlib
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Darwin", "native installer targets macOS")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devcache-installer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "user with spaces"
        self.assets = self.root / "local release assets"
        self.assets.mkdir()
        self.record = self.root / "called-arguments"
        bundle = self.root / "devcache"
        (bundle / "_internal").mkdir(parents=True)
        exe = bundle / "devcache"
        exe.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo 0.1.0; exit 0; fi\nprintf "%s\\n" "$@" > "$DEVCACHE_INSTALL_TEST_RECORD"\n')
        exe.chmod(0o755)
        for arch in ("arm64", "x86_64"):
            path = self.assets / f"devcache-macos-{arch}.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                archive.add(bundle, arcname="devcache")
        self.write_checksums()

    def write_checksums(self):
        entries = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
                   for path in sorted(self.assets.glob("*.tar.gz"))]
        (self.assets / "SHA256SUMS").write_text("\n".join(entries) + "\n")

    def install(self, *extra):
        return subprocess.run(["/bin/bash", str(ROOT / "install.sh"), "--from", str(self.assets),
                               "--home", str(self.home), *extra], capture_output=True, text=True,
                              env=dict(os.environ, DEVCACHE_INSTALL_TEST_RECORD=str(self.record)), timeout=20)

    def test_offline_install_selects_arch_checks_hash_and_preserves_spaces(self):
        cp = self.install("--version", "v0.1.0", "--no-shell")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(self.record.read_text().splitlines(), ["--home", str(self.home), "install", "--no-shell"])

    def test_bad_checksum_never_executes_or_changes_user_files(self):
        for archive in self.assets.glob("*.tar.gz"):
            archive.write_bytes(archive.read_bytes() + b"tampered")
        cp = self.install()
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("Checksum verification failed", cp.stderr)
        self.assertFalse(self.record.exists())
        self.assertFalse(self.home.exists())

    def test_missing_checksum_is_rejected(self):
        (self.assets / "SHA256SUMS").write_text("")
        cp = self.install()
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("checksum", cp.stderr)
        self.assertFalse(self.record.exists())

    def test_requested_release_must_match_binary_version(self):
        cp = self.install("--version", "v0.2.0")
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("does not match", cp.stderr)
        self.assertFalse(self.record.exists())

    def test_invalid_release_and_relative_home_fail_before_installation(self):
        cp = self.install("--version", "../not-a-release")
        self.assertNotEqual(cp.returncode, 0)
        cp = self.install("--home", "relative")
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
