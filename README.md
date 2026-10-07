# coop_support_hack
This is the repo for hackathon

## Plugins

This repo is a Claude Code plugin marketplace.

```
/plugin marketplace add SolaceNawab/RMA-Genie
/plugin install support-rma@coop-support-hack
/plugin install support-jira@coop-support-hack
/plugin install support-rma-genie@coop-support-hack
```

| Plugin | What it does |
|---|---|
| [support-rma](plugins/support-SupportPlans) | `/support-rma:inventory`: available serial numbers from the live Shipment Inventory Record (RMA generation to follow) |
| [support-jira](plugins/support-JiraCreation) | `/support-jira:draft-jira`: collect serial number and support plan, then decrypt and extract the gather-diagnostics bundle (Jira drafting to follow) |
| [support-rma-genie](plugins/support-JiraTemplate) (RMA Genie) | `/support-rma-genie:raise`: build the OPS RMA Jira from an extracted gather-diagnostics bundle (Summary/Description per the Hardware Replacement Workflow), preview for edits, then create it via the Atlassian MCP |

> **Renamed:** the RMA plugin was `support-rma-jira` (`/support-rma-jira:raise-rma`). It's now
> **RMA Genie**: plugin `support-rma-genie`, command **`/support-rma-genie:raise`**. If you
> installed the old name, switch over once:
>
> ```
> /plugin marketplace update coop-support-hack
> /plugin uninstall support-rma-jira@coop-support-hack
> /plugin install support-rma-genie@coop-support-hack
> /reload-plugins
> ```

On a Mac, follow [macOS setup](#macos-setup) first.

## macOS setup

These are the one-time steps a Mac user needs before `/support-rma:inventory` and
`/support-rma-genie:raise` work. They were tested on macOS (Apple Silicon) with
Homebrew Python 3.14. On Windows none of this is needed.

### 1. Python 3

The inventory lookup and the gather-diagnostics parser are Python on macOS.
Check with `python3 --version`. If it's missing, run `brew install python`.

### 2. Install the plugins

```
/plugin marketplace add SolaceNawab/RMA-Genie
/plugin install support-rma@coop-support-hack
/plugin install support-rma-genie@coop-support-hack
/plugin install support-jira@coop-support-hack
```

You need **support-rma 0.4.0 or later** for macOS. Older versions fail with
`powershell.exe: not found` (exit 127) or `permission denied` (exit 126). To update:

```
/plugin marketplace update coop-support-hack
```

Then update the plugins from `/plugin`, and run `/reload-plugins` or restart Claude Code.

### 3. Connect Jira (Atlassian MCP)

If Claude Code signs in through the company gateway (`ANTHROPIC_BASE_URL` /
`ANTHROPIC_AUTH_TOKEN`) instead of a claude.ai account, the claude.ai Atlassian
connector doesn't load. Add Atlassian's MCP server directly:

```
claude mcp add --transport http --scope user atlassian https://mcp.atlassian.com/v1/mcp
```

Then, in Claude Code, run `/mcp`, select **atlassian**, choose **Authenticate**, and sign in
with your Solace Atlassian account (site `sol-jira.atlassian.net`).

### 4. Sync the Shipment Inventory Record with OneDrive

On Windows the lookup can open the sheet from SharePoint through Excel. On a Mac it can't,
so the file has to be synced.

1. Install **OneDrive for Mac** and sign in with your Solace account.
2. In a browser, open the Operations SharePoint site, then **Shared Documents**.
3. On the **Ship Spreadsheet** *folder* (not the file inside it), choose **Add shortcut to My files**.
4. Wait for OneDrive to finish syncing. The file appears at:
   ```
   ~/Library/CloudStorage/OneDrive-SolaceCorporation/Shortcuts/Operations - Ship Spreadsheet/Shipment Inventory Record.xlsx
   ```
   The scripts find it on their own and remember the path.

### 5. Let your terminal app read OneDrive

macOS blocks terminal apps from cloud-storage folders until you allow it. Without
this step the lookup fails with exit 3 and
`macOS blocked access to …/OneDrive-SolaceCorporation (Operation not permitted)`.

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Click **+**, add the app you run Claude Code in (Terminal, iTerm, Termius,
   IntelliJ, …), and turn it on. If the app appears under **Files and Folders**
   instead, allow its OneDrive / cloud-storage access there.
3. **Quit the app completely (⌘Q) and reopen it.** You can resume the session with `claude --continue`.

### 6. Check it works

```
/support-rma:inventory <serial>
```

You should get the customer, tier, MTCE eligibility, chassis, destination city and
address, with the source shown as "Live sheet (OneDrive sync …)".

### 7. decrypt-cms on a Mac (to decrypt gather-diagnostics)

`decrypt-cms` decrypts the customer's `.tgz.p7m`. support-gd-handler bundles only the
Windows `.exe`, and the dev servers have the Linux build, but the tool
(**SolaceDev/decrypt-cms**, internal Go) builds natively for macOS. Build it once:

```
brew install go
gh repo clone SolaceDev/decrypt-cms ~/repos/decrypt-cms
cd ~/repos/decrypt-cms && mkdir -p ~/bin && go build -trimpath -o ~/bin/decrypt-cms .
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc     # if ~/bin isn't on your PATH yet
```

Open a new terminal and check with `decrypt-cms -h`. support-gd-handler's filedrop fetch,
`/support-jira:draft-jira` and `/support-rma-genie:raise` then find it on the `PATH`. To
update it later, run `git pull` and the `go build` line again.

How it decrypts: it signs in to **Vault** (default `-method jwt`; `oidc` and `github` also
work), gets AWS credentials, and has **AWS KMS** decrypt the bundle's key. The private key
never leaves KMS. Your Solace account needs the Vault access for this. If decryption is
refused on the Mac, check whether it works for you on a dev server. If it doesn't work
there either, it's an access issue rather than a Mac issue.

Without a Mac build you can still decrypt and extract on a dev server, copy the
**extracted folder** to the Mac (e.g. `scp -r <you>@<dev-server>:<path>/gather-diagnostics_… ~/Downloads/`),
and run `/support-rma-genie:raise <serial>`. It finds extracted bundles in the current
folder and `~/Downloads` and picks the one whose chassis serial matches.

The filedrop fetch (support-gd-handler's `filedrop.py`) also needs Python `requests`.
Homebrew Python blocks plain `pip install` (PEP 668), so install it for your user only:

```
python3 -m pip install --user --break-system-packages requests
```

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `powershell.exe: not found` (exit 127) | support-rma older than 0.4.0. Update the plugin (step 2). |
| `permission denied` running `inventory.sh` (exit 126) | Same: older support-rma. Update it. |
| `macOS blocked access to … (Operation not permitted)` (exit 3) | Terminal app lacks Full Disk Access (step 5). Remember to quit and reopen it. |
| `no OneDrive-synced copy … was found` (exit 3) | Sheet not synced yet, or OneDrive is still syncing or paused (step 4). |
| `decrypt-cms binary not found` | Build it for macOS (step 7), and make sure `~/bin` is on your `PATH` in a new terminal. |
| decrypt-cms runs but Vault / AWS access is refused | Account access, not the Mac: check whether it works for you on a dev server; request Vault access if not. |
| `No module named 'requests'` | Install `requests` (step 7). |
| Lookup works but the data looks old | Check the OneDrive menu-bar icon: syncing may be paused or behind. |
