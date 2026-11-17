"""Manual readback on a configured Mac; does not unmount disks or clean caches.

Uses a private copy of the configuration for mode/fallback tests. A tiny Go
program optionally exercises actual external build-cache writes, offline.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devcache import App


def main():
    app = App()
    config = app.load()
    root = app.external_root(config)
    rows = {r["id"]: r for r in app.scan() if r["available"] and r["id"] in config["tools"]}
    queries = {
        "npm": ("npm", "config", "get", "cache"),
        "pnpm": ("pnpm", "config", "get", "cache-dir"),
        "go": ("go", "env", "-json", "GOCACHE", "GOMODCACHE"),
        "uv": ("uv", "cache", "dir"),
        "pip": ("pip3", "cache", "dir"),
        "brew": ("brew", "--cache"),
    }
    internal = {}
    for name, row in rows.items():
        for target in row["targets"]:
            internal.setdefault(target["key"], target["path"])
    with tempfile.TemporaryDirectory(prefix="devcache-live-smoke-") as folder:
        state = Path(folder) / "state"
        state.mkdir()
        cfg = state / "config.json"
        cfg.write_text(json.dumps(config))
        env = dict(os.environ, DEVCACHE_STATE_DIR=str(state), GOTOOLCHAIN="local",
                   HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1",
                   COREPACK_ENABLE_NETWORK="0")

        def cli(*args, ok=True):
            cp = subprocess.run([str(app.launcher), *args], env=env,
                                capture_output=True, text=True, timeout=90)
            if ok and cp.returncode:
                raise AssertionError((args, cp.returncode, cp.stdout, cp.stderr))
            return cp

        def readback(label, external):
            values = {}
            for name, query in queries.items():
                if name not in rows:
                    continue
                value = cli("run", *query).stdout.strip()
                if name == "go":
                    data = json.loads(value)
                    values.update({"go-build": data["GOCACHE"], "go-mod": data["GOMODCACHE"]})
                else:
                    key = rows[name]["targets"][0]["key"]
                    values[key] = value
            for key, value in values.items():
                expected = str(root / key) if external else internal[key]
                # pnpm config get returns undefined for the native platform default.
                if not external and key == "pnpm" and value == "undefined":
                    continue
                assert value == expected, (label, key, value, expected)
            print(json.dumps({"case": label, "passed": True, "paths": values}, ensure_ascii=False), flush=True)

        readback("external", True)
        cli("use", "internal")
        readback("manual-internal", False)
        cli("use", "external")
        readback("manual-external", True)
        missing = json.loads(cfg.read_text())
        missing["disk"]["uuid"] = "00000000-0000-0000-0000-000000000000"
        cfg.write_text(json.dumps(missing))
        readback("disk-missing-simulation", False)
        assert json.loads(cfg.read_text())["mode"] == "external"
        cfg.write_text(json.dumps(config))
        readback("disk-returned-simulation", True)
        if "yarn" not in rows and not app.real_path("yarn"):
            before = cfg.read_text()
            cp = cli("enable", "yarn", ok=False)
            assert cp.returncode != 0
            assert cfg.read_text() == before
            print(json.dumps({"case": "uninstalled-yarn-rejected", "passed": True}), flush=True)
        if "npm" in rows:
            cp = cli("clean", "--tools", "npm", "--dry-run")
            assert "未清理任何文件" in cp.stdout
            print(json.dumps({"case": "cleanup-preview", "passed": True}), flush=True)
        if "go" in rows:
            source = Path(folder) / "main.go"
            output = Path(folder) / "hello"
            source.write_text('package main\nfunc main() { println("devcache-ok") }\n')
            env["GOPROXY"] = "off"
            cli("run", "go", "build", "-o", str(output), str(source))
            cp = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
            assert cp.returncode == 0 and "devcache-ok" in cp.stderr
            assert len(list((root / "go-build").iterdir())) > 2
            print(json.dumps({"case": "offline-go-build-with-external-cache", "passed": True}), flush=True)
    print("Live checks passed. Actual mode was not changed; no user caches were deleted.")


if __name__ == "__main__":
    main()
