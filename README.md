# RMA Genie

Claude Code plugins for Solace Technical Support. The main one is **RMA Genie**: it looks up an
appliance serial in the Shipment Inventory Record, reads the customer's gather-diagnostics, and
raises a standardized OPS RMA ticket in Jira.

- [Plugins](#plugins)
- [Windows setup](#windows-setup)
- [macOS setup](#macos-setup)
- [Usage](#usage)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

## Plugins

| Plugin | Commands | What it does |
|---|---|---|
| [**support-rma-genie**](plugins/support-RMA-Genie) (RMA Genie) | `/support-rma-genie:raise`<br>`/support-rma-genie:inventory` | Serial lookup in the Shipment Inventory Record, then builds the OPS RMA Jira from the gather-diagnostics, shows a preview, and creates the ticket. |
| [support-jira](plugins/support-JiraCreation) | `/support-jira:draft-jira` | Collects the serial and support plan, then decrypts and extracts the gather-diagnostics. Jira drafting is still to come. |
| [support-rma](plugins/support-SupportPlans) | `/support-rma:inventory` | Standalone inventory lookup. **Being retired**: RMA Genie now has this built in. |

Most people only need **support-rma-genie**.

---

## Windows setup

One-time steps. Claude Code on Windows runs its commands in **Git Bash**.

### 1. Install the prerequisites

| Tool | How | Check |
|---|---|---|
| Claude Code | [Install guide](https://docs.claude.com/en/docs/claude-code/setup) | `claude --version` |
| Git for Windows (Git Bash) | [git-scm.com](https://git-scm.com/download/win) | `git --version` |
| Python 3 | [python.org](https://www.python.org/downloads/windows/). Tick **Add python.exe to PATH** during install. | `python --version` |
| Desktop Excel | Signed in to your Solace account (usually already installed) | |

If `python3` opens the Microsoft Store, that's the Store stub. Install Python from python.org as
above; the plugins also try `python` and `py`.

### 2. Install the plugins

In Claude Code:

```
/plugin marketplace add SolaceNawab/RMA-Genie
/plugin install support-rma-genie@coop-support-hack

/plugin marketplace add SolaceDev/support-marketplace
/plugin install support-gd-handler@support-marketplace
```

**support-gd-handler** ships `decrypt-cms.exe` (used to decrypt gather-diagnostics) and the
filedrop fetcher. Install its Python dependency once, in Git Bash:

```
python -m pip install -r ~/.claude/plugins/marketplaces/support-marketplace/plugins/support-gd-handler/requirements.txt
```

Then run `/reload-plugins` (or restart Claude Code).

### 3. Connect Jira

```
claude mcp add --transport http --scope user atlassian https://mcp.atlassian.com/v1/mcp
```

In Claude Code, run `/mcp`, select **atlassian**, choose **Authenticate**, and sign in with your
Solace Atlassian account (`sol-jira.atlassian.net`).

> If you're logged in to Claude Code with a claude.ai account that has the Atlassian connector
> enabled, that works too and you can skip this step.

### 4. Give it access to the Shipment Inventory Record

Pick one:

- **OneDrive sync (recommended, fastest).** Open the Operations SharePoint site in a browser →
  **Shared Documents** → on the **Ship Spreadsheet** *folder* (not the file), choose
  **Add shortcut to My files**. Once OneDrive syncs, the file appears under
  `C:\Users\<you>\Solace Corporation\Operations - Ship Spreadsheet\` and is found automatically.
- **Excel fallback (no setup).** If no synced copy is found, the plugin opens the sheet from
  SharePoint through your signed-in desktop Excel. This takes about 30–40 s and is cached for
  10 minutes.

### 5. Check it works

```
/support-rma-genie:inventory <serial>
```

You should get the customer, tier, MTCE eligibility, chassis, destination city and address.

The first time you decrypt a gather-diagnostics, `decrypt-cms.exe` shows a Microsoft sign-in
code. Sign in once; the token is cached in `~/.vault-token`.

---

## macOS setup

One-time steps. Tested on macOS (Apple Silicon) with Homebrew Python 3.14.

### 1. Install the prerequisites

| Tool | How | Check |
|---|---|---|
| Claude Code | [Install guide](https://docs.claude.com/en/docs/claude-code/setup) | `claude --version` |
| Homebrew | [brew.sh](https://brew.sh) | `brew --version` |
| Python 3 | `brew install python` | `python3 --version` |
| Go (to build decrypt-cms) | `brew install go` | `go version` |
| GitHub CLI | `brew install gh`, then `gh auth login` | `gh --version` |
| OneDrive for Mac | App Store, signed in to your Solace account | |

### 2. Install the plugins

In Claude Code:

```
/plugin marketplace add SolaceNawab/RMA-Genie
/plugin install support-rma-genie@coop-support-hack

/plugin marketplace add SolaceDev/support-marketplace
/plugin install support-gd-handler@support-marketplace
```

Install `requests` for the filedrop fetcher. Homebrew Python blocks a plain `pip install`
(PEP 668), so install it for your user:

```
python3 -m pip install --user --break-system-packages requests
```

Then run `/reload-plugins` (or restart Claude Code).

### 3. Connect Jira

```
claude mcp add --transport http --scope user atlassian https://mcp.atlassian.com/v1/mcp
```

In Claude Code, run `/mcp`, select **atlassian**, choose **Authenticate**, and sign in with your
Solace Atlassian account (`sol-jira.atlassian.net`).

> If you're logged in to Claude Code with a claude.ai account that has the Atlassian connector
> enabled, that works too. If you sign in through the company gateway (`ANTHROPIC_BASE_URL`),
> the claude.ai connector doesn't load, so use the command above.

### 4. Sync the Shipment Inventory Record

A Mac can't open the sheet through Excel, so it has to be synced with OneDrive.

1. In a browser, open the Operations SharePoint site → **Shared Documents**.
2. On the **Ship Spreadsheet** *folder* (not the file inside it), choose **Add shortcut to My files**.
3. Wait for OneDrive to finish syncing. The file appears at:
   ```
   ~/Library/CloudStorage/OneDrive-SolaceCorporation/Shortcuts/Operations - Ship Spreadsheet/Shipment Inventory Record.xlsx
   ```
   The plugin finds it automatically.

### 5. Let your terminal read OneDrive

macOS blocks terminal apps from cloud-storage folders until you allow it.

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Click **+**, add the app you run Claude Code in (Terminal, iTerm, IntelliJ, …) and turn it on.
3. **Quit the app completely (⌘Q) and reopen it.** Resume your session with `claude --continue`.

### 6. Build decrypt-cms

support-gd-handler only ships the Windows `.exe`, so build the Mac version once:

```
gh repo clone SolaceDev/decrypt-cms ~/repos/decrypt-cms
cd ~/repos/decrypt-cms && mkdir -p ~/bin && go build -trimpath -o ~/bin/decrypt-cms .
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc     # only if ~/bin isn't on your PATH yet
```

Open a new terminal and check with `decrypt-cms -h`. To update it later, `git pull` and run the
`go build` line again.

decrypt-cms signs in to **Vault** and has **AWS KMS** decrypt the bundle, so your Solace account
needs Vault access. No Mac build? Decrypt on a dev server, copy the **extracted folder** to
`~/Downloads`, and RMA Genie will pick it up.

### 7. Check it works

```
/support-rma-genie:inventory <serial>
```

You should get the customer, tier, MTCE eligibility, chassis, destination city and address, with
the source shown as "Live sheet (OneDrive sync …)".

---

## Usage

```
/support-rma-genie:raise                    # asks for the serial and walks you through the RMA
/support-rma-genie:raise S009004123         # serial given up front
/support-rma-genie:inventory S009004123     # inventory lookup only
/support-rma-genie:inventory                # list available serials (takes several minutes)
```

You can also just ask, e.g. *"raise an RMA for the failed PSU on this appliance"*.

RMA Genie finds extracted gather-diagnostics in the current folder and `~/Downloads`, or decrypts
an archive for you. It always shows a preview before creating anything in Jira. See the
[RMA Genie README](plugins/support-RMA-Genie/README.md) for the full flow.

## Updating

```
/plugin marketplace update coop-support-hack
/reload-plugins
```

**Renamed plugin:** RMA Genie used to be `support-rma-jira` (`/support-rma-jira:raise-rma`). If
you still have the old one:

```
/plugin marketplace update coop-support-hack
/plugin uninstall support-rma-jira@coop-support-hack
/plugin install support-rma-genie@coop-support-hack
/reload-plugins
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python3` opens the Microsoft Store (exit 49) — Windows | Install Python from python.org with **Add to PATH** ticked. |
| `powershell.exe: not found` (exit 127) or `permission denied` (exit 126) — macOS | Old plugin version. Update to support-rma-genie 0.8.0 or later ([Updating](#updating)). |
| `macOS blocked access to … (Operation not permitted)` (exit 3) | Give your terminal app Full Disk Access, then quit and reopen it ([macOS step 5](#5-let-your-terminal-read-onedrive)). |
| `no OneDrive-synced copy … was found` (exit 3) | The sheet isn't synced yet, or OneDrive is paused. On Windows, also check that desktop Excel is signed in. |
| Inventory data looks old | Check the OneDrive icon: syncing may be paused or behind. |
| `decrypt-cms binary not found` | Windows: install support-gd-handler. macOS: build it ([macOS step 6](#6-build-decrypt-cms)) and open a new terminal. |
| decrypt-cms runs but Vault / AWS access is refused | An account access issue, not your machine. Check it works on a dev server; request Vault access if not. |
| `No module named 'requests'` | Install `requests` (step 2 of your OS setup). |
| Jira tools missing | Run `/mcp` and make sure **atlassian** is connected and authenticated. |
