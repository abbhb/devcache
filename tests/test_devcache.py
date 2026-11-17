import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import devcache


class FakeDisks:
    def __init__(self, mount):
        self.online = True
        self.volume = {"VolumeName": "TestSSD", "VolumeUUID": "TEST-UUID",
                       "MountPoint": str(mount), "Internal": False, "WritableVolume": True}

    def info(self, identity):
        return self.volume if self.online else None

    def select(self, name):
        if self.online and name in ("TestSSD", "TEST-UUID"):
            return self.volume
        raise devcache.Error("not present")

    def list(self):
        return [self.volume] if self.online else []


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devcache-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.mount = self.root / "external with spaces"
        self.mount.mkdir()
        self.disks = FakeDisks(self.mount)
        self.app = devcache.App(home=self.home, env={"PATH": str(self.bin)}, disks=self.disks)
        self.mounted = patch("devcache.os.path.ismount", return_value=True)
        self.mounted.start()
        self.addCleanup(self.mounted.stop)

    def make_tool(self, name, code="print('ok')"):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + code + "\n")
        path.chmod(0o755)
        return path

    def add_npm(self):
        self.make_tool("npm", "import os\nprint(os.environ['DEVCACHE_TEST_CACHE'])")
        self.app.env["DEVCACHE_TEST_CACHE"] = str(self.home / ".npm")

    def setup_npm(self):
        self.add_npm()
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.setup("TestSSD", ["npm"])
        return self.app.load()

    def test_uninstalled_tools_are_absent_not_switchable(self):
        self.assertEqual(self.app.scan(), [])
        with self.assertRaisesRegex(devcache.Error, "不能启用"):
            self.app.setup("TestSSD", ["yarn"])
        self.assertFalse(self.app.config_path.exists())
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_existing_cache_without_executable_does_not_count(self):
        (self.home / "Library/Caches/Yarn").mkdir(parents=True)
        self.assertIsNone(self.app.scan_one("yarn"))

    def test_failed_probe_cannot_be_enabled(self):
        self.make_tool("npm", "raise SystemExit(1)")
        row = self.app.scan_one("npm")
        self.assertFalse(row["available"])
        with self.assertRaises(devcache.Error):
            self.app.installed_selection(["npm"])

    def test_setup_only_creates_installed_tool_cache(self):
        config = self.setup_npm()
        root = self.app.external_root(config)
        self.assertTrue((root / "npm").is_dir())
        self.assertFalse((root / "go-mod").exists())
        self.assertEqual(config["tools"], ["npm"])
        self.assertFalse((self.home / ".npm").exists())

    def test_missing_disk_falls_back_and_does_not_create_phantom_directory(self):
        config = self.setup_npm()
        self.disks.online = False
        before = sorted(self.root.rglob("*"))
        root, reason = self.app.effective(config)
        self.assertIsNone(root)
        self.assertIn("自动回退", reason)
        self.assertEqual(before, sorted(self.root.rglob("*")))

    def test_external_mode_does_not_become_internal_preference_on_fallback(self):
        config = self.setup_npm()
        self.disks.online = False
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.use("external")
        self.assertEqual(self.app.load()["mode"], "external")
        self.disks.online = True
        self.assertIsNotNone(self.app.effective(self.app.load())[0])

    def test_manual_internal_mode_never_uses_external(self):
        self.setup_npm()
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.use("internal")
        self.assertIsNone(self.app.effective(self.app.load())[0])

    def test_same_uuid_still_works_after_volume_rename(self):
        config = self.setup_npm()
        new_mount = self.root / "renamed disk"
        self.mount.rename(new_mount)
        self.disks.volume["MountPoint"] = str(new_mount)
        self.disks.volume["VolumeName"] = "renamed"
        self.assertTrue(str(self.app.external_root(config)).startswith(str(new_mount)))

    def test_same_name_wrong_uuid_is_rejected(self):
        config = self.setup_npm()
        self.disks.volume["VolumeUUID"] = "ANOTHER-UUID"
        self.assertIsNone(self.app.effective(config)[0])

    def test_directory_without_real_mount_is_rejected(self):
        config = self.setup_npm()
        with patch("devcache.os.path.ismount", return_value=False):
            self.assertIsNone(self.app.effective(config)[0])

    def test_readonly_volume_falls_back(self):
        config = self.setup_npm()
        self.disks.volume["WritableVolume"] = False
        self.assertIsNone(self.app.effective(config)[0])

    def test_missing_marker_falls_back_instead_of_silently_recreating(self):
        config = self.setup_npm()
        root = self.app.external_root(config)
        (root / devcache.MARKER).unlink()
        self.assertIsNone(self.app.effective(config)[0])
        self.assertFalse((root / devcache.MARKER).exists())

    def test_symlinked_destination_is_rejected_without_writing_through_it(self):
        config = self.setup_npm()
        root = self.app.external_root(config)
        (root / "npm").rmdir()
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (root / "npm").symlink_to(unrelated, target_is_directory=True)
        self.assertIsNone(self.app.effective(config)[0])
        self.assertEqual(list(unrelated.iterdir()), [])

    def test_nested_environment_restores_original_config_after_disk_disconnect(self):
        original = {"PATH": "/bin", "GOCACHE": "/custom/internal/go"}
        first = devcache.routed_environment(original, {"GOCACHE": "/Volumes/TestSSD/go", "npm_config_cache": "/Volumes/TestSSD/npm"})
        second = devcache.routed_environment(first, {})
        self.assertEqual(second, original)
        self.assertNotIn(devcache.META, second)

    def test_child_explicit_environment_override_is_preserved(self):
        env = devcache.routed_environment({}, {"GOCACHE": "/external/go"})
        env["GOCACHE"] = "/child/explicit/cache"
        self.assertEqual(devcache.base_environment(env)["GOCACHE"], "/child/explicit/cache")

    def test_internal_run_does_not_force_old_gvm_version_cache(self):
        self.make_tool("go")
        self.app.env["GOPATH"] = str(self.home / ".gvm/pkgsets/new/global")
        self.app.save({"version": 1, "mode": "external", "disk": None, "tools": ["go"]})
        with patch("devcache.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 0
            self.assertEqual(self.app.run(["go", "version"]), 0)
            env = popen.call_args.kwargs["env"]
            self.assertNotIn("GOMODCACHE", env)
            self.assertNotIn("GOCACHE", env)
            self.assertIn("pkgsets/new", env["GOPATH"])

    def test_run_uses_current_path_for_version_managers(self):
        self.setup_npm()
        alt = self.root / "another-node-version"
        alt.mkdir()
        exe = alt / "npm"
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        self.app.env["PATH"] = str(self.app.shims) + ":" + str(alt) + ":" + str(self.bin)
        with patch("devcache.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 23
            self.assertEqual(self.app.run(["npm", "hello", "argument with spaces"]), 23)
            self.assertEqual(popen.call_args.args[0], [str(exe), "hello", "argument with spaces"])
            self.assertIn(str(self.mount), popen.call_args.kwargs["env"]["npm_config_cache"])

    def test_another_installation_shim_is_not_mistaken_for_an_installed_tool(self):
        foreign = self.root / "another user/.local/share/devcache/shims"
        foreign.mkdir(parents=True)
        shim = foreign / "npm"
        shim.write_text("#!/bin/sh\n# devcache shim\nexit 77\n")
        shim.chmod(0o755)
        self.app.env["PATH"] = str(foreign) + ":" + str(self.bin)
        self.assertIsNone(self.app.real_path("npm"))
        self.assertIsNone(self.app.scan_one("npm"))
        actual = self.make_tool("npm")
        self.assertEqual(self.app.real_path("npm"), str(actual))

    def test_corrupt_config_does_not_block_real_tool_execution(self):
        self.add_npm()
        self.app.config_path.parent.mkdir(parents=True)
        self.app.config_path.write_text("{bad json")
        with patch("devcache.subprocess.Popen") as popen, contextlib.redirect_stderr(io.StringIO()):
            popen.return_value.wait.return_value = 0
            self.assertEqual(self.app.run(["npm", "--version"]), 0)
            self.assertNotIn("npm_config_cache", popen.call_args.kwargs["env"])

    def test_npm_clean_preview_and_cancel_preserve_all_files(self):
        self.setup_npm()
        folder = self.home / ".npm/_cacache"
        folder.mkdir(parents=True)
        (folder / "payload").write_text("keep")
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.clean(dry_run=True)
            with patch.object(self.app, "confirm", return_value=False):
                self.app.clean()
        self.assertEqual((folder / "payload").read_text(), "keep")

    def test_confirmed_npm_clean_only_deletes_known_cache_children(self):
        self.setup_npm()
        folder = self.home / ".npm/_cacache"
        folder.mkdir(parents=True)
        (folder / "payload").write_text("cache")
        personal = self.home / ".npm/personal-file"
        personal.write_text("keep")
        root = self.app.external_root(self.app.load())
        (root / "npm/external-file").write_text("keep external")
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.clean(yes=True)
        self.assertFalse(folder.exists())
        self.assertEqual(personal.read_text(), "keep")
        self.assertEqual((root / "npm/external-file").read_text(), "keep external")

    def test_cleanup_rejects_home_project_and_symlink(self):
        for path in (self.home, self.home / "Projects/example"):
            with self.assertRaises(devcache.Error):
                self.app.safe_source({"key": "go-mod", "path": str(path)})
        target = self.home / "Library/Caches/go-build"
        target.parent.mkdir(parents=True)
        target.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(devcache.Error):
            self.app.safe_source({"key": "go-build", "path": str(target)})

    def test_cleanup_cannot_run_while_managed_command_is_active(self):
        with self.app.lock():
            with self.assertRaises(devcache.Error):
                with self.app.lock(exclusive=True):
                    self.fail("exclusive maintenance lock unexpectedly acquired")

    def test_copy_preserves_internal_source_and_verifies_payload(self):
        self.setup_npm()
        cache = self.home / ".npm/_cacache"
        cache.mkdir(parents=True)
        (cache / "payload").write_bytes(b"cache contents\x00\xff")
        (cache.parent / "personal-file").write_text("not a cache")
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.copy()
        root = self.app.external_root(self.app.load())
        self.assertEqual((root / "npm/_cacache/payload").read_bytes(), (cache / "payload").read_bytes())
        self.assertTrue((cache / "payload").exists())
        self.assertFalse((root / "npm/personal-file").exists())

    def test_new_pnpm_major_after_version_switch_is_not_silently_routed(self):
        self.make_tool("pnpm", "print('11.0.0')")
        self.app.save({"version": 1, "mode": "external", "disk": None, "tools": ["pnpm"]})
        with patch("devcache.subprocess.Popen") as popen, patch.object(self.app, "probe", return_value="11.0.0"):
            popen.return_value.wait.return_value = 0
            self.app.run(["pnpm", "--version"])
            self.assertNotIn("npm_config_cache_dir", popen.call_args.kwargs["env"])

    def test_go_cleanup_calls_native_program_with_each_internal_target(self):
        self.make_tool("go")
        build = self.home / "Library/Caches/go-build"
        module = self.home / ".gvm/pkgsets/go1.24.2/global/pkg/mod"
        build.mkdir(parents=True)
        module.mkdir(parents=True)
        (build / "cached").write_text("cache")
        (module / "cached").write_text("module")
        rows = [{"id": "go", "available": True, "executable": str(self.bin / "go"), "targets": [
            {"key": "go-build", "env": "GOCACHE", "path": str(build)},
            {"key": "go-mod", "env": "GOMODCACHE", "path": str(module)}]}]
        with patch.object(self.app, "maintenance_rows", return_value=rows), patch("devcache.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()), patch("devcache.size_of", return_value=5):
            run.return_value.returncode = 0
            self.app.clean(names=["go"], yes=True)
        self.assertEqual(len(run.call_args_list), 2)
        first, second = run.call_args_list
        self.assertEqual(first.args[0], [str(self.bin / "go"), "clean", "-cache"])
        self.assertEqual(first.kwargs["env"]["GOCACHE"], str(build))
        self.assertEqual(second.args[0], [str(self.bin / "go"), "clean", "-modcache"])
        self.assertEqual(second.kwargs["env"]["GOMODCACHE"], str(module))
        self.assertEqual(second.kwargs["env"]["GOTOOLCHAIN"], "local")

    def test_copy_conflict_does_not_overwrite_or_enter_cleanup(self):
        self.setup_npm()
        cache = self.home / ".npm/_cacache"
        cache.mkdir(parents=True)
        (cache / "payload").write_text("internal data")
        root = self.app.external_root(self.app.load())
        (root / "npm/_cacache").mkdir()
        (root / "npm/_cacache/payload").write_text("external data")
        with patch.object(self.app, "clean") as clean, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(devcache.Error, "校验未通过"):
                self.app.copy(ask_clean=True)
            clean.assert_not_called()
        self.assertEqual((cache / "payload").read_text(), "internal data")
        self.assertEqual((root / "npm/_cacache/payload").read_text(), "external data")

    def test_install_is_idempotent_and_only_wraps_installed_selected_commands(self):
        self.setup_npm()
        rc = self.home / ".zshrc"
        rc.write_text("# existing settings\nexport EXAMPLE=keep\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.install()
            once = rc.read_text()
            self.app.install()
        self.assertEqual(rc.read_text(), once)
        self.assertEqual(once.count(devcache.START), 1)
        self.assertIn("EXAMPLE=keep", once)
        self.assertTrue((self.app.shims / "npm").exists())
        self.assertFalse((self.app.shims / "yarn").exists())
        self.assertFalse((self.app.shims / "go").exists())
        cp = subprocess.run(["/bin/zsh", "-fc", f"source {devcache.shlex.quote(str(self.app.shell_path))}; whence -w npm; whence -w yarn"], capture_output=True, text=True)
        self.assertIn("npm: function", cp.stdout)
        self.assertIn("yarn: none", cp.stdout)
        self.assertNotIn("parse error", cp.stderr)

    def test_uninstall_preserves_settings_and_all_caches(self):
        self.setup_npm()
        rc = self.home / ".zshrc"
        rc.write_text("# user settings\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.install()
            self.app.uninstall()
        self.assertIn("# user settings", rc.read_text())
        self.assertNotIn(devcache.START, rc.read_text())
        self.assertTrue(self.app.config_path.exists())
        self.assertTrue(self.app.launcher.exists())
        self.assertEqual(self.app.load()["mode"], "internal")
        self.assertTrue((self.mount / "DevCaches").exists())

    def test_installed_launcher_keeps_custom_home_and_handles_spaces(self):
        self.setup_npm()
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.install(no_shell=True)
        self.assertFalse((self.home / ".zshrc").exists())
        env = dict(os.environ)
        env.pop("DEVCACHE_STATE_DIR", None)
        cp = subprocess.run([str(self.app.launcher), "--version"], env=env, capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), devcache.VERSION)
        self.assertIn(str(self.home), self.app.launcher.read_text())

    def test_install_preserves_existing_configuration_on_upgrade(self):
        self.setup_npm()
        before = self.app.config_path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.install()
            self.app.install()
        self.assertEqual(before, self.app.config_path.read_bytes())

    def test_install_and_uninstall_preserve_zshrc_symlink(self):
        self.setup_npm()
        original = self.root / "dotfiles-zshrc"
        original.write_text("# dotfile original\n")
        rc = self.home / ".zshrc"
        rc.symlink_to(original)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.install()
            self.assertTrue(rc.is_symlink())
            self.assertIn(devcache.START, original.read_text())
            self.app.uninstall()
        self.assertTrue(rc.is_symlink())
        self.assertIn("# dotfile original", original.read_text())
        self.assertNotIn(devcache.START, original.read_text())

    def test_purge_removes_only_managed_program_and_preserves_cache_and_backups(self):
        self.setup_npm()
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.install()
            self.app.uninstall(purge=True)
        self.assertFalse(self.app.launcher.exists())
        self.assertFalse((self.home / ".local/share/devcache/app").exists())
        self.assertTrue(self.app.config_path.exists())
        self.assertTrue((self.mount / "DevCaches").exists())
        self.assertTrue(list((self.app.state / "backups").iterdir()))

    def test_installer_does_not_overwrite_unrelated_command(self):
        self.app.launcher.parent.mkdir(parents=True)
        self.app.launcher.write_text("#!/bin/sh\necho unrelated\n")
        with self.assertRaisesRegex(devcache.Error, "不属于 devcache"):
            self.app.install()
        self.assertIn("unrelated", self.app.launcher.read_text())

    def test_noninteractive_clean_requires_explicit_confirmation(self):
        with patch("devcache.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(devcache.Error, "非交互"):
                self.app.confirm("clean?")

    def test_end_to_end_offline_invocation_uses_default_and_preserves_exit_code(self):
        tool = self.make_tool("npm", "import os,sys,json\nprint(json.dumps({'cache':os.environ.get('npm_config_cache'),'args':sys.argv[1:]}))\nraise SystemExit(17)")
        config = {"version": 1, "mode": "external", "tools": ["npm"],
                  "disk": {"uuid": "00000000-0000-0000-0000-000000000000", "name": "missing", "root": "DevCaches/test"}}
        self.app.save(config)
        env = dict(os.environ, DEVCACHE_STATE_DIR=str(self.app.state), PATH=str(self.bin))
        env.pop("npm_config_cache", None)
        cp = subprocess.run([sys.executable, str(Path(devcache.__file__)), "run", "npm", "argument with spaces"], env=env, capture_output=True, text=True)
        self.assertEqual(cp.returncode, 17, cp.stderr)
        result = json.loads(cp.stdout)
        self.assertIsNone(result["cache"])
        self.assertEqual(result["args"], ["argument with spaces"])


if __name__ == "__main__":
    unittest.main()
