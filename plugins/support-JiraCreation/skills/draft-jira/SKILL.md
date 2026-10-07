---
name: draft-jira
description: Draft a Jira ticket for a hardware support case. Collects the device serial number, looks up its support plan tier (Platinum or Platinum+) in the Shipment Inventory Record, then decrypts and extracts the customer's gather-diagnostics archive. Use when the user asks to draft, open, or file a Jira ticket for a support case, or to decrypt a gather-diagnostics bundle.
---

# draft-jira

Collect the case inputs, unpack the diagnostics bundle, then stop.

## Environment

Before Step 1, detect the platform with `uname -s` and locate `decrypt-cms`. If no binary is found, list the locations you tried, say so and stop. Don't try to work around it.

| `uname -s` | Platform | Where to find `decrypt-cms` (first hit wins) |
|---|---|---|
| `MINGW*` / `MSYS*` / `CYGWIN*` | Windows (Git Bash) | `decrypt-cms.exe` bundled with the support-gd-handler plugin: `~/.claude/plugins/cache/support-marketplace/support-gd-handler/<version>/scripts/decrypt-cms.exe`. Use the newest `<version>` directory present. |
| `Linux` / `Darwin` | Dev server or Unix workstation | 1. `decrypt-cms` on `$PATH` (`command -v decrypt-cms`). 2. The RND shared load at `/home/public/RND/loads/decrypt-cms/main/current/linux/amd64/decrypt-cms`. Use `current`, not a pinned version. If `current` is missing, use the newest numbered directory present. |

Tell the user which platform and binary you're using before Step 5.

Each platform authenticates differently:

- **Windows** (bundled `.exe`): Microsoft SSO device-code flow. The first decrypt in a session prints a sign-in URL and code.
- **Linux** (shared load): JWT through Vault. This works on dev servers (e.g. `dev3-193`) that have Vault access.

## Step 1 — Serial number

If the user already gave a serial number in their message, use it and do not ask again.

Otherwise ask for it in plain text:

> What's the device serial number?

Do not guess or invent a serial number. If the user doesn't know it, say the ticket can't be drafted without one and stop.

## Step 2 — Support plan

Look the plan up in the Shipment Inventory Record with the support-rma plugin's serial lookup (the same one `/support-rma:inventory` uses). Find its script at `~/.claude/plugins/cache/coop-support-hack/support-rma/<version>/scripts/inventory.sh`, using the newest `<version>` directory present, and run it in the foreground:

```bash
"<inventory.sh>" lookup "<serial>" --state-dir "$HOME/.claude/plugins/data/support-rma-coop-support-hack"
```

It takes a few seconds. The output is JSON. Each entry in `results[]` has `support_tier` (`Platinum+` when Premium Onsite Support is Yes, otherwise `Platinum`), plus `customer`, `sheet` and `row`.

- **One result, or several that all have the same `support_tier`:** use that tier. Don't ask. Tell the user: "Support plan from the inventory sheet: <tier> (<customer>, row <row>)."
- **Several results with different tiers:** list each one (row, customer, tier, dest city), then ask the question below.
- **Serial in `not_found`, script not found, or a non-zero exit:** say why in one line (show the stderr message on a non-zero exit), then ask the question below.

Only ask when the lookup didn't settle it. Use `AskUserQuestion` with exactly these two options, no others:

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

## Step 4 — Output folder

Ask where the decrypted bundle should go. The default is the archive's own directory.

If the platform has a native folder picker, open it, starting in the archive's directory. Say "Opening a folder picker — choose where to put the decrypted gather-diagnostics" first. As in Step 3, run it in the foreground with `timeout: 600000`. It prints the chosen folder, or nothing if the user cancels.

**Windows.**

```bash
powershell.exe -NoProfile -STA -Command 'Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = "Choose where to put the decrypted gather-diagnostics"; $d.ShowNewFolderButton = $true; $d.SelectedPath = "<archive dir in C:\ form>"; $o = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; if ($d.ShowDialog($o) -eq "OK") { $d.SelectedPath }'
```

**macOS.**

```bash
osascript -e 'POSIX path of (choose folder with prompt "Choose where to put the decrypted gather-diagnostics" default location POSIX file "<archive dir>")'
```

**Linux.** Same `$DISPLAY` / `$WAYLAND_DISPLAY` rule as Step 3. Try `zenity --file-selection --directory --title="Choose where to put the decrypted gather-diagnostics" --filename="<archive dir>/"` first, then `kdialog --getexistingdirectory "<archive dir>"`.

**Fallback.** If there is no picker or the picker command fails, ask in plain text:

> Where should the decrypted gather-diagnostics go? (Press enter for `<archive dir>`.)

If the user cancels the picker or gives an empty answer, use the archive's directory and say so. If the folder doesn't exist, ask before creating it. Call the result `<output dir>`.

If `<output dir>/<archive>-extracted` already exists, tell the user and ask whether to extract over it or pick another folder.

## Step 5 — Decrypt

Use the binary found under Environment. Pass no flags other than those shown below. The defaults are correct.

**Windows.** The bundled `.exe` writes the `.tgz` next to the input. It ignores any second argument, so don't pass an output path:

```bash
"<decrypt-cms.exe>" "<archive dir>/<archive>.tgz.p7m"
```

The device-code sign-in blocks until the user completes it, so run this command with `run_in_background`. Read its output until the Microsoft URL and code appear, then relay both to the user. Tell them the process continues automatically once they sign in. Wait for the command to exit.

**Linux / macOS.** Run from the directory containing the archive and pass the basename only. The binary writes the `.tgz` next to the input:

```bash
cd "<archive dir>" && "<decrypt-cms>" "<archive>.tgz.p7m"
```

**Both platforms:**

- **Move to the output folder:** if `<output dir>` is different from `<archive dir>`, move the `.tgz` there once the decrypt exits: `mv "<archive dir>/<archive>.tgz" "<output dir>/"`.

- **Auth or Vault failure:** show the error verbatim and stop. Do **not** try `-skip-vault`, `-key`, `-userVaultAddr`, or any other flag to get around it. Those flags change which credentials and environment are used, and that decision belongs to the user.
- **Find the output:** list `<output dir>` afterwards and expect `<archive>.tgz` there. If nothing new appeared and the decrypted bytes went to stdout instead, re-run with stdout redirected to `"<output dir>/<archive>.tgz"`.
- **Keep the original:** leave the `.p7m` in place.

## Step 6 — Extract

Unpack into its own directory inside `<output dir>` so the bundle doesn't spill into it:

```bash
mkdir -p "<output dir>/<archive>-extracted"
tar -xzf "<output dir>/<archive>.tgz" -C "<output dir>/<archive>-extracted"
```

On Windows, add `--force-local`. Without it, GNU tar reads `C:\...` as a remote `host:path` and fails. You can also convert paths to `/c/...` form first.

On Windows, tar may also fail to create symlinks inside the bundle and exit 2 (e.g. `usr/sw/var/soltr_*/db` → `.dbHistory/db.NNNNNNNN`). Check that each symlink target exists in the extracted tree. If it does, the extraction is complete: tell the user which link is missing and continue. Any other tar error, show it verbatim and stop.

Then show the user the top-level layout of what came out.

## Step 7 — Confirm

Echo back:

```
Serial number: <serial>
Support plan:  <Platinum | Platinum+> (from inventory row <row> | chosen by user)
Diagnostics:   <path to extracted directory>
```

Then stop. **Do not create, draft, or submit a Jira ticket** — ticket creation is not implemented. Say the inputs and diagnostics are ready and that drafting isn't wired up yet.
