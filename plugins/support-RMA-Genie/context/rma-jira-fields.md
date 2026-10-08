# OPS RMA Jira: fields, formats and rules

**Source of truth:** [Hardware Replacement Workflow (Confluence, SS space)](https://sol-jira.atlassian.net/wiki/spaces/SS/pages/3758332688/Hardware+Replacement+Workflow). This file encodes Step 3 of that workflow (raise the OPS RMA). If the two disagree, Confluence wins; flag the difference to the user.

All examples use placeholders or synthetic values. Never copy real customer data into this file.

## Contents

1. [Support tiers](#1-support-tiers)
2. [Summary decision table](#2-summary-decision-table)
3. [Description template](#3-description-template)
4. [Field values](#4-field-values)
5. [Part-number lookup guide](#5-part-number-lookup-guide)
6. [Glossary](#6-glossary)

---

## 1. Support tiers

The user picks one of **Platinum / Platinum+ / Nuances**.

| Tier | How the part ships | Install | Return |
|---|---|---|---|
| **Platinum** (Standard Support) | Direct from HQ (Ottawa), next business day | Customer self-installs | Solace pays return shipping; the customer coordinates the return |
| **Platinum+** (Rapid Hardware Repair, RHR; formerly Premium Onsite Support) | Field service partner (Flash Global, Maintech, Fujitsu) from a local depot | Depends on sub-level (below) | Handled by the partner / FE |
| **Nuances** (exceptions to Platinum+) | Direct from Ottawa even though the customer is Platinum+ | As agreed with the customer | As agreed |

Platinum+ sub-levels:

| Sub-level | Meaning |
|---|---|
| **Yes** | Local part + Field Engineer (FE) on site within 4h |
| **Hybrid** | Local part via the partner (e.g. 4h), no FE; the customer self-installs |
| **In-Country Local Spares** | Local partner shipment, NBD or best effort, no FE |

Nuance types:

| Nuance | Meaning |
|---|---|
| **(i) Part not stocked at partner depot** | Some SFPs, non-standard configs. Ships direct from Ottawa. |
| **(ii) Partner out of stock** (Spare Qty = 0) | Emergency direct shipment from Ottawa while the partner depot is backfilled. There is **no separate backfill RMA**: this RMA itself cues Ops to backfill the depot. Say so in the Description. |

Nuances use the **direct (Platinum) Summary format**; the exception is explained in the Description's "Support level" line.

## 2. Summary decision table

Always start with the literal prefix `RMA: ` (a live audit found 10/50 tickets without it).

| Tier | Summary format |
|---|---|
| Platinum or Nuances | `RMA: Replace <Failed Part#> <Chassis S/N> for <Customer> in <Country>` |
| Platinum+ | `RMA: Replace <Failed Part#> <Chassis S/N> for <Customer> via <Partner> <Location> Order# <WO#>` |

Rules:
- **One part per RMA.** The Summary is the only place the part is named; the Description has no parts list.
- **Full appliance:** Part# = the chassis product # (e.g. `CHS-3560AC-05-A`).
- **Customer** = the short business name the user supplies (e.g. `ACME`), not the legal name.
- **Partner** = `Flash`, `Maintech` or `Fujitsu`; **Location** = the depot city/country (e.g. `Singapore`).
- **Order#** = the partner's order (work order) number, for **Flash and Maintech** only. RMA Genie asks for it as soon as the Platinum Plus Maintenance lookup identifies the partner. If it isn't raised yet, write `Order# TBD` and point it out in the preview. **Fujitsu:** leave out the `Order#` part (`… via Fujitsu <Location>`) unless the user gives one.
- **Platinum+ only, Platinum Plus Maintenance sheet** (`PlatinumPlusMaintenance.xlsx`): per part `Support By` (FLASH / MAINTECH), `Response Time` (4H = Yes, 4H PART ONLY / HYBRID = Hybrid, BEST EFFORT / SHIPS NBD = In-Country Local Spares), `Bin` (partner + depot, e.g. `FLASH HEATHROW`, `MAIN SINGAPORE`), `Spare Qty` (0 / `NO SPARE` = Nuance (ii)), `Solace Part #` (orderable part, e.g. `PKG-3560-HPTRGM4-8X10GE` for a full appliance), `Spare Serial` (assigned spare unit). This takes precedence over the inventory sheet's partner and sub-level when present.
- **From the inventory sheet:** `Hardware spare provided by` holds partner + depot, often truncated: `Main Charlotte` = Maintech Charlotte, `Flash Manchest` = Flash Manchester, `Flash Greensbor` = Flash Greensboro. `Main…` always means **Maintech**.

Examples (synthetic):
- `RMA: Replace CHS-3560AC-05-A S009000001 for ACME in Singapore`
- `RMA: Replace CHS-3560AC-05-A S009000001 for ACME via Flash Singapore Order# 1234`
- `RMA: Replace ADB-000004-01-A S009000001 for ACME in Germany`
- `RMA: Replace ADB-000004-01-A S009000001 for ACME via Maintech Frankfurt Order# 5678`

## 3. Description template

Use markdown (`contentFormat: markdown`). `[..]` = conditional. Each CLI section goes in **its own fenced code block**, copied verbatim **including the GD's `#####` header** (`# CLI command:` / `# Host:` lines). The issue summary comes **after** the CLI output.

````
[Platinum+ only] Faulty appliance S/N <chassis S/N> will be replaced with appliance S/N <replacement S/N> from <Partner> <Location>

Company: <Customer>

```
Shipping address
<company name as on the address>
<street, city, postcode[, country]>
<contact name> Contact Number <phone>
```

[Data center address: same code-block layout, only if different]

[Booking ref / change request: <ref>]
SolOS: <version from show version>
Support level: <see rule below>
[Platinum+] Field service work order: <Partner> #<WO>
[High priority] Priority justification: <reason>
Salesforce case: <case number>
Related Support Jira: SOL-xxxxx

**Diagnostics:**

```
#################################################################
# CLI command: show hardware detail
# Host:        <hostname>
#################################################################
<verbatim output>
```

```
<show product-key, verbatim with header>
```

[Full appliance: show version, show ip vrf management, show console, each in its own block]
[Evidence sections the user confirmed, each in its own block]

**Issue summary:**
<symptoms> → <troubleshooting done> → <suspected cause> → <decision / escalation> → [replacement S/N assigned by Ops] → RCA requested: yes/no
````

Per-condition rules:

| Condition | Rule |
|---|---|
| Opening "Faulty appliance ... replaced with ..." line | Platinum+ only. The replacement S/N comes from Ops (a person tells the engineer): ask the user. If unknown, write `replacement S/N: TBD (Ops to assign)`. For a part (not full appliance) replacement, adapt: `Faulty <part#> S/N <serial> in appliance S/N <chassis> will be replaced from <Partner> <Location>`. |
| Shipping address | Always its own fenced code block, four lines: the literal `Shipping address`, the company name as written on the address (e.g. `Barclays PLC`, not the Summary short name), the street / city / postcode on one line, then `<contact name> Contact Number <phone>` (e.g. `DCOPS Contact Number 02031344800`). From the inventory `address`: the first comma-separated part is the company, the rest up to `Attn:` is the street line, and the `Attn:` part gives the contact name and phone. With several phone numbers, keep them on the one line separated by ` / `. |
| Data center address | Only when it differs from the shipping address. Same code-block layout, headed `Data center address`, without the contact line. |
| Support level line | `Platinum` / `Platinum+ (Yes)` / `Platinum+ (Hybrid)` / `Platinum+ (In-Country Local Spares)` / `Platinum+ exception: shipped from HQ (part not stocked at partner depot)` / `Platinum+ exception: shipped from HQ (partner out of stock; please backfill <Partner> <Location> depot)` |
| Work order line | Platinum+ only (including Nuances where a WO exists). Uses the same partner order # as the Summary (`Flash #2491`). |
| Priority justification | Only when Priority = High. |
| show hardware detail | Always. If no GD is available, list the chassis serial (and what the user pasted) and say "GD not available". |
| show product-key | Always. |
| show version, show ip vrf management, show console | Always for a full appliance (Ops pre-configures the replacement). For parts, `SolOS:` line is enough; include the sections if the user wants. |
| Missing section | Write `<command>: not available in GD` instead of inventing output. |
| Issue summary | Narrative, factual, from the evidence findings + the user's input. Mention the exact evidence (e.g. "IPMI SEL: Power Unit: Failure detected at <time>"). |

Reference style (redacted, from a real RMA): address block, then `show hardware detail` and `show system detail` pasted verbatim with their headers, then a narrative: "Power Unit: Failure detected" IPMI SEL lines, suspected power housing fault, escalated to a full appliance replacement, Ops assigned replacement appliance S/N, RCA requested after the faulty unit returns.

Philosophy: "more is never less" as long as it is helpful. Include everything the runbook requires plus supporting evidence.

## 4. Field values

| Field | Value |
|---|---|
| Site | `sol-jira.atlassian.net` |
| cloudId | `f76135b4-3004-44fe-a1bc-bd189b6e79f3` |
| Project | `OPS` ("Operations", id 13930) |
| Issue type | `RMA` (id 10512) |
| Required at create | project, issuetype, summary (everything else is optional) |
| Priority | `Medium` (default; ships next day). `High` only on customer request or production impact; the reason must be in the Description. |
| Labels | Platinum+ only: exactly one of `Flash` or `Maintech` (the partner). Fujitsu: no label defined; ask the user. No label for Platinum. For Nuances, label the partner only if a partner is involved (e.g. backfill case). |
| Issue link | Type **Relates**, between the new RMA and the originating Support Jira (`SOL-xxxxx`). |
| Watchers | **Manual only.** The Atlassian MCP has no add-watcher tool and watchers cannot be set at create. Resolve each name with lookupJiraAccountId; after creation list the resolved names + the ticket URL so the user adds them in the browser. No cc comments, no REST calls. |
| Ignore | Approval custom fields, components. |
| Ticket URL | `https://sol-jira.atlassian.net/browse/<KEY>` |

Duplicate-check JQL: `project = OPS AND issuetype = RMA AND text ~ "<chassis serial>" ORDER BY created DESC` (fields: summary, status, created; maxResults ~10).

After creation, Salesforce: add the OPS key to the case's Jira tab and set **Hardware Fault = Yes**.

## 5. Part-number lookup guide

Where each part number lives (from `extract_gd.py inventory` / `show hardware detail`):

| Part | Where to find the Part# | Serial |
|---|---|---|
| Full appliance | `Chassis Product #:` (e.g. `CHS-3560AC-05-A`) | `Chassis serial:` |
| ADB (Assured Delivery Blade) | `Slot x/y: Assured Delivery Blade` → `Product #:` (e.g. `ADB-000004-01-A`) | `Serial #:` in the slot block |
| NAB (Network Acceleration Blade) | `Slot x/y: Network Acceleration Blade` → `Product #:` | `Serial #:` in the slot block |
| HBA (Host Bus Adapter) | The HBA slot block → `Product #:` | `Serial #:`; also record the WWNs (customer must be told the WWNs before an HBA swap) |
| SFP | `SFP (Port N) Details:` → `Part Number :` inside the blade block. Note: the vendor part number (e.g. `AFBR-709SMZ`) may differ from the Solace orderable part (e.g. `SFPP-PC02`): confirm with the user. | `Serial #` in the SFP block |
| Disk / SSD | `Disk N:` → `Device Model:` (vendor model). Confirm the Solace orderable part with the user. | `Serial #:` |
| PSU (power module) | Not shown in show hardware detail. **3560 AC PSU: `CHS-PWRAC0-02-A`** (from a real Maintech RMA, OPS-4741). For other platforms, ask the user and add the number here. | Not in GD; use the module number (`Power module N`) |
| Fan | Not in show hardware detail: ask the user. | Ask |

## 6. Glossary

| Term | Meaning |
|---|---|
| ADB | Assured Delivery Blade (guaranteed-messaging card with flash + capacitor backup) |
| NAB | Network Acceleration Blade (network I/O card) |
| HBA | Host Bus Adapter (Fibre Channel card for external storage) |
| GD | gather-diagnostics bundle collected from the broker |
| RMA | Return Merchandise Authorization (the OPS ticket that ships a replacement) |
| RCA | Root Cause Analysis (on the returned faulty unit) |
| RHR | Rapid Hardware Repair = Platinum+ |
| Platinum | Standard Support: direct NBD shipment from HQ, customer self-install |
| Platinum+ | RHR via field service partners from local depots |
| FE / FSP | Field Engineer / Field Service Partner (Flash Global, Maintech, Fujitsu) |
| WO | Work Order: the partner's order number for the dispatch |
| HQ | Solace headquarters, Ottawa (ships direct RMAs) |
| NBD | Next Business Day |
