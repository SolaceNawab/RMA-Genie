#!/usr/bin/env bash
# Bash entry point (Claude Code's Bash tool on Windows is Git Bash).
# Usage: inventory.sh [lookup SERIAL...] [--path FILE] [--url URL] [--state-dir DIR] [other script args...]
#   lookup  -> lookup_serial.ps1 (RMA facts for the given serials, fast)
#   default -> read_inventory.ps1 (full listing)
# Windows runs the PowerShell scripts. Elsewhere (macOS / Linux), or with
# SUPPORT_RMA_BACKEND=python, inventory.py runs instead: same arguments, same JSON.
# Unsubstituted ${user_config.*} placeholders are treated as empty.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
blank() { case "$1" in ''|'${user_config.'*|'${CLAUDE_'*) return 0 ;; *) return 1 ;; esac; }

script=read_inventory.ps1; mode=list
args=()
if [ "${1-}" = lookup ]; then
  script=lookup_serial.ps1; mode=lookup; shift
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

if [ "${SUPPORT_RMA_BACKEND-}" != python ] && command -v powershell.exe >/dev/null 2>&1; then
  exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(winpath "$here/$script")" "${args[@]}"
fi
py=$(command -v python3 || command -v python || true)
[ -n "$py" ] || { echo "inventory.sh: needs PowerShell (Windows) or Python 3 (macOS / Linux); found neither" >&2; exit 3; }
exec "$py" "$here/inventory.py" "$mode" ${args[@]+"${args[@]}"}
