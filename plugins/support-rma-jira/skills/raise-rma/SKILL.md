---
name: raise-rma
description: "Raises Operations (OPS) RMA Jira tickets for Solace hardware appliance replacements (PSU, ADB, NAB, SFP, HBA, disk, fan, or full appliance) following the Hardware Replacement Workflow. Parses an extracted gather-diagnostics bundle for the chassis serial, part numbers, SolOS version, product keys and failure evidence, applies the Platinum / Platinum+ / exception Summary conventions, drafts the Description for user review, checks for duplicate RMAs, then creates the ticket via the Atlassian MCP (or produces paste-ready text). Use when a support case needs a hardware replacement, an RMA, a part shipment from HQ, or a Flash/Maintech partner backfill ticket."
argument-hint: "[extracted-GD-folder]"
---

# Raise an OPS RMA ticket

This skill implements **Step 3 of the Hardware Replacement Workflow**: raising the OPS RMA Jira once a Salesforce case and the gather-diagnostics (GD) have confirmed a hardware fault. The goal is a consistent, complete ticket that Ops can act on without a back-and-forth: correct `RMA:` Summary, every field the runbook requires, and the CLI evidence pasted verbatim.

Reference files (read them when the step says so):
- `${CLAUDE_PLUGIN_ROOT}/context/rma-jira-fields.md`: tiers, Summary decision table, Description template, field values, part-number lookup, glossary. **Read it before Step 3.**
- `${CLAUDE_PLUGIN_ROOT}/context/evidence-guide.md`: per-failure-type sections and log patterns (used by the evidence subagent).
- `${CLAUDE_PLUGIN_ROOT}/scripts/extract_gd.py`: read-only GD parser (`inventory`, `sections`, `section`). Run it; don't read it.

## Hard rules (and why)

1. **Customer PII never touches disk.** Names, addresses and phone numbers live only in this conversation and in the Jira ticket. No scratch files, progress files, temp drafts or `/tmp` copies. Track progress with an in-chat checklist; if the session is interrupted, re-run the GD parse rather than having saved state. This is a data-protection requirement, not a style preference.
2. **The user edits before anything is created.** Always show the full preview (Summary, fields, Description) and loop on add/remove/reword until the user explicitly approves. The engineer owns the ticket; the draft is a starting point.
3. **No guessing.** Never guess watchers (resolve them or ask), never mix two appliances' GDs (an HA mate looks almost identical), never fabricate CLI output (missing = "not available in GD").

## Progress checklist

Keep this checklist in chat and tick items as you go:

```
[ ] 0 Pre-flight (MCP / draft mode)
[ ] 1 GD located
[ ] 2 Faulty unit confirmed
[ ] 3 Part(s) + tier chosen
[ ] 4 Evidence scanned + sections chosen
[ ] 5 Case details collected
[ ] 6 Duplicate check
[ ] 7 Preview approved
[ ] 8 Ticket created + linked
```

---

## Step 0: Pre-flight

Check which Atlassian MCP tools are available. Teammates have either the direct server (`mcp__atlassian__*`) or the claude.ai connector (`mcp__claude_ai_Atlassian__*`). Don't hardcode a prefix: use whichever exposes these operations: `createJiraIssue`, `createIssueLink`, `searchJiraIssuesUsingJql`, `lookupJiraAccountId`, `getAccessibleAtlassianResources`.

- If found: optionally call `getAccessibleAtlassianResources` to confirm access to `sol-jira.atlassian.net` (cloudId `f76135b4-3004-44fe-a1bc-bd189b6e79f3`).
- If none is available, or auth fails: announce **DRAFT MODE**. Everything still works, but Step 6 is skipped and Step 8 outputs paste-ready text instead of creating the ticket.

## Step 1: Locate the GD

1. If an argument was given, use that path.
2. Otherwise auto-discover extracted folders in the cwd: directories named `gather-diagnostics*` that contain `cli-diagnostics.txt` or `gdh-diagnostics.txt`, possibly nested as `<f>/<f>/`. A quick `find . -maxdepth 3 \( -name cli-diagnostics.txt -o -name gdh-diagnostics.txt \)` works. Ignore archives (`.tgz`, `.p7m`): those are not extracted.
3. **Several found:** run `inventory` on each and list them by hostname + chassis serial; ask which one is the faulty unit.
4. **None found:** suggest decrypting and extracting the bundle first (`/support-jira:draft-jira` or `/support-gd-handler:fetch-gds`), or offer the manual fallback: ask for the chassis serial and whatever CLI output the user can paste (`show hardware detail`, `show product-key`, ...). In the manual fallback, use only what the user pasted.

## Step 2: Inventory and confirm the faulty unit

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract_gd.py" inventory "<gd>"
```

Exit 2 means no diagnostics file was found at that path (go back to Step 1).

Show a compact table: hostname, platform, chassis product #, chassis serial, SolOS, power redundancy + each power module state, blades (slot, type, product #, serial, state), and `missing_required` if non-empty.

Ask the user to **confirm this is the faulty unit** and that the chassis serial matches the case. If it doesn't match, **stop and ask**: it's likely the HA mate's GD, and an RMA against the wrong serial ships the wrong hardware.

## Step 3: Part(s) and support tier

Read `${CLAUDE_PLUGIN_ROOT}/context/rma-jira-fields.md` now if you haven't.

Use one AskUserQuestion call with two questions:
- **(a) Part(s) to replace** (multiSelect). Pre-fill options from the inventory, e.g. `ADB ADB-000004-01-A slot 1/3`, `NAB NAB-0810EM-01-A slot 1/6`, `PSU (power module 2: Failed)`, `Full appliance CHS-3560AC-05-A`. Put components that look faulty first. "Other" covers SFP / HBA / disk / fan.
- **(b) Support tier**: `Platinum` / `Platinum+` / `Nuances`.

Then a follow-up AskUserQuestion:
- Platinum+ → sub-level: `Yes (part + FE within 4h)` / `Hybrid (part, no FE)` / `In-Country Local Spares`.
- Nuances → `(i) part not stocked at partner depot` / `(ii) partner out of stock (Spare Qty = 0)`. Nuances use the direct Summary format.

For "Other" parts, find the part number per the part-number lookup guide in rma-jira-fields.md. PSU, fan, and the Solace orderable part for SFPs/disks may not be in the GD: ask in Step 5.

## Step 4: Evidence scan (delegated)

The GD files can be several MB, so delegate the scan to a **general-purpose subagent** to keep this context clean. Subagents do not expand `${CLAUDE_PLUGIN_ROOT}`, so **pass absolute paths**. Use the expanded paths in this file's reference list. If they still show the literal `${CLAUDE_PLUGIN_ROOT}`, resolve it (`echo "$CLAUDE_PLUGIN_ROOT"`). If that is empty too, locate the plugin with `find ~/.claude/plugins -path '*support-rma-jira*/scripts/extract_gd.py'`.

Prompt the subagent with:
- the absolute GD folder path,
- the absolute path to `extract_gd.py`,
- the absolute path to `evidence-guide.md` (tell it to read it first),
- the selected part type(s) and the inventory facts that matter (e.g. "Power module 2: Failed"),
- instructions: run `extract_gd.py sections <gd>`, read the relevant sections with `extract_gd.py section`, discover log files under the GD folder with `find` (don't assume a layout), grep them per the guide, and **return at most 10 concise findings** with exact section names / quoted log lines + a list of suggested sections to include + sections it looked for but didn't find. **It must not write any files** and must not invent output.

When it returns, show the findings, then ask (AskUserQuestion, multiSelect) which suggested sections to include in the Description. `show hardware detail` and `show product-key` are always included and don't need to be offered; for a full appliance, `show version`, `show ip vrf management` and `show console` are also always included.

## Step 5: Collect case details (one message)

Ask for everything still missing in **one** message, as a checklist, so the user can answer in one go:

```
- Customer short name (for the Summary, e.g. "ACME"):
- Country (Platinum / Nuances Summary):
- Shipping address:
- Data center address (only if different):
- Contact name + phone:
- Booking ref / change request (optional):
- Related Support Jira (SOL-xxxxx):
- Priority: Medium (default) or High (+ reason: customer request / production impact):
- Watchers (names):
[Platinum+] Partner (Flash / Maintech / Fujitsu), partner location, work order #, replacement S/N (from Ops; "TBD" is fine):
[Nuance (ii)] Partner + depot to backfill:
[PSU / fan / SFP / disk] Solace part number:
```

Anything still missing after the reply: ask once more, then write `not provided` in the draft and point it out in the preview. These answers contain PII: keep them in chat only (rule 1).

## Step 6: Duplicate check

Skip in DRAFT MODE. Otherwise call `searchJiraIssuesUsingJql` with:

```
project = OPS AND issuetype = RMA AND text ~ "<chassis serial>" ORDER BY created DESC
```

requesting only `summary`, `status`, `created`, maxResults ~10. If anything matches, show key / summary / status / created and ask whether to continue (it might be the same failure already being handled, or a legitimate repeat failure worth referencing in the Description).

## Step 7: Compose and preview

**Summary:** apply the decision table in rma-jira-fields.md §2 (always the `RMA: ` prefix; multiple parts → one RMA, part#s joined with ` and `, no serial; full appliance → chassis product #).

**Description:** follow the template and per-condition rules in rma-jira-fields.md §3. Get the CLI text with:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract_gd.py" section "<gd>" "show hardware detail" "show product-key" [...]
```

This prints the sections verbatim, header included. Put **each section in its own fenced code block**. Exit 3 means some sections are missing: the found ones are still printed; list the missing ones and either ask the user to paste them or write `<command>: not available in GD`. Never reconstruct output.

Draft the **Issue summary** after the CLI blocks, from the findings + the user's input: symptoms → troubleshooting done → suspected cause → decision/escalation → replacement S/N assigned by Ops (if any) → RCA requested yes/no. Ask about troubleshooting steps or RCA if you don't know them rather than inventing them.

Show the **full preview** in chat:

```
Summary:   RMA: ...
Priority:  Medium | High (reason)
Labels:    Flash | Maintech | (none)
Link:      Relates → SOL-xxxxx
Watchers:  <names> (added manually after creation)
Description:
<full markdown>
```

Ask what to add, remove or reword. Apply edits and re-show the changed parts (or the whole preview if the edits are large). Loop until the user **explicitly approves** (e.g. "create it", "looks good, go"). Silence or a question is not approval.

## Step 8: Create, link, report

**DRAFT MODE:** output the paste-ready Summary, the Description (in one block the user can copy), and the field list (project OPS, issue type RMA, priority, labels, link Relates → SOL key, watchers). Done.

**MCP mode:**
1. `createJiraIssue`: cloudId as above, `projectKey: OPS`, `issueTypeName: RMA`, `summary`, `description`, `contentFormat: markdown`, `additional_fields: {"priority": {"name": "<Medium|High>"}}` plus `"labels": ["Flash"]` or `["Maintech"]` for Platinum+.
2. `createIssueLink`: type `Relates`, between the new OPS key and the SOL key (direction doesn't matter for Relates).
3. Resolve each watcher with `lookupJiraAccountId`. One clear match → use it. Several → show the candidates and ask. None → say so and ask for a better name or email. Never pick one yourself.

Final output:

```
Created <KEY>: https://sol-jira.atlassian.net/browse/<KEY>
Summary: <final summary>
Linked: Relates → SOL-xxxxx
Add these watchers manually (Watch → Add watchers): <resolved display names>
Salesforce: add <KEY> to the case's Jira tab and set Hardware Fault = Yes.
```

## Error handling

| Situation | What to do |
|---|---|
| MCP missing or auth fails | DRAFT MODE: paste-ready output. |
| GD serial ≠ case serial | Stop and ask. Never mix nodes. |
| Several GDs | List them by hostname + serial and ask. |
| Section missing (older SolOS, gdh format) | List what's missing; ask the user to paste it, or write "not available in GD". |
| Possible duplicate RMA | Show it and ask whether to continue. |
| Watcher not found / ambiguous | Show candidates and ask; never guess. |
| SOL link fails or SOL key doesn't exist | Keep the RMA (the SOL key is already in the Description) and report the failure. |
| createJiraIssue fails | Show the error + the full paste-ready draft so nothing is lost. |
| PSU (or other) part# unknown | Ask the user. |

## Roadmap (not in this version)

- **Phase 2:** automatic entitlement lookup by chassis serial from the Shipment Inventory Record / Platinum Plus Maintenance Excel sheets (tier, sub-level, customer short name, country, partner, depot).
- support-log-buddy integration for the evidence scan.
- A post-install update mode (add install / return details to an existing RMA).
- Salesforce lookup to pre-fill case details.
