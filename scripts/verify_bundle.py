"""Exercise first install, upgrade, and full removal in an isolated user root."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    env = dict(os.environ)
    env.pop("DEVCACHE_STATE_DIR", None)
    with tempfile.TemporaryDirectory(prefix="devcache-package-test-") as folder:
        home = Path(folder) / "new user with spaces"
        home.mkdir()
        rc = home / ".zshrc"
        rc.write_text("# existing shell settings\n")
        cache = home / "Library/Caches/keep-this"
        cache.mkdir(parents=True)
        (cache / "data").write_text("preserve cache data")
        launcher = home / ".local/bin/devcache"

        def run(*argv):
            result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError((argv, result.stdout, result.stderr))
            return result.stdout

        run(str(binary), "--home", str(home), "install")
        assert run(str(launcher), "--version").strip()
        config = home / ".config/devcache/config.json"
        config.parent.mkdir(exist_ok=True)
        content = json.dumps({"version": 1, "mode": "internal", "tools": [], "disk": None,
                              "upgrade_sentinel": "preserve settings"})
        config.write_text(content)
        run(str(binary), "--home", str(home), "install")
        assert config.read_text() == content
        assert rc.read_text().count("# >>> devcache >>>") == 1
        status = json.loads(run(str(launcher), "status", "--json"))
        assert status["effective"] == "internal" and status["disk"] is None
        run(str(launcher), "uninstall", "--purge")
        assert not launcher.exists()
        assert not (home / ".local/share/devcache/runtimes").exists()
        assert config.exists()
        assert "# existing shell settings" in rc.read_text()
        assert "# >>> devcache >>>" not in rc.read_text()
        assert (cache / "data").read_text() == "preserve cache data"
        assert list((home / ".config/devcache/backups").iterdir())
    print("Native first install / upgrade / removal passed; user caches and settings retained.")


if __name__ == "__main__":
    main()
