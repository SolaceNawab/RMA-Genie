# support-rma-jira

Claude Code plugin that raises standardized **OPS RMA** Jira tickets for Solace hardware appliance replacements. It automates Step 3 ("Open an RMA in Jira") of the [Hardware Replacement Workflow](https://sol-jira.atlassian.net/wiki/spaces/SS/pages/3758332688/Hardware+Replacement+Workflow).

**macOS:** see the repo README's [macOS setup](../../README.md#macos-setup). Decrypting gather-diagnostics isn't possible on a Mac, so decrypt on a dev server and copy the extracted folder over.


## What it does

1. Asks for the **chassis serial** of the faulty appliance first, then **looks it up in the Shipment Inventory Record** (via support-rma) for the customer, support tier, MTCE status, shipping address, contact and spare provider. These pre-fill the rest of the ticket.
2. Gets the gather-diagnostics: it reuses an already-extracted bundle whose chassis serial matches, otherwise it runs **`/support-jira:draft-jira`** to pick, decrypt and extract the archive. If the archive isn't downloaded yet, it can fetch it from `filedrop.solace.com` instead (support-gd-handler). It then reads the bundle (hostname, chassis part number and serial, SolOS, blades, power modules) and checks that the chassis serial matches the one you gave, which guards against using the HA mate's bundle.
3. Asks for the one part to replace (PSU / ADB / NAB / SFP / HBA / disk / fan / full appliance). It asks for the support tier only if the inventory lookup couldn't settle it. For Platinum+ it asks for the sub-level (Yes / Hybrid / In-Country Local Spares) or a **Nuance** (part not stocked at the partner depot, or partner out of stock).
4. Scans the bundle for failure evidence and suggests extra CLI sections to include. You choose which ones go in.
5. Collects the remaining case details (case #, SOL, priority, watchers, work order, ...) in a single prompt, with the inventory values pre-filled so you only correct them.
6. Searches OPS for an existing RMA on the same serial.
7. Builds the Summary using the runbook format for the tier, and the Description in the standard layout (header block with the shipping address in a code block, CLI in code blocks, issue summary). The part is named in the Summary only. It then **shows you a preview and lets you edit it until you approve**.
8. Creates the ticket (priority, `Flash`/`Maintech` label, **Relates** link to the SOL Jira). It finishes by printing the key, the watchers you need to add, and a Salesforce reminder.

If no Atlassian connection is available, it gives you paste-ready text instead.

Customer details (names, addresses, phone numbers) are never written to disk. They exist only in the conversation and the Jira ticket.

## Prerequisites

- **[support-jira](../support-JiraCreation)** from this marketplace (`/plugin install support-jira@coop-support-hack`). raise-rma uses `/support-jira:draft-jira` to decrypt and extract the bundle.
- **[support-gd-handler](https://github.com/SolaceDev/support-marketplace/tree/main/plugins/support-gd-handler)** from support-marketplace (`/plugin install support-gd-handler@support-marketplace`). Optional: raise-rma uses its `filedrop.py` and `handle_gds.py` only to fetch a bundle that isn't downloaded yet. Their requirements apply too:
  - `pip install -r ~/.claude/plugins/marketplaces/support-marketplace/plugins/support-gd-handler/requirements.txt` (installs `requests`)
  - `decrypt-cms`: bundled with the plugin on Windows. On Linux/macOS it must be on `PATH` or reachable through the RND shared load.
- **Python 3** (standard library only). It's used by `scripts/extract_gd.py`.
- **[support-rma](../support-SupportPlans)** from this marketplace (`/plugin install support-rma@coop-support-hack`), plus a OneDrive-synced copy of the Shipment Inventory Record or a signed-in desktop Excel. Without it, raise-rma asks for the tier and case details by hand.
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
/plugin install support-rma-jira@coop-support-hack
```

## Usage

```
/support-rma-jira:raise-rma                                   # asks for the serial, looks it up, decrypts the GD via draft-jira
/support-rma-jira:raise-rma S009004123                        # serial given up front
/support-rma-jira:raise-rma ./gather-diagnostics_<host>_...   # explicit extracted GD folder
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
support-rma-jira/
├── .claude-plugin/plugin.json
├── context/
│   ├── rma-jira-fields.md     # Summary/Description/field rules (from the Confluence runbook)
│   └── evidence-guide.md      # failure type → evidence sections and log patterns (DRAFT)
├── scripts/extract_gd.py      # parses cli-diagnostics.txt / gdh-diagnostics.txt; prints to stdout only
└── skills/raise-rma/SKILL.md
```

`extract_gd.py` can also be run on its own:
```
python3 scripts/extract_gd.py inventory <gd-folder>                     # JSON summary
python3 scripts/extract_gd.py sections  <gd-folder>                     # list available CLI sections
python3 scripts/extract_gd.py section   <gd-folder> "show hardware detail" "show product-key"
```

## Limitations and roadmap

- **Watchers have to be added by hand.** The Atlassian MCP has no add-watcher tool, so the skill resolves the names you give it and prints them for you to add.
- **The PSU part number** doesn't appear in `show hardware detail`, so the skill asks you for it.
- **`evidence-guide.md` is a draft.** Refine it as the team sees real cases.
- **Phase 2:** automatic entitlement and tier lookup by serial. [`support-rma`](../support-SupportPlans)'s `/support-rma:inventory` already reads the Shipment Inventory Record (customer, tier, maintenance eligibility, address), so it's the natural thing to hook in here. The Platinum Plus Maintenance sheet (spare quantity) is still to do.
- **Later:** support-log-buddy findings, a post-install update mode (Step 6.2), and pre-filling details from Salesforce through support-ticket-lookup.
