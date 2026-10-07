# support-rma-jira

Claude Code plugin that raises standardized **OPS RMA** Jira tickets for Solace hardware appliance replacements. It automates Step 3 ("Open an RMA in Jira") of the [Hardware Replacement Workflow](https://sol-jira.atlassian.net/wiki/spaces/SS/pages/3758332688/Hardware+Replacement+Workflow).

## What it does

1. Reads an extracted gather-diagnostics bundle and pulls out the hostname, chassis part number and serial, SolOS version, blades and power modules. It asks you to confirm this is the faulty unit, which guards against using the HA mate's bundle.
2. Asks for the part type(s) (PSU / ADB / NAB / SFP / HBA / disk / fan / full appliance) and the support tier: **Platinum**, **Platinum+** (Yes / Hybrid / In-Country Local Spares), or a **Nuance** (part not stocked at the partner depot, or partner out of stock).
3. Scans the bundle for failure evidence and suggests extra CLI sections to include. You choose which ones go in.
4. Collects the shipping, contact, partner, work order, SOL and watcher details in a single prompt.
5. Searches OPS for an existing RMA on the same serial.
6. Builds the Summary using the runbook format for the tier, and the Description in the standard layout (header block, parts list, CLI in code blocks, issue summary). It then **shows you a preview and lets you edit it until you approve**.
7. Creates the ticket (priority, `Flash`/`Maintech` label, **Relates** link to the SOL Jira). It finishes by printing the key, the watchers you need to add, and a Salesforce reminder.

If no Atlassian connection is available, it gives you paste-ready text instead.

Customer details (names, addresses, phone numbers) are never written to disk. They exist only in the conversation and the Jira ticket.

## Prerequisites

- **An extracted gather-diagnostics bundle.** Decrypt and extract the customer's bundle first, with either [`support-jira`](../support-jira) (`/support-jira:draft-jira`) in this repo or `support-gd-handler` (`/support-gd-handler:fetch-gds`) from support-marketplace.
- **Python 3** (standard library only). It's used by `scripts/extract_gd.py`.
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
/support-rma-jira:raise-rma                                   # auto-discover extracted GD folders in the current directory
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
| Several parts | Part numbers joined with `and`, serial left out |

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
- **Phase 2:** automatic entitlement and tier lookup by serial. [`support-rma`](../support-rma)'s `/support-rma:inventory` already reads the Shipment Inventory Record (customer, tier, maintenance eligibility, address), so it's the natural thing to hook in here. The Platinum Plus Maintenance sheet (spare quantity) is still to do.
- **Later:** support-log-buddy findings, a post-install update mode (Step 6.2), and pre-filling details from Salesforce through support-ticket-lookup.
