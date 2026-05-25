#!/usr/bin/env bash
# Bootstrap a fresh Linux environment for working on hermes-agent.
#
# Built for the Claude Code for Web sandbox (paste this into the cloud
# environment's setup-script field) and reused by .devcontainer so local /
# Codespaces dev matches. Idempotent — safe to re-run.
#
# What it provisions:
#   * uv (pinned) — Python toolchain + package manager
#   * Python 3.14 + locked deps in .venv. `uv sync --locked` asserts uv.lock is
#     in sync with pyproject.toml (it fails loudly if a pin was bumped without
#     re-locking) and installs exactly that locked, reproducible set — the same
#     .[all,dev] package footprint CI installs (note: CI runs the suite on
#     3.11; 3.14 is newer than the CI interpreter). The platform-bound extras
#     (matrix, voice) are intentionally excluded — they don't build cleanly on
#     every arch.
#   * optional: Node workspace deps (ui-tui, web, website) via --with-node
#
# Deliberately NOT installed: ripgrep. The only tests that shell out to it skip
# gracefully when it's absent (tests/tools/test_search_hidden_dirs.py), and the
# Claude Code sandbox already ships `rg` on PATH.
#
# Network: works as-is under Claude Code for Web "Trusted" egress. For "Custom"
# egress, allowlist: astral.sh, github.com, objects.githubusercontent.com,
# pypi.org, files.pythonhosted.org (and registry.npmjs.org for --with-node).
#
# Usage:
#   scripts/cloud-setup.sh              # Python env only (fast)
#   scripts/cloud-setup.sh --with-node  # also install Node workspaces

set -euo pipefail

# Pinned uv version + sha256 of its install script. Bump both deliberately (and
# re-test) rather than tracking latest — consistent with this repo's exact-pin
# supply-chain policy. Regenerate the digest when bumping UV_VERSION:
#   curl -fsSL https://astral.sh/uv/<version>/install.sh | sha256sum
UV_VERSION="0.11.13"
UV_INSTALLER_SHA256="48cd5aca5d5671a3b3d5f61538cc8622e4434af63319115159990d8b0dd02416"

WITH_NODE=0
for arg in "$@"; do
  case "$arg" in
    --with-node) WITH_NODE=1 ;;
    -h | --help)
      echo "usage: scripts/cloud-setup.sh [--with-node]"
      exit 0
      ;;
    *)
      echo "error: unknown argument: $arg" >&2
      echo "usage: scripts/cloud-setup.sh [--with-node]" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── uv ────────────────────────────────────────────────────────────────────────
# The standalone installer drops uv in ~/.local/bin; put it on PATH first so an
# already-installed uv (e.g. from a devcontainer feature) is reused.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv > /dev/null 2>&1; then
  echo "▶ installing uv ${UV_VERSION} (sha256-verified installer)"
  uv_installer="$(mktemp)"
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o "$uv_installer"
  echo "${UV_INSTALLER_SHA256}  ${uv_installer}" | sha256sum -c - > /dev/null
  sh "$uv_installer"
  rm -f "$uv_installer"
fi
uv --version

# ── Python + locked deps ──────────────────────────────────────────────────────
# Creates .venv at the repo root, which scripts/run_tests.sh probes for.
echo "▶ syncing Python 3.14 environment from uv.lock (.[all,dev])"
uv sync --locked --python 3.14 --extra all --extra dev

# ── Node workspaces (optional) ────────────────────────────────────────────────
if [ "$WITH_NODE" -eq 1 ]; then
  if ! command -v npm > /dev/null 2>&1; then
    echo "error: --with-node given but npm is not on PATH (need Node 24+)" >&2
    exit 1
  fi
  for dir in ui-tui web website; do
    if [ -f "$dir/package.json" ]; then
      echo "▶ npm ci in $dir"
      (cd "$dir" && npm ci)
    fi
  done
fi

echo "✓ setup complete. Run the test suite with: scripts/run_tests.sh"
