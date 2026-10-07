# RMA Genie (support-rma-genie)

Claude Code plugin that raises standardized **OPS RMA** Jira tickets for Solace hardware appliance replacements. It automates Step 3 ("Open an RMA in Jira") of the [Hardware Replacement Workflow](https://sol-jira.atlassian.net/wiki/spaces/SS/pages/3758332688/Hardware+Replacement+Workflow).

**macOS:** see the repo README's [macOS setup](../../README.md#macos-setup). To decrypt gather-diagnostics on a Mac, build `decrypt-cms` natively (step 7 there).


## What it does

1. Asks for the **chassis serial** of the faulty appliance first, then **looks it up in the Shipment Inventory Record** (via support-rma) for the customer, support tier, MTCE status, shipping address, contact and spare provider. These pre-fill the rest of the ticket.
2. Gets the gather-diagnostics: one `extract_gd.py discover` call lists every extracted bundle (with its chassis serial) and every archive in the cwd and `~/Downloads`. A matching extracted bundle is used straight away. Otherwise you pick an archive from a list (no folder picker), and it is decrypted with `decrypt-cms` and extracted next to the archive. If the archive isn't downloaded yet, it can fetch it from `filedrop.solace.com` instead (support-gd-handler). It then reads the bundle (hostname, chassis part number and serial, SolOS, blades, power modules) and checks that the chassis serial matches the one you gave, which guards against using the HA mate's bundle.
3. Asks for the one part to replace (PSU / ADB / NAB / SFP / HBA / disk / fan / full appliance). It asks for the support tier only if the inventory lookup couldn't settle it. For Platinum+ it asks for the sub-level (Yes / Hybrid / In-Country Local Spares) or a **Nuance** (part not stocked at the partner depot, or partner out of stock).
4. Scans the bundle for failure evidence with `extract_gd.py scan` (about 2 s, no subagent): current-state health checks plus grouped, quoted log history. The relevant sections go into the draft, and you can drop any of them in the preview.
5. Searches OPS for an existing RMA on the same serial (in parallel with step 1).
6. Shows a **compact preview** (Summary, fields, header, evidence and Issue summary in full; CLI sections listed by name and line count) **together with** the checklist of missing case details (case #, SOL, priority, watchers, work order, ...), pre-filled from the inventory. You answer and approve in one reply.
7. Creates the ticket (priority, `Flash`/`Maintech` label, **Relates** link to the SOL Jira). It finishes by printing the key, the watchers you need to add, and a Salesforce reminder.

If no Atlassian connection is available, it gives you paste-ready text instead.

Customer details (names, addresses, phone numbers) are never written to disk. They exist only in the conversation and the Jira ticket.

## Prerequisites

- **[support-gd-handler](https://github.com/SolaceDev/support-marketplace/tree/main/plugins/support-gd-handler)** from support-marketplace (`/plugin install support-gd-handler@support-marketplace`). On Windows RMA Genie uses its bundled `decrypt-cms.exe` to decrypt the bundle, and on any platform it uses `filedrop.py` to fetch a bundle that isn't downloaded yet. support-jira's `/support-jira:draft-jira` is no longer needed. Its requirements apply too:
  - `pip install -r ~/.claude/plugins/marketplaces/support-marketplace/plugins/support-gd-handler/requirements.txt` (installs `requests`)
  - `decrypt-cms`: bundled with the plugin on Windows. On Linux/macOS it must be on `PATH` or reachable through the RND shared load.
- **Python 3** (standard library only). It's used by `scripts/extract_gd.py`.
- **[support-rma](../support-SupportPlans)** from this marketplace (`/plugin install support-rma@coop-support-hack`), plus a OneDrive-synced copy of the Shipment Inventory Record or a signed-in desktop Excel. Without it, RMA Genie asks for the tier and case details by hand.
- **An Atlassian MCP connection** to `sol-jira.atlassian.net`. Either of the following works:
  - **The Atlassian MCP server, added directly.** This works with any Claude Code login, including gateway/API-token setups:
    ```
    claude mcp add --transport http --scope user atlassian https://mcp.atlassian.com/v1/mcp
    ```
    Then run `/mcp`, select **atlassian**, choose **Authenticate**, and sign in with your Solace Atlassian account.
  - **The claude.ai Atlassian connector.** This needs Claude Code logged in with a claude.ai account (`/login`) that has the Atlassian connector enabled at claude.ai.

## Installation

```
/plugin marketplace add SolaceNawab/coop_support_hack
/plugin install support-rma-genie@coop-support-hack
```

## Usage

```
/support-rma-genie:raise                                  # asks for the serial, looks it up, finds or decrypts the GD
/support-rma-genie:raise S009004123                       # serial given up front
/support-rma-genie:raise ./gather-diagnostics_<host>_...  # explicit extracted GD folder
```

Or ask in plain language:
```
"raise an RMA for the failed PSU on this appliance"
"open an OPS RMA — full appliance replacement via Flash"
```

### Summary formats

| Tier | Format |
|------|--------|
| Platinum / Nuance (shipped from HQ) | `RMA: Replace <Part#> <Serial> for <Customer> in <Country>` |
| Platinum+ (Flash / Maintech) | `RMA: Replace <Part#> <Serial> for <Customer> via <Partner> <Location> Order# <WO>` |
Full field rules are in [`context/rma-jira-fields.md`](context/rma-jira-fields.md).

## Layout

```
support-rma-genie/   (folder: plugins/support-JiraTemplate)
├── .claude-plugin/plugin.json
├── context/
│   ├── rma-jira-fields.md     # Summary/Description/field rules (from the Confluence runbook)
│   └── evidence-guide.md      # failure type → evidence sections and log patterns (DRAFT)
├── scripts/extract_gd.py      # parses cli-diagnostics.txt / gdh-diagnostics.txt; prints to stdout only
└── skills/raise/SKILL.md
```

`extract_gd.py` can also be run on its own:
```
python3 scripts/extract_gd.py discover [<dir> ...]                      # extracted GDs (with serials) + archives, newest first
python3 scripts/extract_gd.py inventory <gd-folder>                     # JSON summary
python3 scripts/extract_gd.py scan      <gd-folder> <psu|adb|nab|sfp|hba|disk|fan|full>   # evidence scan
python3 scripts/extract_gd.py sections  <gd-folder>                     # list available CLI sections
python3 scripts/extract_gd.py section   <gd-folder> "show hardware detail" "show product-key"
```

## Limitations and roadmap

- **Watchers have to be added by hand.** The Atlassian MCP has no add-watcher tool, so the skill resolves the names you give it and prints them for you to add.
- **The PSU part number** doesn't appear in `show hardware detail`, so the skill asks you for it.
- **`evidence-guide.md` is a draft.** Refine it as the team sees real cases.
- **Phase 2:** automatic entitlement and tier lookup by serial. [`support-rma`](../support-SupportPlans)'s `/support-rma:inventory` already reads the Shipment Inventory Record (customer, tier, maintenance eligibility, address), so it's the natural thing to hook in here. The Platinum Plus Maintenance sheet (spare quantity) is still to do.
- **Later:** support-log-buddy findings, a post-install update mode (Step 6.2), and pre-filling details from Salesforce through support-ticket-lookup.
