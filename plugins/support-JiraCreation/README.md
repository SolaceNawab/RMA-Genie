# support-jira

Collects the inputs for a hardware support case and unpacks the customer's
gather-diagnostics bundle so it's ready for a Jira ticket. Ticket drafting
itself is not implemented yet. The skill stops once the inputs and diagnostics
are ready.

## Usage

| | |
|---|---|
| `/support-jira:draft-jira` | serial number → support plan (looked up) → pick archive → decrypt → extract |
| "decrypt this gather-diagnostics bundle" | the skill also triggers on plain requests |

The skill:
1. Asks for the device serial number.
2. Looks up the support plan (Platinum or Platinum+) in the Shipment Inventory
   Record through the support-rma plugin. It asks only if the serial isn't in the
   sheet, matches rows with different tiers, or the lookup fails.
3. Opens a native file picker to choose the `.tgz.p7m` archive, or asks for
   the path in text if no picker is available.
4. Decrypts the archive with `decrypt-cms`.
5. Extracts it into `<archive>-extracted/` next to the original.
6. Echoes back the serial, plan, and extracted path.

## Platform support

The skill detects the platform with `uname -s` and picks the decrypt tool to match:

| Platform | `decrypt-cms` | Sign-in |
|---|---|---|
| Windows (Git Bash) | `decrypt-cms.exe` bundled with the **support-gd-handler** plugin (support-marketplace) | Microsoft SSO device code. The token is cached in `~/.vault-token` |
| Linux / macOS | `decrypt-cms` on `$PATH`, else `/home/public/RND/loads/decrypt-cms/main/current/linux/amd64/decrypt-cms` | JWT through Vault (dev servers such as `dev3-193`) |

If no binary is found, the skill lists where it looked and stops.

File pickers: Windows Forms dialog (PowerShell), `osascript` on macOS, and
`zenity` / `kdialog` on Linux desktops.

## Requirements

- **Windows:** install the `support-gd-handler` plugin from support-marketplace.
  It ships `decrypt-cms.exe`.
- **Linux:** the RND shared loads mounted at `/home/public`, or `decrypt-cms` on `$PATH`.
- The **support-rma** plugin (`/plugin install support-rma@coop-support-hack`)
  for the support-plan lookup. Without it the skill asks for the plan instead.
- A Solace Microsoft account for the decrypt sign-in.
- The **atlassian** plugin (`/plugin install atlassian@claude-plugins-official`)
  for Jira access. The repo's `.claude/settings.json` enables it, so Claude Code
  offers to install it when you open the repo. Run `/mcp` once to sign in to
  `sol-jira.atlassian.net`.

## Known quirks

- On Windows, `tar` can't create some symlinks in the bundle. For example
  `usr/sw/var/soltr_*/db` → `.dbHistory/db.NNNNNNNN` fails, tar exits 2, and
  the target folder still extracts. No data is lost; only the shortcut is missing.
