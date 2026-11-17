#!/bin/bash
# devcache installer: macOS native releases; no Python or sudo required.
set -euo pipefail

repository="${DEVCACHE_REPOSITORY:-abbhb/devcache}"
release_version="${DEVCACHE_VERSION:-latest}"
install_home="${DEVCACHE_INSTALL_HOME:-$HOME}"
offline_dir=""
no_shell=0

usage() {
  cat <<'EOF'
Usage: bash install.sh [options]
  --version vX.Y.Z   Install a specific release (default: latest)
  --home DIRECTORY  Install for this user root (default: current home)
  --no-shell        Do not change .zshrc
  --from DIRECTORY  Install from local release assets, with SHA256SUMS
  -h, --help        Show this help

Re-run this installer to upgrade. Existing cache settings and data are retained.
EOF
}
die() { printf 'devcache installer: %s\n' "$*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "Missing value for $1"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) need_value "$@"; release_version="$2"; shift 2 ;;
    --home) need_value "$@"; install_home="$2"; shift 2 ;;
    --from) need_value "$@"; offline_dir="$2"; shift 2 ;;
    --no-shell) no_shell=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ "$(/usr/bin/uname -s)" == Darwin ]] || die "Only macOS is supported."
[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "Invalid repository."
[[ "$release_version" == latest || "$release_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Invalid release version."
[[ "$install_home" == /* ]] || die "--home must be an absolute path."

architecture="$(/usr/bin/uname -m)"
# Prefer a native arm64 build even when launched from a Rosetta terminal.
if [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" == 1 ]]; then
  architecture=arm64
fi
case "$architecture" in arm64|x86_64) ;; *) die "Unsupported architecture: $architecture" ;; esac
asset="devcache-macos-${architecture}.tar.gz"

work_dir="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/devcache-install.XXXXXXXX")"
cleanup() { /bin/rm -rf "$work_dir"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if [[ -n "$offline_dir" ]]; then
  [[ -d "$offline_dir" ]] || die "Local release directory not found."
  /bin/cp "$offline_dir/$asset" "$work_dir/$asset"
  /bin/cp "$offline_dir/SHA256SUMS" "$work_dir/SHA256SUMS"
else
  if [[ "$release_version" == latest ]]; then
    download_base="https://github.com/$repository/releases/latest/download"
  else
    download_base="https://github.com/$repository/releases/download/$release_version"
  fi
  printf 'Downloading devcache for macOS %s (%s)...\n' "$architecture" "$release_version"
  curl_options=(--fail --location --silent --show-error --proto '=https' --tlsv1.2 --retry 3)
  /usr/bin/curl "${curl_options[@]}" "$download_base/$asset" -o "$work_dir/$asset"
  /usr/bin/curl "${curl_options[@]}" "$download_base/SHA256SUMS" -o "$work_dir/SHA256SUMS"
fi

expected="$(/usr/bin/awk -v name="$asset" '$2 == name { print $1 }' "$work_dir/SHA256SUMS")"
[[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "Missing or ambiguous checksum for $asset."
actual="$(/usr/bin/shasum -a 256 "$work_dir/$asset" | /usr/bin/awk '{print $1}')"
[[ "$actual" == "$expected" ]] || die "Checksum verification failed; nothing was installed."

/usr/bin/tar -tzf "$work_dir/$asset" > "$work_dir/members"
while IFS= read -r member; do
  case "$member" in devcache|devcache/*) ;; *) die "Unexpected archive layout." ;; esac
  case "/$member/" in */../*|*/./*) die "Unsafe archive path." ;; esac
done < "$work_dir/members"
/usr/bin/tar -xzf "$work_dir/$asset" -C "$work_dir" --no-same-owner
binary="$work_dir/devcache/devcache"
[[ -x "$binary" && -d "$work_dir/devcache/_internal" ]] || die "Incomplete native package."
binary_version="$("$binary" --version)"
if [[ "$release_version" != latest && "$binary_version" != "${release_version#v}" ]]; then
  die "Package version does not match the requested release."
fi

install_args=(--home "$install_home" install)
if [[ "$no_shell" == 1 ]]; then install_args+=(--no-shell); fi
"$binary" "${install_args[@]}"
printf '\nInstalled devcache %s. Open a new terminal and run: devcache\n' "$binary_version"
printf 'Choose your own external drive with: devcache disks / devcache setup "DRIVE NAME"\n'
