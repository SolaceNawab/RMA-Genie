---
name: draft-jira
description: Draft a Jira ticket for a hardware support case. Collects the device serial number and support plan tier (Platinum or Platinum+), then decrypts and extracts the customer's gather-diagnostics archive. Use when the user asks to draft, open, or file a Jira ticket for a support case, or to decrypt a gather-diagnostics bundle.
---

# draft-jira

Collect the case inputs, unpack the diagnostics bundle, then stop.

## Environment

Before Step 1, detect the platform with `uname -s` and locate `decrypt-cms`. If no binary is found, list the locations you tried, say so and stop. Don't try to work around it.

| `uname -s` | Platform | Where to find `decrypt-cms` (first hit wins) |
|---|---|---|
| `MINGW*` / `MSYS*` / `CYGWIN*` | Windows (Git Bash) | `decrypt-cms.exe` bundled with the support-gd-handler plugin: `~/.claude/plugins/cache/support-marketplace/support-gd-handler/<version>/scripts/decrypt-cms.exe`. Use the newest `<version>` directory present. |
| `Linux` / `Darwin` | Dev server or Unix workstation | 1. `decrypt-cms` on `$PATH` (`command -v decrypt-cms`). 2. The RND shared load at `/home/public/RND/loads/decrypt-cms/main/current/linux/amd64/decrypt-cms`. Use `current`, not a pinned version. If `current` is missing, use the newest numbered directory present. |

Tell the user which platform and binary you're using before Step 4.

Each platform authenticates differently:

- **Windows** (bundled `.exe`): Microsoft SSO device-code flow. The first decrypt in a session prints a sign-in URL and code.
- **Linux** (shared load): JWT through Vault. This works on dev servers (e.g. `dev3-193`) that have Vault access.

## Step 1 — Serial number

If the user already gave a serial number in their message, use it and do not ask again.

Otherwise ask for it in plain text:

> What's the device serial number?

Do not guess or invent a serial number. If the user doesn't know it, say the ticket can't be drafted without one and stop.

## Step 2 — Support plan

Ask with `AskUserQuestion` — exactly these two options, no others:

```
question: "Which support plan covers this device?"
header: "Plan"
multiSelect: false
options:
  - label: "Platinum"
    description: "Standard Platinum support plan."
  - label: "Platinum+"
    description: "Platinum+ support plan (extended coverage)."
```

## Step 3 — Diagnostics archive

Expect an encrypted, zipped bundle ending in `.tgz.p7m`.

If the platform has a native file picker, open it so the user can choose the archive. Say "Opening a file picker — choose the gather-diagnostics archive" first. The picker blocks until the user closes it, so run it in the foreground with `timeout: 600000`. It prints the chosen path, or nothing if the user cancels.

**Windows.** Use the Windows Forms dialog through PowerShell. The owner form is set to `TopMost` so the dialog opens in front of the terminal:

```bash
powershell.exe -NoProfile -STA -Command 'Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = "Select the gather-diagnostics archive"; $d.Filter = "Gather-diagnostics (*.p7m)|*.p7m|All files (*.*)|*.*"; $d.InitialDirectory = Join-Path $env:USERPROFILE "Downloads"; $o = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; if ($d.ShowDialog($o) -eq "OK") { $d.FileName }'
```

**macOS.**

```bash
osascript -e 'POSIX path of (choose file with prompt "Select the gather-diagnostics archive")'
```

**Linux.** Use a picker only when `$DISPLAY` or `$WAYLAND_DISPLAY` is set. Try `zenity --file-selection --title="Select the gather-diagnostics archive" --file-filter="*.p7m"` first, then `kdialog --getopenfilename "$HOME" "*.p7m"`. A headless dev server over SSH usually has neither.

**Fallback.** Ask in plain text if any of these apply:

- there is no picker
- the picker command fails
- the user cancels (empty output)

> What's the path to the gather-diagnostics archive?

**Check the file.** Whichever way you get the path, confirm the file exists before continuing. If it doesn't, report the path you tried and ask again. If the name doesn't end in `.tgz.p7m`, tell the user and ask whether to continue.

**Quote every path** in shell commands — these filenames often contain spaces and timestamps.

## Step 4 — Decrypt

Use the binary found under Environment. Pass no flags other than those shown below. The defaults are correct.

**Windows.** The bundled `.exe` takes the output path as a second positional argument:

```bash
"<decrypt-cms.exe>" "<archive>.tgz.p7m" "<archive>.tgz"
```

The device-code sign-in blocks until the user completes it, so run this command with `run_in_background`. Read its output until the Microsoft URL and code appear, then relay both to the user. Tell them the process continues automatically once they sign in. Wait for the command to exit.

**Linux / macOS.** Run from the directory containing the archive and pass the basename only. The binary writes the `.tgz` next to the input:

```bash
cd "<archive dir>" && "<decrypt-cms>" "<archive>.tgz.p7m"
```

**Both platforms:**

- **Auth or Vault failure:** show the error verbatim and stop. Do **not** try `-skip-vault`, `-key`, `-userVaultAddr`, or any other flag to get around it. Those flags change which credentials and environment are used, and that decision belongs to the user.
- **Find the output:** list the directory afterwards. Expect a `.tgz` next to the original. If nothing new appeared and the decrypted bytes went to stdout instead, re-run with stdout redirected to `"<archive>.tgz"`.
- **Keep the original:** leave the `.p7m` in place.

## Step 5 — Extract

Unpack into its own directory so the bundle doesn't spill into the working directory:

```bash
mkdir -p "<archive>-extracted"
tar -xzf "<archive>.tgz" -C "<archive>-extracted"
```

On Windows, add `--force-local`. Without it, GNU tar reads `C:\...` as a remote `host:path` and fails. You can also convert paths to `/c/...` form first.

On Windows, tar may also fail to create symlinks inside the bundle and exit 2 (e.g. `usr/sw/var/soltr_*/db` → `.dbHistory/db.NNNNNNNN`). Check that each symlink target exists in the extracted tree. If it does, the extraction is complete: tell the user which link is missing and continue. Any other tar error, show it verbatim and stop.

Then show the user the top-level layout of what came out.

## Step 6 — Confirm

Echo back:

```
Serial number: <serial>
Support plan:  <Platinum | Platinum+>
Diagnostics:   <path to extracted directory>
```

Then stop. **Do not create, draft, or submit a Jira ticket** — ticket creation is not implemented. Say the inputs and diagnostics are ready and that drafting isn't wired up yet.
