#!/usr/bin/env bash
# Bash entry point for read_inventory.ps1 (Claude Code's Bash tool on Windows is Git Bash).
# Usage: inventory.sh [--path FILE] [--state-dir DIR] [any read_inventory.ps1 args...]
# Unsubstituted ${user_config.*} placeholders are treated as empty.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
blank() { case "$1" in ''|'${user_config.'*|'${CLAUDE_'*) return 0 ;; *) return 1 ;; esac; }

args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --path)      blank "${2-}" || args+=(-Path "$(winpath "$2")"); shift 2 ;;
    --state-dir) blank "${2-}" || args+=(-StateDir "$(winpath "$2")"); shift 2 ;;
    --serial-column) blank "${2-}" || args+=(-SerialColumn "$2"); shift 2 ;;
    *)           args+=("$1"); shift ;;
  esac
done

exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(winpath "$here/read_inventory.ps1")" "${args[@]}"
