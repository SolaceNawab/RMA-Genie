#!/usr/bin/env bash
# Link an RMA's buginfo folder to the Salesforce case's RTinfo folder.
# Usage: buginfo_link.sh CASE KEY [--host HOST] [--base DIR]
#   CASE = Salesforce case number (digits), KEY = Jira key (e.g. OPS-4879)
# Creates BASE/buginfo/KEY -> BASE/RTinfo/CASE. Runs locally when BASE/buginfo
# exists (dev server), otherwise over SSH on HOST (user@host, e.g.
# jwansa@dev2-185.sol-local), which must accept key login without a prompt.
# Never replaces an existing entry.
# Prints one "buginfo: ..." status line. Exit codes:
#   0 created, or already links to RTinfo/CASE
#   1 bad arguments
#   2 KEY already exists but points somewhere else (left alone)
#   3 buginfo not reachable (not mounted here and no HOST, or SSH failed)
# Unsubstituted ${user_config.*} placeholders are treated as empty.
set -uo pipefail

blank() { case "$1" in ''|'${user_config.'*|'${CLAUDE_'*) return 0 ;; *) return 1 ;; esac; }

case_no=${1-}; key=${2-}; shift 2 2>/dev/null || true
host=''; base=/home/public/RND/rnd_buginfo
while [ $# -gt 0 ]; do
  case "$1" in
    --host) host=${2-}; shift 2 ;;
    --base) base=${2-}; shift 2 ;;
    *) echo "buginfo: unknown option $1" >&2; exit 1 ;;
  esac
done

# Strict formats: these values end up in a remote shell command.
[[ $case_no =~ ^[0-9]{5,10}$ ]] || { echo "buginfo: bad case number '$case_no'" >&2; exit 1; }
[[ $key =~ ^[A-Z][A-Z0-9]*-[0-9]+$ ]] || { echo "buginfo: bad Jira key '$key'" >&2; exit 1; }
[[ $base =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo "buginfo: bad base '$base'" >&2; exit 1; }

# Runs on whichever host has the share; args: BASE CASE KEY
link_script='
B=$1; C=$2; K=$3; L="$B/buginfo/$K"; T="$B/RTinfo/$C"
[ -d "$B/buginfo" ] || { echo "buginfo: $B/buginfo not found on $(hostname)"; exit 3; }
if [ -e "$L" ] || [ -L "$L" ]; then
  ls -l "$L"
  if [ "$(readlink "$L")" = "$T" ]; then echo "buginfo: already exists"; exit 0; fi
  echo "buginfo: $K already exists and points elsewhere; left alone"; exit 2
fi
[ -d "$T" ] || echo "warning: RTinfo/$C does not exist yet"
ln -s "$T" "$L" || { echo "buginfo: ln failed"; exit 3; }
ls -l "$L"; echo "buginfo: created on $(hostname)"
'

if [ -d "$base/buginfo" ]; then
  bash -c "$link_script" _ "$base" "$case_no" "$key"
  exit $?
fi
if blank "$host"; then
  echo "buginfo: $base not mounted here and no SSH host set (plugin option buginfo_ssh_host, e.g. jwansa@dev2-185.sol-local)"
  exit 3
fi

# macOS: Apple's ssh reads passphrases from the Keychain; a Homebrew ssh earlier in PATH doesn't.
ssh_bin=ssh; [ "$(uname -s)" = Darwin ] && [ -x /usr/bin/ssh ] && ssh_bin=/usr/bin/ssh
out=$("$ssh_bin" -o BatchMode=yes -o ConnectTimeout=10 "$host" bash -s -- "$base" "$case_no" "$key" <<<"$link_script" 2>&1)
rc=$?
printf '%s\n' "$out"
case $rc in
  0|2|3) exit $rc ;;
  *) echo "buginfo: ssh to $host failed (exit $rc). Set up key login: ssh-copy-id $host"; exit 3 ;;
esac
