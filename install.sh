#!/bin/sh
# Install the standalone forgefy CLI binary (no Python required).
#
#   curl -fsSL https://raw.githubusercontent.com/Polybamz/forgefy-cli/main/install.sh | sh
#
# Downloads the latest GitHub Release asset for your OS/arch, installs it to
# ~/.local/bin/forgefy, and adds that directory to your shell's PATH if it
# isn't already there (same install-directory convention as rustup/Deno/Bun).
set -eu

REPO="Polybamz/forgefy-cli"
INSTALL_DIR="${FORGEFY_INSTALL_DIR:-$HOME/.local/bin}"

os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
  Linux)  platform="linux" ;;
  Darwin) platform="macos" ;;
  *)
    echo "forgefy install.sh: unsupported OS '$os'." >&2
    echo "Windows users: use install.ps1 instead, or 'pip install forgefy-cli' / 'pipx install forgefy-cli'." >&2
    exit 1
    ;;
esac

case "$arch" in
  x86_64|amd64) cpu="amd64" ;;
  arm64|aarch64) cpu="arm64" ;;
  *)
    echo "forgefy install.sh: unsupported architecture '$arch'." >&2
    echo "Try 'pip install forgefy-cli' or 'pipx install forgefy-cli' instead." >&2
    exit 1
    ;;
esac

asset="forgefy-${platform}-${cpu}"
url="https://github.com/${REPO}/releases/latest/download/${asset}"

mkdir -p "$INSTALL_DIR"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

echo "Downloading ${asset}..."
if ! curl -fsSL "$url" -o "$tmp"; then
  echo "forgefy install.sh: no release build for ${platform}/${cpu} yet (checked $url)." >&2
  echo "Try 'pip install forgefy-cli' or 'pipx install forgefy-cli' instead." >&2
  exit 1
fi

chmod +x "$tmp"
mv "$tmp" "$INSTALL_DIR/forgefy"
trap - EXIT

echo "Installed forgefy to $INSTALL_DIR/forgefy"

case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *)
    echo ""
    echo "$INSTALL_DIR is not on your PATH yet. Add this to your shell profile"
    echo "(~/.bashrc, ~/.zshrc, etc.) and open a new terminal:"
    echo ""
    echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
    echo ""
    ;;
esac

echo "Run 'forgefy --help' to get started."
