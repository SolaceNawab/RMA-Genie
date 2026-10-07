#!/usr/bin/env bash
# Bash entry point for the PowerShell scripts (Claude Code's Bash tool on Windows is Git Bash).
# Usage: inventory.sh [lookup SERIAL...] [--path FILE] [--url URL] [--state-dir DIR] [other script args...]
#   lookup  -> lookup_serial.ps1 (RMA facts for the given serials, fast)
#   default -> read_inventory.ps1 (full listing)
# Unsubstituted ${user_config.*} placeholders are treated as empty.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
blank() { case "$1" in ''|'${user_config.'*|'${CLAUDE_'*) return 0 ;; *) return 1 ;; esac; }

script=read_inventory.ps1
args=()
if [ "${1-}" = lookup ]; then
  script=lookup_serial.ps1; shift
  serials=()
  while [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; do serials+=("$1"); shift; done
  [ ${#serials[@]} -gt 0 ] || { echo "inventory.sh: lookup needs at least one serial" >&2; exit 2; }
  args+=(-Serial "$(IFS=,; printf '%s' "${serials[*]}")")
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --path)      blank "${2-}" || args+=(-Path "$(winpath "$2")"); shift 2 ;;
    --url)       blank "${2-}" || args+=(-Url "$2"); shift 2 ;;
    --state-dir) blank "${2-}" || args+=(-StateDir "$(winpath "$2")"); shift 2 ;;
    --serial-column) blank "${2-}" || args+=(-SerialColumn "$2"); shift 2 ;;
    *)           args+=("$1"); shift ;;
  esac
done

exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(winpath "$here/$script")" "${args[@]}"
