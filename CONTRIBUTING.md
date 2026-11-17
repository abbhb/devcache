# Contributing

The CLI targets macOS and uses only the Python standard library. Do not add an adapter based solely on a known directory name: discover the installed executable, check that its cache query succeeds, and verify the version-specific routing mechanism. Missing or unsupported tools must not become switchable.

## Tests

```sh
python3 -m unittest discover -s tests -v
bash -n install.sh
```

Unit tests use temporary files and fake volumes. Never require a contributor to unmount a real drive or delete real caches. `tests/live_smoke.py` is an optional readback check for an already-configured Mac; it is not part of CI and must not be used as a substitute for isolated tests.

## Native builds

Build on the requested architecture, using Python 3.13 and the pinned build dependency:

```sh
python3 -m venv .venv-build
.venv-build/bin/python -m pip install -r requirements-build.txt
.venv-build/bin/python scripts/build.py --arch arm64
.venv-build/bin/python scripts/verify_bundle.py dist/devcache/devcache
```

Use `--arch x86_64` on an Intel Mac. Packaging uses a one-directory runtime to avoid unpacking a Python environment every time npm or Go is launched. The lifecycle check installs under a temporary user root with spaces, upgrades it, verifies retained settings, and removes only the managed program files.

Artifacts are `devcache-macos-arm64.tar.gz` and `devcache-macos-x86_64.tar.gz`, with per-build checksums. The release workflow assembles both, includes `install.sh`, and publishes a combined `SHA256SUMS`.

## Release

1. Update `VERSION` in `devcache.py` and `CHANGELOG.md` through a feature branch and PR.
2. Wait for Python compatibility tests and both native package/lifecycle checks.
3. Merge the approved PR, then create a matching `vX.Y.Z` tag on that commit.
4. Push the tag. The Release workflow builds both architectures again and publishes a GitHub Release only after they pass.
5. Download the public assets, verify checksums, and exercise the installer against an isolated user root.

Keep local state, volume UUIDs, cache contents, build environments and credentials out of commits. Do not push code directly to the target branch.
