# devcache

[![CI](https://github.com/abbhb/devcache/actions/workflows/ci.yml/badge.svg)](https://github.com/abbhb/devcache/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/abbhb/devcache)](https://github.com/abbhb/devcache/releases)

[中文](README.md) | English

Move macOS development caches to an external drive, with automatic fallback to the tools' original settings when that drive is unavailable.

- Detects installed, usable tools before offering a switch. Leftover cache folders do not count as installed software.
- Binds your chosen drive by volume UUID. Renaming it does not break routing.
- Checks the drive at each command launch. Reconnecting it restores external caching on the next command.
- Keeps compilers, version managers, installed packages, credentials and project files in place.
- Lets you copy and verify caches, preview internal disk usage, and explicitly confirm cleanup.
- Requires no daemon, global cache symlinks, or permanent edits to npm/Go configuration files.

## Install

For macOS Apple Silicon and Intel. Native packages include a runtime: **no Python, Homebrew or sudo required**.

```sh
curl -fsSL https://raw.githubusercontent.com/abbhb/devcache/main/install.sh | bash
```

The installer downloads the correct architecture from GitHub Releases and verifies its SHA-256 checksum. It installs per-user files and backs up `.zshrc` before adding shell integration. It does not bind a drive or remove cache data automatically.

Open a new terminal, run `devcache`, and choose the external-drive setup option. Console messages and the interactive menu are currently in Chinese; all subcommands below are available directly.

```sh
devcache disks
devcache setup "YOUR DRIVE NAME"
devcache scan --sizes
devcache status
devcache use internal
devcache use external           # Still falls back if the selected drive is missing
devcache enable npm go pnpm     # Only installed and supported tools can be enabled
devcache disable uv
devcache copy --ask-clean       # Verify copied contents, then offer cleanup
devcache clean --dry-run
devcache clean                  # Shows paths and sizes; defaults to No
devcache exec -- python3 -m pip cache dir
```

Re-run the installer to upgrade while preserving settings and data. To pin a release:

```sh
curl -fsSL https://raw.githubusercontent.com/abbhb/devcache/v0.1.0/install.sh | bash -s -- --version v0.1.0
```

Download and inspect `install.sh` first if preferred. The installer also accepts `--no-shell`, `--home /absolute/user/root`, and `--from /directory/with/release-assets` for offline installation. Prebuilt packages are built and tested on macOS 15 for both architectures and are ad-hoc signed, without Apple Developer notarization. Older macOS versions are not covered by this native-package test matrix.

## Supported tools

| Tool | Managed cache | Preserved |
| --- | --- | --- |
| npm / npx | npm cache directory | Global installation prefix and Node versions |
| Go / GVM | `GOCACHE`, `GOMODCACHE` | Go versions, `GOPATH`, project source |
| pnpm 10 | `cacheDir` | pnpm store and existing project dependencies |
| uv | `UV_CACHE_DIR` | Python installations and virtual environments |
| pip / pip3 | `PIP_CACHE_DIR` | Installed packages and environments |
| Homebrew | `HOMEBREW_CACHE` | Cellar, Caskroom and installed software |
| Yarn Classic 1 | `YARN_CACHE_FOLDER` | Installed dependencies |

Yarn 2+ may directly use cache files at runtime through PnP, so it is not offered for switching. Other pnpm majors are not yet validated. Detected tools without adapters are listed separately and cannot be enabled. GUI application caches are outside this release's scope.

## Behavior and limits

Two independent cache locations are used; there is no background synchronization. A fallback may require downloading dependencies or rebuilding them. An already-running task cannot move its open files when a drive is unplugged and may need to be restarted. The wrapper does not automatically retry commands with side effects.

Terminal integration uses both PATH shims and zsh functions, resolving the real command from the current PATH each time so NVM/GVM version changes still work. Existing IDE processes and absolute executable paths can bypass this integration; use `devcache exec -- COMMAND` to explicitly launch a process with managed cache settings. A missing drive restores the original environment/configuration rather than pinning one previously detected GVM cache path.

pnpm's `~/Library/Caches/pnpm` cache and its package store are different. The store remains in place to avoid store-affinity problems in existing projects. uv may copy instead of link packages when its cache and virtual environment are on different filesystems, making installs slower.

Cleanup requires explicit confirmation and is limited to recognized internal cache paths. Symlinked roots, project roots and broad or unknown custom paths are rejected. npm cleanup only removes `_cacache`, `_npx` and `_logs`; Homebrew cleanup only removes downloads. The tool does not uninstall old Homebrew packages. Stop installation/build tasks before copying or cleaning; locks cover commands started through devcache, not unrelated processes.

## Uninstall

```sh
devcache uninstall             # Disable integration, retain the CLI
devcache uninstall --purge     # Also remove the CLI and its runtimes
```

Both retain development caches, configuration and shell backups. Open a new terminal afterward. Upgrade runtimes are retained for already-running tasks until full removal.

## Source installation and development

The source CLI uses only the Python standard library and requires Python 3.10+ on macOS.

```sh
git clone https://github.com/abbhb/devcache.git
cd devcache
python3 devcache.py install
python3 -m unittest discover -s tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for native packaging and release instructions.

Configuration: `~/.config/devcache/config.json`. Installation: `~/.local/share/devcache/`. Launcher: `~/.local/bin/devcache`. Shell backups: `~/.config/devcache/backups/`. Set `DEVCACHE_VERBOSE=1` for per-command routing diagnostics.

## License

[MIT](LICENSE).
