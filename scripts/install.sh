#!/usr/bin/env bash
# Install Fanfic Organizer from the latest GitHub release.
#
#   curl -fsSL https://raw.githubusercontent.com/BlakeASmith/fanfic-organizer/main/scripts/install.sh | bash
#
# Options are forwarded to install_plugin.py
# (--version TAG, --url URL, --zip PATH, --no-start, --no-install-calibre).
# PR build example (needs gh auth or GITHUB_TOKEN):
#   bash scripts/install.sh --url 'https://github.com/.../actions/runs/.../artifacts/...'

set -euo pipefail

REPO="BlakeASmith/fanfic-organizer"
REF="${FANFIC_ORGANIZER_INSTALL_REF:-main}"
INSTALL_PY_URL="https://raw.githubusercontent.com/${REPO}/${REF}/scripts/install_plugin.py"

die() {
  echo "fanfic-organizer install: error: $*" >&2
  exit 1
}

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  die "python3 not found. Install Python 3 or run from a checkout with python3 on PATH."
}

INSTALL_PY=""
CLEANUP_INSTALL_PY=false

resolve_install_py() {
  if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ "${BASH_SOURCE[0]}" != bash ]] && [[ "${BASH_SOURCE[0]}" != /dev/fd/* ]]; then
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${script_dir}/install_plugin.py" ]]; then
      INSTALL_PY="${script_dir}/install_plugin.py"
      CLEANUP_INSTALL_PY=false
      return 0
    fi
  fi
  INSTALL_PY="$(mktemp "${TMPDIR:-/tmp}/fanfic-organizer-install.XXXXXX")"
  CLEANUP_INSTALL_PY=true
  if ! curl -fsSL --retry 3 "${INSTALL_PY_URL}" -o "${INSTALL_PY}"; then
    rm -f "${INSTALL_PY}"
    die "Could not download ${INSTALL_PY_URL}"
  fi
}

python_bin="$(find_python)"
resolve_install_py
install_py="${INSTALL_PY}"
cleanup_install_py="${CLEANUP_INSTALL_PY}"

set +e
"${python_bin}" "${install_py}" "$@"
status=$?
set -e

if [[ "${cleanup_install_py}" == true ]]; then
  rm -f "${install_py}"
fi

exit "${status}"
