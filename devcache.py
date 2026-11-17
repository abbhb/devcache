#!/usr/bin/env python3
"""macOS development cache routing. Python standard library only."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

VERSION = "0.1.0"
START = "# >>> devcache >>>"
END = "# <<< devcache <<<"
MARKER = ".devcache-volume.json"
META = "_DEVCACHE_ENV"
COMMANDS = {
    "npm": ("npm", "npx"), "go": ("go",), "pnpm": ("pnpm",),
    "uv": ("uv",), "pip": ("pip", "pip3"), "brew": ("brew",),
    "yarn": ("yarn",),
}
ROUTES = {
    "npm": {"npm_config_cache": "npm"},
    "go": {"GOCACHE": "go-build", "GOMODCACHE": "go-mod"},
    "pnpm": {"npm_config_cache_dir": "pnpm"},
    "uv": {"UV_CACHE_DIR": "uv"},
    "pip": {"PIP_CACHE_DIR": "pip"},
    "brew": {"HOMEBREW_CACHE": "Homebrew"},
    "yarn": {"YARN_CACHE_FOLDER": "yarn"},
}
INFO_ONLY = {
    "gvm": "Go 版本管理器；模块/编译缓存通过 go 管理，保留编译器。",
    "cargo": "已检测到；CARGO_HOME 混有配置、凭据和工具，本版不提供整体迁移。",
    "mvn": "已检测到；本版尚未适配 Maven 仓库。",
    "gradle": "已检测到；GRADLE_USER_HOME 混有配置，本版尚未适配。",
    "bun": "已检测到；本版尚未适配。",
    "deno": "已检测到；本版尚未适配。",
    "ccache": "已检测到；本版尚未适配。",
    "sccache": "已检测到；本版尚未适配。",
}


class Error(Exception):
    pass


def atomic_write(path: Path, text: str, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".devcache-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def base_environment(env: dict[str, str]) -> dict[str, str]:
    """Undo inherited routing before deciding again, including in nested shims."""
    result = dict(env)
    try:
        previous = json.loads(result.pop(META, "{}"))
        for key, item in previous.items():
            if result.get(key) == item["routed"]:
                if item["original"] is None:
                    result.pop(key, None)
                else:
                    result[key] = item["original"]
    except (ValueError, TypeError, KeyError, AttributeError):
        # This metadata contains only cache paths and is never a source of commands.
        result.pop(META, None)
    return result


def routed_environment(env: dict[str, str], overrides: dict[str, str]):
    result = base_environment(env)
    metadata = {k: {"original": result.get(k), "routed": v}
                for k, v in overrides.items()}
    result.update(overrides)
    if metadata:
        result[META] = json.dumps(metadata)
    return result


def human_size(value):
    if value is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def size_of(path: Path):
    if not path.exists():
        return 0
    try:
        cp = subprocess.run(["/usr/bin/du", "-sk", str(path)], capture_output=True,
                            text=True, timeout=90)
        return int(cp.stdout.split()[0]) * 1024 if cp.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


class Disks:
    def call(self, *args):
        try:
            cp = subprocess.run(["/usr/sbin/diskutil", *args], capture_output=True,
                                timeout=2)
            if cp.returncode:
                return None
            return plistlib.loads(cp.stdout)
        except (OSError, ValueError, plistlib.InvalidFileException,
                subprocess.TimeoutExpired):
            return None

    def info(self, identity):
        return self.call("info", "-plist", identity)

    def list(self):
        data = self.call("list", "-plist", "external") or {}
        names = data.get("AllDisks", [])
        with ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(self.info, names))
        return [x for x in items if x and x.get("VolumeUUID")
                and x.get("MountPoint") and x.get("Internal") is False]

    def select(self, query):
        candidates = [x for x in self.list() if query.casefold() in
                      {str(x.get(k, "")).casefold() for k in
                       ("VolumeName", "VolumeUUID", "MountPoint", "DeviceIdentifier")}]
        if len(candidates) != 1:
            raise Error(f"外盘 {query!r} 未挂载或名称不唯一；运行 devcache disks 查看 UUID。")
        return candidates[0]


class App:
    def __init__(self, home=None, state=None, env=None, disks=None):
        self.home = Path(home or Path.home()).absolute()
        self.state = Path(state or os.environ.get(
            "DEVCACHE_STATE_DIR", str(self.home / ".config/devcache"))).absolute()
        self.env = base_environment(dict(os.environ if env is None else env))
        self.disks = disks or Disks()
        self.config_path = self.state / "config.json"
        self.shims = self.home / ".local/share/devcache/shims"
        self.launcher = self.home / ".local/bin/devcache"
        self.shell_path = self.home / ".local/share/devcache/shell.zsh"

    def load(self):
        if not self.config_path.exists():
            return {"version": 1, "mode": "internal", "tools": [], "disk": None}
        try:
            data = json.loads(self.config_path.read_text())
            disk = data.get("disk") if isinstance(data, dict) else None
            if (not isinstance(data, dict) or data.get("version") != 1 or data.get("mode") not in
                    ("internal", "external", "auto") or
                    not isinstance(data.get("tools"), list) or
                    any(x not in ROUTES for x in data["tools"]) or
                    (disk is not None and (not isinstance(disk, dict) or any(
                        not isinstance(disk.get(k), str) or not disk[k] for k in ("uuid", "name", "root"))))):
                raise ValueError("unsupported configuration")
            return data
        except (ValueError, TypeError, KeyError) as exc:
            raise Error(f"配置无法读取：{self.config_path}；原工具仍可直接运行。") from exc

    def save(self, config):
        atomic_write(self.config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    def real_path(self, command):
        entries = []
        for entry in self.env.get("PATH", os.defpath).split(os.pathsep):
            if os.path.abspath(entry or ".") != str(self.shims):
                entries.append(entry)
        return shutil.which(command, path=os.pathsep.join(entries))

    def probe(self, command, *args):
        executable = self.real_path(command)
        if not executable:
            raise Error("命令未安装或当前 PATH 不可见")
        env = dict(self.env, COREPACK_ENABLE_NETWORK="0", HOMEBREW_NO_AUTO_UPDATE="1",
                   HOMEBREW_NO_ANALYTICS="1", GOTOOLCHAIN="local", NO_COLOR="1")
        try:
            cp = subprocess.run([executable, *args], env=env, cwd=self.home,
                                capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Error("工具启动失败或缓存查询超时") from exc
        if cp.returncode:
            raise Error("工具无法正常执行缓存查询（未下载或安装任何工具）")
        return cp.stdout.strip()

    def cache_path(self, value):
        value = value.strip().strip('"')
        if not value or value in ("undefined", "null", "None"):
            raise Error("工具没有返回有效缓存路径")
        path = Path(os.path.expanduser(value))
        if not path.is_absolute():
            path = self.home / path
        return str(path.absolute())

    def scan_one(self, tool):
        aliases = [c for c in COMMANDS[tool] if self.real_path(c)]
        # npx on its own is not evidence that npm is installed and usable.
        primary = "pip3" if tool == "pip" and "pip3" in aliases else COMMANDS[tool][0]
        if not aliases or not self.real_path(primary):
            return None
        row = {"id": tool, "commands": aliases, "executable": self.real_path(primary),
               "available": False, "targets": [], "note": ""}
        try:
            if tool == "go":
                data = json.loads(self.probe("go", "env", "-json", "GOCACHE", "GOMODCACHE"))
                targets = [("go-build", "GOCACHE", data["GOCACHE"]),
                           ("go-mod", "GOMODCACHE", data["GOMODCACHE"])]
                # Historical GVM pkgsets can have separate, equally disposable module caches.
                gvm = Path(self.env.get("GVM_ROOT", str(self.home / ".gvm")))
                for path in sorted((gvm / "pkgsets").glob("*/*/pkg/mod")):
                    if str(path) != data["GOMODCACHE"]:
                        targets.append(("go-mod", "GOMODCACHE", str(path)))
            elif tool == "npm":
                targets = [("npm", "npm_config_cache", self.probe("npm", "config", "get", "cache"))]
            elif tool == "pnpm":
                row["version"] = self.probe("pnpm", "--version")
                # This adapter has been checked against pnpm 10. Never guess a new major's layout.
                if row["version"].split(".")[0] != "10":
                    raise Error("本版仅验证 pnpm 10 的 cacheDir；其他版本暂不提供切换")
                path = self.probe("pnpm", "config", "get", "cache-dir")
                if path in ("undefined", "null", ""):
                    path = str(Path(self.env.get("XDG_CACHE_HOME",
                                    str(self.home / "Library/Caches"))) / "pnpm")
                targets = [("pnpm", "npm_config_cache_dir", path)]
                row["note"] = "仅迁移 cacheDir；store 和现有项目 node_modules 保留，避免 store 绑定冲突。"
                row["store"] = self.probe("pnpm", "store", "path")
            elif tool == "uv":
                targets = [("uv", "UV_CACHE_DIR", self.probe("uv", "cache", "dir"))]
                row["note"] = "外盘与虚拟环境跨文件系统时，uv 会使用复制，安装可能更慢。"
            elif tool == "pip":
                targets = [("pip", "PIP_CACHE_DIR", self.probe(primary, "cache", "dir"))]
            elif tool == "brew":
                targets = [("Homebrew", "HOMEBREW_CACHE", self.probe("brew", "--cache"))]
            elif tool == "yarn":
                version = self.probe("yarn", "--version")
                if version.split(".")[0] != "1":
                    raise Error("Yarn 2+ 的 PnP 可能直接依赖缓存文件；本版仅支持 Yarn Classic")
                path = Path(self.probe("yarn", "cache", "dir"))
                # Classic appends a format version (e.g. v6) to YARN_CACHE_FOLDER.
                if re.fullmatch(r"v\d+", path.name):
                    path = path.parent
                targets = [("yarn", "YARN_CACHE_FOLDER", str(path))]
            row["targets"] = [{"key": k, "env": e, "path": self.cache_path(p)}
                              for k, e, p in targets]
            row["available"] = True
        except (Error, ValueError, KeyError) as exc:
            row["note"] = str(exc)
        return row

    def scan(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(self.scan_one, COMMANDS))
        return [r for r in rows if r]

    def installed_selection(self, requested=None):
        rows = self.scan()
        available = {r["id"]: r for r in rows if r["available"]}
        names = list(available) if requested is None else list(dict.fromkeys(requested))
        missing = [name for name in names if name not in available]
        if missing:
            raise Error("未安装、不可用或尚未适配，不能启用：" + ", ".join(missing))
        if not names:
            raise Error("没有检测到可以管理的已安装工具。")
        return [available[name] for name in names]

    @contextlib.contextmanager
    def lock(self, exclusive=False):
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "activity.lock").open("a") as f:
            try:
                fcntl.flock(f, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Error("已有受管理命令/缓存维护正在运行，请结束后重试。") from exc
            yield

    def external_root(self, config, initialize=False):
        disk = config.get("disk")
        if not disk:
            raise Error("尚未绑定外盘")
        info = self.disks.info(disk["uuid"])
        if (not info or info.get("VolumeUUID", "").casefold() != disk["uuid"].casefold()
                or info.get("Internal") is not False or not info.get("MountPoint")
                or info.get("WritableVolume") is False):
            raise Error("绑定的外盘未挂载或不可写")
        mount = Path(info["MountPoint"])
        if not os.path.ismount(mount) or mount.is_symlink():
            raise Error("挂载点验证失败")
        relative = Path(disk["root"])
        if relative.is_absolute() or not relative.parts or any(
                part in (".", "..") for part in relative.parts):
            raise Error("外盘缓存目录配置无效")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        # All directory creation is anchored to an open volume descriptor. Never
        # mkdir -p /Volumes/name: a detached disk must not become an internal folder.
        with contextlib.ExitStack() as stack:
            fd = os.open(mount, flags)
            stack.callback(os.close, fd)
            device = os.fstat(fd).st_dev
            for component in relative.parts:
                if initialize:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                fd = os.open(component, flags, dir_fd=fd)
                stack.callback(os.close, fd)
                if os.fstat(fd).st_dev != device:
                    raise Error("缓存目录跨到了其他文件系统")
            marker = {"version": 1, "uuid": disk["uuid"]}
            if initialize:
                try:
                    mfd = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(mfd, "w") as f:
                        json.dump(marker, f)
            mfd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(mfd) as f:
                if json.load(f) != marker:
                    raise Error("外盘目录标记不匹配")
            leaves = {leaf for tool in config["tools"] for leaf in ROUTES[tool].values()}
            for leaf in leaves:
                try:
                    os.mkdir(leaf, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(leaf, flags, dir_fd=fd)
                try:
                    if os.fstat(child).st_dev != device:
                        raise Error("缓存子目录跨到了其他文件系统")
                finally:
                    os.close(child)
            probe = f".write-probe-{os.getpid()}"
            pfd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=fd)
            os.close(pfd)
            os.unlink(probe, dir_fd=fd)
        return mount / relative

    def effective(self, config):
        if config["mode"] == "internal":
            return None, "手动使用内盘/原配置"
        try:
            return self.external_root(config), "外盘已就绪"
        except (Error, OSError, ValueError) as exc:
            return None, f"自动回退内盘：{exc}"

    def setup(self, disk_name, names=None):
        rows = self.installed_selection(names)
        info = self.disks.select(disk_name)
        config = {"version": 1, "mode": "external", "tools": [r["id"] for r in rows],
                  "disk": {"uuid": info["VolumeUUID"], "name": info["VolumeName"],
                           "root": f"DevCaches/devcache-{self.home.name}"}}
        with self.lock(exclusive=True):
            self.external_root(config, initialize=True)
            self.save(config)
            if self.launcher.exists():
                self.write_integration(rows)
        print(f"已绑定 {info['VolumeName']}（UUID {info['VolumeUUID']}）。")
        print("已为已安装工具创建外盘缓存目录，并启用外盘优先、缺盘自动回退。")
        print("旧缓存尚未复制或清理；可执行 devcache copy 或 devcache clean。")

    def use(self, mode):
        config = self.load()
        if mode != "internal" and not config.get("disk"):
            raise Error("请先运行 devcache setup <外盘名称>。")
        config["mode"] = mode
        self.save(config)
        root, reason = self.effective(config)
        print(f"偏好：{mode}；当前：{'external' if root else 'internal'}；{reason}")

    def toggle(self, names, enabled):
        config = self.load()
        if not config.get("disk"):
            raise Error("请先运行 devcache setup <外盘名称>。")
        # Both operations validate availability; stale directories do not count.
        selected = self.installed_selection(names)
        tools = set(config["tools"])
        for row in selected:
            (tools.add if enabled else tools.discard)(row["id"])
        config["tools"] = [x for x in COMMANDS if x in tools]
        with self.lock(exclusive=True):
            if enabled:
                try:
                    self.external_root(config)
                except (Error, OSError):
                    pass  # It will become effective when the bound disk returns.
            self.save(config)
            self.write_integration(self.scan())
        print(("已启用：" if enabled else "已停用：") + ", ".join(names))

    def run(self, argv, all_tools=False):
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            raise Error("缺少要运行的命令")
        command = argv[0]
        executable = self.real_path(command)
        if not executable:
            print(f"devcache: {command} 未安装或当前 PATH 不可见。", file=sys.stderr)
            return 127
        try:
            config = self.load()
        except Error:
            print("devcache: 配置不可用，按原工具配置执行。", file=sys.stderr)
            config = {"mode": "internal", "tools": [], "disk": None}
        tool = next((k for k, aliases in COMMANDS.items() if command in aliases), None)
        selected = config["tools"] if all_tools else ([tool] if tool in config["tools"] else [])
        # A version manager may switch to a different major after setup. Do not
        # route a new pnpm/Yarn layout merely because the old one was enabled.
        selected = list(selected)
        for name, major in (("pnpm", "10"), ("yarn", "1")):
            if name in selected:
                try:
                    compatible = self.probe(name, "--version").split(".")[0] == major
                except Error:
                    compatible = False
                if not compatible:
                    selected.remove(name)
        with self.lock():
            root, reason = self.effective(config) if selected else (None, "未管理")
            overrides = {}
            if root:
                for name in selected:
                    if any(self.real_path(alias) for alias in COMMANDS[name]):
                        overrides.update({key: str(root / leaf) for key, leaf in ROUTES[name].items()})
            env = routed_environment(self.env, overrides)
            if self.env.get("DEVCACHE_VERBOSE") == "1":
                print(f"devcache: {'external' if root else 'internal'}；{reason}", file=sys.stderr)
            process = subprocess.Popen([executable, *argv[1:]], env=env)
            previous = {}
            def forward(signum, _frame):
                if process.poll() is None:
                    process.send_signal(signum)
            try:
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    previous[sig] = signal.signal(sig, forward)
                code = process.wait()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
            return code if code >= 0 else 128 - code

    def report(self, sizes=False, as_json=False, scan_only=False):
        config = self.load()
        root, reason = self.effective(config)
        rows = self.scan()
        for row in rows:
            row["enabled"] = row["id"] in config["tools"] and row["available"]
            for target in row["targets"]:
                target["effective"] = str(root / target["key"]) if root and row["enabled"] else target["path"]
                if sizes:
                    target["internal_bytes"] = size_of(Path(target["path"]))
                    target["external_bytes"] = size_of(root / target["key"]) if root else None
        notes = [{"id": name, "note": note, "switchable": False}
                 for name, note in INFO_ONLY.items() if self.real_path(name)]
        data = {"mode": config["mode"], "effective": "external" if root else "internal",
                "reason": reason, "disk": config.get("disk"), "root": str(root) if root else None,
                "tools": rows, "other_installed": notes}
        if as_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return data
        print(f"devcache {VERSION}  偏好 {config['mode']} → 当前 {data['effective']}")
        print(reason)
        if config.get("disk"):
            print(f"外盘：{config['disk']['name']}  UUID {config['disk']['uuid']}")
        print("\n已安装并可管理的工具：")
        for row in rows:
            if not row["available"]:
                continue
            flag = "启用" if row["enabled"] else "停用"
            print(f"  {row['id']:<7} [{flag}] {row['executable']}")
            for target in row["targets"]:
                amount = f"  内盘 {human_size(target['internal_bytes'])}" if sizes else ""
                print(f"    {target['key']:<10} {target['path']}{amount}")
                if target["effective"] != target["path"]:
                    print(f"      → {target['effective']}")
            if row["note"]:
                print(f"    {row['note']}")
        unavailable = [{"id": r["id"], "note": r["note"]} for r in rows if not r["available"]]
        if unavailable or notes:
            print("\n检测到但不提供切换：")
            for item in unavailable + notes:
                print(f"  {item['id']}: {item['note']}")
        return data

    def safe_source(self, target):
        path = Path(target["path"])
        try:
            rel = path.relative_to(self.home)
        except ValueError as exc:
            raise Error(f"拒绝清理 home 以外的路径：{path}") from exc
        if (any(part == ".." for part in rel.parts) or not rel.parts or
                (len(rel.parts) < 2 and not (target["key"] == "npm" and rel.parts == (".npm",)))):
            raise Error(f"拒绝清理过宽的目录：{path}")
        allowed = (rel.parts[:2] == ("Library", "Caches") and len(rel.parts) >= 3)
        allowed |= (rel.parts[0] == ".cache" and len(rel.parts) >= 2)
        allowed |= (target["key"] == "npm" and rel.parts == (".npm",))
        allowed |= (target["key"] == "go-mod" and rel.parts[-2:] == ("pkg", "mod")
                    and (rel.parts[0] == "go" or rel.parts[:2] == (".gvm", "pkgsets")))
        if not allowed:
            raise Error(f"自定义目录不在可清理范围，保留原数据：{path}")
        current = path
        while current != self.home:
            if current.is_symlink():
                raise Error(f"拒绝跟随缓存目录的符号链接：{current}")
            current = current.parent
        if path.exists() and path.stat().st_dev != self.home.stat().st_dev:
            raise Error(f"该路径不在内建盘：{path}")
        if any((path / name).exists() for name in (".git", "package.json", "go.mod", "pyproject.toml")):
            raise Error(f"该目录看起来是项目，拒绝清理：{path}")
        return path

    def maintenance_rows(self, names=None):
        config = self.load()
        if names is not None:
            return self.installed_selection(names)
        rows = [row for row in self.scan() if row["available"] and row["id"] in config["tools"]]
        if not rows:
            raise Error("没有已安装且已启用的工具可供维护。")
        return rows

    def confirm(self, message, yes=False):
        if yes:
            return True
        if not sys.stdin.isatty():
            raise Error("非交互模式不会清理；先使用 --dry-run，确认后显式加 --yes。")
        return input(message + " [y/N] ").strip().lower() in ("y", "yes")

    def clean(self, names=None, dry_run=False, yes=False):
        rows = self.maintenance_rows(names)
        targets = [(row, target, self.safe_source(target)) for row in rows for target in row["targets"]]
        print("将清理以下内盘缓存（不会删除工具本体、项目或外盘缓存）：")
        for row, target, path in targets:
            print(f"  {row['id']}/{target['key']}  {human_size(size_of(path))}  {path}")
        print("npm 仅清理 _cacache、_npx、_logs；其他文件保留。")
        if dry_run:
            print("仅预览，未清理任何文件。")
            return
        if not self.confirm("请先停止安装/编译任务。清理后缺失依赖可能需要联网下载。继续？", yes):
            print("已取消。")
            return
        with self.lock(exclusive=True):
            for row, target, path in targets:
                # Revalidate after confirmation rather than trusting the preview.
                self.safe_source(target)
                if not path.exists():
                    continue
                before = size_of(path)
                env = dict(self.env, HOMEBREW_NO_AUTO_UPDATE="1", COREPACK_ENABLE_NETWORK="0")
                if target["key"] == "go-mod":
                    argv = [self.real_path("go"), "clean", "-modcache"]
                    env["GOMODCACHE"] = str(path)
                    env["GOTOOLCHAIN"] = "local"
                elif target["key"] == "go-build":
                    argv = [self.real_path("go"), "clean", "-cache"]
                    env["GOCACHE"] = str(path)
                    env["GOTOOLCHAIN"] = "local"
                elif row["id"] == "uv":
                    argv = [self.real_path("uv"), "cache", "clean", "--cache-dir", str(path)]
                elif row["id"] == "pip":
                    argv = [row["executable"], "cache", "purge", "--cache-dir", str(path)]
                else:
                    argv = None
                    children = (path / n for n in ("_cacache", "_npx", "_logs")) if row["id"] == "npm" else list(path.iterdir())
                    for child in children:
                        if child.is_symlink() or child.is_file():
                            child.unlink()
                        elif child.is_dir():
                            shutil.rmtree(child)
                if argv:
                    cp = subprocess.run(argv, env=env, cwd=self.home)
                    if cp.returncode:
                        raise Error(f"{row['id']} 清理失败，已停止后续清理。")
                after = size_of(path)
                freed = max(0, before - after) if before is not None and after is not None else None
                print(f"已清理 {row['id']}/{target['key']}：目录占用减少 {human_size(freed)}")

    def copy(self, names=None, ask_clean=False):
        rows = self.maintenance_rows(names)
        config = self.load()
        # Copy can also prepare a detected tool that is currently disabled.
        config["tools"] = list(dict.fromkeys(config["tools"] + [r["id"] for r in rows]))
        with self.lock(exclusive=True):
            root = self.external_root(config)
            for row in rows:
                for target in row["targets"]:
                    source = self.safe_source(target)
                    if not source.exists():
                        continue
                    dest = root / target["key"]
                    pairs = [(source, dest)]
                    if row["id"] == "npm":
                        pairs = [(source / name, dest / name) for name in
                                 ("_cacache", "_npx", "_logs") if (source / name).exists()]
                    for src, dst in pairs:
                        if src.is_symlink():
                            raise Error(f"拒绝复制符号链接缓存根目录：{src}")
                        if dst.is_symlink():
                            raise Error(f"拒绝复制到符号链接缓存根目录：{dst}")
                        print(f"复制 {src} → {dst}（保留内盘原文件）", flush=True)
                        # No --delete, and never overwrite an already-populated cache file.
                        argv = ["/usr/bin/rsync", "-a", "--ignore-existing", str(src) + "/", str(dst) + "/"]
                        if subprocess.run(argv).returncode:
                            raise Error("复制失败；未清理内盘，已复制的缓存可保留后重试。")
                        self.external_root(config)
                        with subprocess.Popen(["/usr/bin/rsync", "-rlnc", "--itemize-changes",
                                               str(src) + "/", str(dst) + "/"],
                                              stdout=subprocess.PIPE, text=True) as check:
                            different = False
                            for line in check.stdout:
                                if line.strip():
                                    if not different:
                                        print("复制校验发现差异：" + line.rstrip(), file=sys.stderr)
                                    different = True
                            check.stdout.close()
                            if check.wait() or different:
                                raise Error("校验未通过（可能缓存正在变化）；保留内盘原文件，不进入清理。")
                    print(f"已通过内容校验：{target['key']}")
        if ask_clean:
            self.clean([r["id"] for r in rows])

    def write_integration(self, rows):
        selected = self.load()["tools"]
        commands = [c for r in rows if r["id"] in selected and r["available"] for c in r["commands"]]
        self.shims.mkdir(parents=True, exist_ok=True)
        for path in self.shims.iterdir():
            if path.name not in commands and path.is_file() and "# devcache shim" in path.read_text():
                path.unlink()
        source = ["# Generated by devcache; cache paths are selected per command."]
        for command in commands:
            call = f"{shlex.quote(str(self.launcher))} run {shlex.quote(command)}"
            atomic_write(self.shims / command, f'#!/bin/sh\n# devcache shim\nexec {call} "$@"\n', 0o755)
            # Functions survive nvm/gvm changing PATH in the same shell command.
            source.append(f'function {command} {{ {call} "$@"; }}')
        source.extend([
            "_devcache_prepend_path() {",
            f"  local shim={shlex.quote(str(self.shims))}",
            f"  local bin={shlex.quote(str(self.launcher.parent))}",
            '  path=("$shim" "$bin" ${${path:#$shim}:#$bin})',
            "}",
            "autoload -Uz add-zsh-hook",
            "add-zsh-hook -d precmd _devcache_prepend_path 2>/dev/null",
            "add-zsh-hook precmd _devcache_prepend_path",
            "_devcache_prepend_path",
        ])
        atomic_write(self.shell_path, "\n".join(source) + "\n", 0o644)

    def install(self, no_shell=False):
        if self.launcher.exists():
            existing = self.launcher.read_text()
            if "# devcache launcher" not in existing and "devcache/app/devcache.py" not in existing:
                raise Error(f"{self.launcher} 已存在且不属于 devcache，未覆盖。")
        rows = self.scan()
        data_dir = self.home / ".local/share/devcache"
        if getattr(sys, "frozen", False):
            bundle = Path(sys.executable).parent
            if not (bundle / "_internal").is_dir():
                raise Error("预编译安装要求完整的目录安装包；请重新运行官方安装脚本。")
            runtimes = data_dir / "runtimes"
            runtimes.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=f"{VERSION}-", dir=runtimes))
            try:
                # Keep old runtimes: already-running commands can finish after an upgrade.
                shutil.copytree(bundle, stage / "runtime", symlinks=True)
            except BaseException:
                shutil.rmtree(stage)
                raise
            runtime = stage / "runtime/devcache"
            command = [str(runtime)]
            kind = "standalone"
        else:
            app_dir = data_dir / "app"
            app_dir.mkdir(parents=True, exist_ok=True)
            source = Path(__file__).read_text()
            runtime = app_dir / "devcache.py"
            atomic_write(runtime, source, 0o644)
            # Use the interpreter that actually ran and validated this installation.
            # Avoid selecting an unrelated Homebrew Python or a hard-coded user path.
            command = [sys.executable, str(runtime)]
            kind = "python"
        command += ["--home", str(self.home)]
        call = " ".join(shlex.quote(arg) for arg in command)
        launcher = f'#!/bin/sh\n# devcache launcher\nexec {call} "$@"\n'
        atomic_write(self.launcher, launcher, 0o755)
        atomic_write(data_dir / "install.json", json.dumps(
            {"version": VERSION, "kind": kind, "runtime": str(runtime)}, indent=2) + "\n")
        self.write_integration(rows)
        if no_shell:
            print(f"已安装：{self.launcher}（未修改 shell 配置）")
            print("请将 ~/.local/bin 加入 PATH，或使用上述完整路径运行。")
            return
        rc = self.home / ".zshrc"
        old = rc.read_text() if rc.exists() else ""
        block = f'{START}\n[[ -r {shlex.quote(str(self.shell_path))} ]] && source {shlex.quote(str(self.shell_path))}\n{END}\n'
        pattern = re.compile(re.escape(START) + r"\n.*?" + re.escape(END) + r"\n?", re.S)
        updated = pattern.sub(lambda _: block, old) if START in old else old.rstrip("\n") + "\n\n" + block
        if updated != old:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = self.state / "backups" / f"zshrc-{stamp}"
            atomic_write(backup, old)
            atomic_write(rc.resolve() if rc.is_symlink() else rc, updated,
                         rc.stat().st_mode & 0o777 if rc.exists() else 0o644)
            print(f"zshrc 已备份：{backup}")
        print(f"已安装：{self.launcher}")
        print(f"新终端自动生效；当前终端运行：source {shlex.quote(str(self.shell_path))}")

    def uninstall(self, purge=False):
        rc = self.home / ".zshrc"
        if rc.exists():
            old = rc.read_text()
            updated = re.sub(re.escape(START) + r"\n.*?" + re.escape(END) + r"\n?", "", old, flags=re.S)
            if updated != old:
                atomic_write(self.state / "backups" / f"zshrc-uninstall-{dt.datetime.now():%Y%m%d-%H%M%S}", old)
                atomic_write(rc.resolve() if rc.is_symlink() else rc, updated, rc.stat().st_mode & 0o777)
        config = self.load()
        config["mode"] = "internal"
        self.save(config)
        for path in self.shims.glob("*"):
            if path.is_file() and "# devcache shim" in path.read_text():
                path.unlink()
        if self.shell_path.exists():
            self.shell_path.unlink()
        if purge:
            if self.launcher.exists():
                content = self.launcher.read_text()
                if "# devcache launcher" in content or "devcache/app/devcache.py" in content:
                    self.launcher.unlink()
            data_dir = self.home / ".local/share/devcache"
            for name in ("app", "runtimes"):
                path = data_dir / name
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            manifest = data_dir / "install.json"
            if manifest.exists():
                manifest.unlink()
            print("已卸载命令和运行时；配置、备份及内外盘缓存保留。请重新打开终端。")
        else:
            print("已移除新终端的接管；重新打开终端即可。缓存、备份和 devcache 命令保留。")


def split_tools(value):
    return [x.strip() for x in value.split(",") if x.strip()] if value is not None else None


def menu(app):
    while True:
        config = app.load()
        root, reason = app.effective(config)
        print(f"\ndevcache  当前 {'外盘' if root else '内盘'} · {reason}")
        print("1 查看工具和缓存占用\n2 一键配置外盘\n3 切回内盘\n4 切回外盘（缺盘自动回退）")
        print("5 复制内盘缓存到外盘\n6 清理内盘缓存（先预览再确认）\n7 选择参与切换的已安装工具\n0 退出")
        choice = input("选择：").strip()
        try:
            if choice == "0":
                return 0
            if choice == "1":
                app.report(sizes=True)
            elif choice == "2":
                disks = app.disks.list()
                for i, item in enumerate(disks, 1):
                    print(f"{i} {item['VolumeName']} · {item['MountPoint']} · {item['VolumeUUID']}")
                if not disks:
                    print("没有已挂载的外盘。")
                    continue
                index = input("选择外盘编号（回车取消）：").strip()
                if index.isdigit() and 0 < int(index) <= len(disks):
                    app.setup(disks[int(index) - 1]["VolumeUUID"])
                    if app.confirm("是否复制原缓存到外盘？"):
                        app.copy(ask_clean=True)
                    elif app.confirm("是否预览并询问清理内盘旧缓存？"):
                        app.clean()
            elif choice in ("3", "4"):
                app.use("internal" if choice == "3" else "external")
            elif choice == "5":
                app.copy(ask_clean=True)
            elif choice == "6":
                app.clean()
            elif choice == "7":
                rows = [r for r in app.scan() if r["available"]]
                for i, row in enumerate(rows, 1):
                    print(f"{i} {'[x]' if row['id'] in config['tools'] else '[ ]'} {row['id']}")
                selected = input("输入要切换启用状态的编号（回车取消）：").strip()
                if selected.isdigit() and 0 < int(selected) <= len(rows):
                    name = rows[int(selected) - 1]["id"]
                    app.toggle([name], name not in config["tools"])
        except (Error, OSError, ValueError) as exc:
            print(f"devcache: {exc}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="macOS 开发缓存外盘管理；外盘缺失默认回到原工具配置。")
    parser.add_argument("--home", type=Path, help="自定义用户根目录，用于独立安装或隔离测试")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="action")
    for name in ("status", "scan"):
        p = sub.add_parser(name, help="扫描已安装工具和真实缓存路径")
        p.add_argument("--sizes", action="store_true", help="统计目录磁盘占用")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("disks", help="列出已挂载外盘")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("setup", help="一键绑定外盘并启用已安装工具")
    p.add_argument("disk", help="卷名、挂载点或 UUID")
    p.add_argument("--tools", help="仅选择这些已安装工具，用逗号分隔；默认全部已适配工具")
    p.add_argument("--copy", action="store_true", help="复制并校验原缓存，保留内盘")
    p.add_argument("--ask-clean", action="store_true", help="配置/复制后预览并询问清理内盘")
    p = sub.add_parser("use", help="手动切换偏好，external 也始终允许自动回退")
    p.add_argument("mode", choices=("internal", "external", "auto"))
    for name in ("enable", "disable"):
        p = sub.add_parser(name, help="选择已安装工具是否参与切换")
        p.add_argument("tools", nargs="+")
    p = sub.add_parser("copy", help="复制内盘缓存到外盘并校验内容")
    p.add_argument("--tools")
    p.add_argument("--ask-clean", action="store_true")
    p = sub.add_parser("clean", help="预览并确认清理内盘缓存")
    p.add_argument("--tools")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="明确确认清理，不再询问")
    for name in ("run", "exec"):
        p = sub.add_parser(name, help="启动时决定缓存位置；exec 可运行任意命令")
        p.add_argument("argv", nargs=argparse.REMAINDER)
    p = sub.add_parser("install", help="安装用户级命令和 zsh 接管，备份 zshrc")
    p.add_argument("--no-shell", action="store_true", help="仅安装命令，不修改 zshrc")
    p = sub.add_parser("uninstall", help="移除 zsh 接管，保留缓存和备份")
    p.add_argument("--purge", action="store_true", help="同时删除 devcache 命令和运行时，保留配置/缓存/备份")
    args = parser.parse_args(argv)
    app = App(home=args.home)
    try:
        if args.action in ("status", "scan"):
            app.report(args.sizes, args.json)
        elif args.action == "disks":
            disks = app.disks.list()
            if args.json:
                print(json.dumps(disks, ensure_ascii=False, indent=2))
            else:
                for disk in disks:
                    print(f"{disk['VolumeName']}  {disk['MountPoint']}  UUID {disk['VolumeUUID']}")
        elif args.action == "setup":
            app.setup(args.disk, split_tools(args.tools))
            if args.copy:
                app.copy(split_tools(args.tools), args.ask_clean)
            elif args.ask_clean:
                app.clean(split_tools(args.tools))
        elif args.action == "use":
            app.use(args.mode)
        elif args.action in ("enable", "disable"):
            app.toggle(args.tools, args.action == "enable")
        elif args.action == "copy":
            app.copy(split_tools(args.tools), args.ask_clean)
        elif args.action == "clean":
            app.clean(split_tools(args.tools), args.dry_run, args.yes)
        elif args.action in ("run", "exec"):
            return app.run(args.argv, args.action == "exec")
        elif args.action == "install":
            app.install(args.no_shell)
        elif args.action == "uninstall":
            with app.lock(exclusive=True):
                app.uninstall(args.purge)
        elif sys.stdin.isatty():
            return menu(app)
        else:
            app.report()
        return 0
    except (Error, OSError, ValueError) as exc:
        print(f"devcache: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        return 130


if __name__ == "__main__":
    sys.exit(main())
