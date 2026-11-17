# Changelog

## [0.1.0] - 2026-11-17

Initial public release.

- Per-user macOS CLI with installed-tool detection and real cache-path discovery.
- UUID-based external-drive selection, manual internal/external modes, and automatic fallback on command launch.
- Cache adapters for npm/npx, Go/GVM, pnpm 10 cacheDir, uv, pip, Homebrew, and Yarn Classic.
- Optional cache copy with content verification and separately confirmed internal-cache cleanup.
- Native Apple Silicon and Intel packages, checksum-verified one-command installation, upgrades, and removal.
- Isolated tests for fallback, tool availability, safe cache maintenance, first installation, and upgrade preservation.

Known scope: pnpm store and GUI application caches remain in place; Yarn 2+ and other pnpm majors are not yet offered for switching. In-flight work cannot survive arbitrary drive removal transparently.
