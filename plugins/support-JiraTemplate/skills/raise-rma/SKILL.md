---
name: raise-rma
description: "Raises Operations (OPS) RMA Jira tickets for Solace hardware appliance replacements (PSU, ADB, NAB, SFP, HBA, disk, fan, or full appliance) following the Hardware Replacement Workflow. Asks for the chassis serial first and looks up the support tier, customer and shipping address in the Shipment Inventory Record (via support-rma), decrypts the gather-diagnostics via /support-jira:draft-jira, parses it for part numbers, SolOS version, product keys and failure evidence, applies the Platinum / Platinum+ / exception Summary conventions, drafts the Description for user review, checks for duplicate RMAs, then creates the ticket via the Atlassian MCP (or produces paste-ready text). Use when a support case needs a hardware replacement, an RMA, a part shipment from HQ, or a Flash/Maintech partner backfill ticket."
argument-hint: "[serial | extracted-GD-folder]"
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
[ ] 1 Serial collected + entitlement looked up
[ ] 2 GD located/decrypted + faulty unit confirmed
[ ] 3 Part(s) chosen (+ tier, only if the lookup didn't settle it)
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

## Step 1: Serial number and entitlement

### 1a. Serial number (ask first)

The chassis serial of the faulty appliance drives everything else, so get it before anything else. If the argument looks like a serial (7+ characters containing digits, not an existing path, e.g. `S009004123`) or the user already gave one in the conversation, use it. Otherwise ask in plain text:

> What's the chassis serial number of the faulty appliance?

Don't guess or invent a serial. If the user doesn't know it, ask for the extracted GD folder or the archive instead and take the serial from the GD in Step 2 (then run 1b on it).

### 1b. Entitlement lookup (automatic)

Look up the serial in the Shipment Inventory Record with the support-rma plugin's serial lookup (the same one `/support-rma:inventory` uses). Don't ask the user first.

Find the newest installed copy of the script and run it in the foreground (it takes a few seconds):

```bash
INV=$(ls -d ~/.claude/plugins/cache/coop-support-hack/support-rma/*/scripts/inventory.sh 2>/dev/null | sort -V | tail -1)
"$INV" lookup "<serial>" --state-dir "$HOME/.claude/plugins/data/support-rma-coop-support-hack"
```

The output is JSON. Each `results[]` entry has `customer`, `support_tier` (`Platinum+` when Premium Onsite Support is Yes, otherwise `Platinum`), `mtce_eligible`, `mtce_can_provide`, `mtce_contract_active`, `chassis`, `dest_city`, `address` (usually ends with `Attn: <name>, <phone>`), `hw_spare_provided_by`, `sheet` and `row`. Keep these values in the conversation only (rule 1). Don't save the JSON.

How to use the result:

| Result | What to do |
|---|---|
| One row, or several rows that agree on customer, tier, chassis and address | Use it. Say "Entitlement from the inventory sheet: <tier> (<customer>, row <row>)". Don't ask about the tier. |
| Several rows that differ | List them (row, customer, tier, chassis, dest city) and ask which applies. |
| `mtce_eligible: false` | Warn: "Maintenance not active per the sheet (can provide: <x>, contract active: <y>)". This is a warning only. Ask whether to continue; the engineer decides. |
| Serial in `not_found`, `$INV` empty (support-rma not installed), or a non-zero exit | Say why in one line (show stderr on a non-zero exit; exit 3 usually means Excel isn't signed in, see `/support-rma:inventory`). Then fall back to asking the tier in Step 3 and the case details in Step 5. |

Derive these values from the result and treat them as pre-filled defaults for later steps:
- **Tier:** `support_tier`.
- **Customer short name:** `customer`.
- **Country:** the country at the end of `address` (e.g. `UK`). If it isn't clear, leave it blank for Step 5.
- **Shipping address:** `address` without the trailing `Attn:` part.
- **Contact name + phone:** the `Attn:` part of `address`, if present.
- **Platinum+ partner:** `hw_spare_provided_by`, if it names Flash, Maintech or Fujitsu.
- **Platinum+ partner location:** `dest_city`, as a suggestion only. Confirm it in Step 5.
- **Expected chassis product #:** `chassis`, checked against the GD in Step 2c.

Show the entitlement as one short block (customer, tier, MTCE eligibility, chassis, dest city, hardware spare provided by), then go straight to Step 2.

## Step 2: Get the GD and confirm the faulty unit

### 2a. Locate or decrypt the GD

In this order:

1. **An argument path was given:** use it.
2. **Already extracted:** directories named `gather-diagnostics*` in the cwd or `~/Downloads` that contain `cli-diagnostics.txt` or `gdh-diagnostics.txt`, possibly nested as `<f>/<f>/` (`find . ~/Downloads -maxdepth 3 \( -name cli-diagnostics.txt -o -name gdh-diagnostics.txt \)`). Run `inventory` (2b) on each and keep the one whose `chassis_serial` equals the Step 1 serial. Exactly one match → use it without asking. None → treat as not found. Ignore the others (an HA mate's GD looks almost identical).
3. **Otherwise, decrypt with `/support-jira:draft-jira`** (the normal case). Invoke it with the Skill tool, passing the serial as `args` so it doesn't ask again. It opens a file picker for the `.tgz.p7m`, decrypts it (on Windows it prints a Microsoft sign-in URL + code; relay them), extracts it into `<archive>-extracted`, and reports the folder. Its final "drafting isn't wired up" note doesn't apply here: take the extracted folder it reports and continue with 2b. Ignore its support-plan line; Step 1b already settled the tier. If the skill isn't available, tell the user to install it (`/plugin install support-jira@coop-support-hack`) and fall back to option 4 or 5.
4. **The archive isn't on this machine yet: fetch from filedrop.** Ask for the Salesforce case number and the customer name as it appears in filedrop (lowercase, prefix-matched, e.g. "rbc"). This uses support-gd-handler's scripts at these exact fixed paths. Don't search for them and don't read them:
   - `FD=~/.claude/plugins/marketplaces/support-marketplace/plugins/support-gd-handler/scripts/filedrop.py`
   - `HG=~/.claude/plugins/marketplaces/support-marketplace/plugins/support-gd-handler/scripts/handle_gds.py`

   If `filedrop.py` doesn't exist, tell the user to install support-gd-handler (`/plugin install support-gd-handler@support-marketplace`) and fall back to option 5.

   Steps:
   1. Before running anything, tell the user: *"Fetching from filedrop. If Microsoft sign-in is needed, a URL and code will appear below. Complete the sign-in and it continues automatically."*
   2. Search for the case folder: `python3 "$FD" find <customer> "<case number>"`. Use a Bash timeout of 600000 ms. Each match prints as `<full_path>\t<type>\t<size>\t<date>`.
   3. Handle the results:
      - **No filedrop account matched:** the customer name is wrong. Names are lowercase and prefix-matched. Ask again; don't retry blindly.
      - **No matches:** say so, and ask the user to check the case number or give a filename fragment (e.g. the hostname) to search for instead.
      - **Matches found:** keep the GD-looking files (`gather-diagnostics*` ending in `.tgz`, `.tgz.p7m`, `.zip.p7m` or `.zip.p7m.zip`). If there are several (HA pairs usually have one per node, and there may be older uploads), list them with size and date and ask which to get. The default is all of the newest set.
   4. Download, decrypt and extract: `python3 "$FD" get <customer> <full_path> [<full_path> ...]`. Run it in the background with a 600000 ms timeout. Watch its output for the Microsoft sign-in URL and code and show them to the user straight away. It downloads into the cwd and decrypts and extracts automatically.
   5. Run `python3 "$HG"` with no arguments. It auto-discovers the extracted folders and prints a `=== BROKER CONTEXT ===` block. Read the saved output file rather than the inline stdout, which can be thousands of lines. Show only that block.
   6. Then go back to option 2 to find the extracted folders.

   Don't run `rm` or `mkdir` around this; support-gd-handler cleans up after itself. If it errors (extraction failed, unexpected wrapper, `PermissionError`), show the error verbatim and point the user to `/support-gd-handler:fetch-gds`, which knows the workarounds. Don't improvise fixes.
5. **Manual fallback** (no GD available): ask for whatever CLI output the user can paste (`show hardware detail`, `show product-key`, ...) and use only what the user pasted.

### 2b. GD inventory

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract_gd.py" inventory "<gd>"
```

Exit 2 means no diagnostics file was found at that path (go back to 2a).

### 2c. Confirm

Check automatically:
- GD `chassis_serial` = the Step 1 serial. If not, **stop and ask**: it's likely the HA mate's GD, and an RMA against the wrong serial ships the wrong hardware. Never mix nodes.
- GD `chassis_product` = the sheet's `chassis` (from 1b). If not, flag it: it may be the wrong GD or a sheet error. The user decides.

Then show one compact table with the GD inventory: hostname, platform, chassis product #, chassis serial, SolOS, power redundancy + each power module state, blades (slot, type, product #, serial, state), and `missing_required` if non-empty. If both checks pass, don't ask the user to confirm the unit separately; go to Step 3.

## Step 3: Part(s) and support tier

Read `${CLAUDE_PLUGIN_ROOT}/context/rma-jira-fields.md` now if you haven't.

Use one AskUserQuestion call with:
- **Part(s) to replace** (multiSelect). Pre-fill options from the inventory, e.g. `ADB ADB-000004-01-A slot 1/3`, `NAB NAB-0810EM-01-A slot 1/6`, `PSU (power module 2: Failed)`, `Full appliance CHS-3560AC-05-A`. Put components that look faulty first. "Other" covers SFP / HBA / disk / fan.
- **Support tier**: `Platinum` / `Platinum+` / `Nuances`. **Only include this question if Step 1b didn't settle the tier.**

Then a follow-up AskUserQuestion, depending on the tier:
- **Platinum** (from the sheet or the user): no follow-up.
- **Platinum+** → sub-level, with the Nuances as extra options, because the sheet can't tell whether this shipment is an exception: `Yes (part + FE within 4h)` / `Hybrid (part, no FE)` / `In-Country Local Spares` / `Nuance: ship from HQ`. If the user picks the Nuance option, ask `(i) part not stocked at partner depot` / `(ii) partner out of stock (Spare Qty = 0)`.
- **Nuances** (chosen by the user when there was no lookup) → `(i) part not stocked at partner depot` / `(ii) partner out of stock (Spare Qty = 0)`.

Nuances use the direct Summary format.

For "Other" parts, find the part number per the part-number lookup guide in rma-jira-fields.md. PSU, fan, and the Solace orderable part for SFPs/disks may not be in the GD: ask in Step 5.

## Step 4: Evidence scan (delegated)

The GD files can be several MB, so delegate the scan to a **general-purpose subagent** to keep this context clean. Subagents do not expand `${CLAUDE_PLUGIN_ROOT}`, so **pass absolute paths**. Use the expanded paths in this file's reference list. If they still show the literal `${CLAUDE_PLUGIN_ROOT}`, resolve it (`echo "$CLAUDE_PLUGIN_ROOT"`). If that is empty too, locate the plugin with `find ~/.claude/plugins -path '*/scripts/extract_gd.py'`.

Prompt the subagent with:
- the absolute GD folder path,
- the absolute path to `extract_gd.py`,
- the absolute path to `evidence-guide.md` (tell it to read it first),
- the selected part type(s) and the inventory facts that matter (e.g. "Power module 2: Failed"),
- instructions: run `extract_gd.py sections <gd>`, read the relevant sections with `extract_gd.py section`, discover log files under the GD folder with `find` (don't assume a layout), grep them per the guide, and **return at most 10 concise findings** with exact section names / quoted log lines + a list of suggested sections to include + sections it looked for but didn't find. **It must not write any files** and must not invent output.

When it returns, show the findings, then ask (AskUserQuestion, multiSelect) which suggested sections to include in the Description. `show hardware detail` and `show product-key` are always included and don't need to be offered; for a full appliance, `show version`, `show ip vrf management` and `show console` are also always included.

## Step 5: Collect case details (one message)

Ask for everything still missing in **one** message, as a checklist, so the user can answer in one go.

Fill in the values from the Step 1b lookup and mark them `(from inventory sheet)`. The user only needs to correct them, not retype them. Leave out the Platinum+ lines for a Platinum customer. Don't re-ask anything the user has already given in the conversation.

```
- Salesforce case number (skip if already given):
- Customer short name for the Summary (e.g. "ACME"; may differ from the filedrop name):
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
Salesforce case <case number>: add <KEY> to the Jira tab and set Hardware Fault = Yes.
```

## Error handling

| Situation | What to do |
|---|---|
| MCP missing or auth fails | DRAFT MODE: paste-ready output. |
| GD chassis serial ≠ Step 1 serial | Stop and ask. Never mix nodes. |
| Inventory lookup unavailable / serial not found | Say why in one line; ask the tier (Step 3) and case details (Step 5) as usual. |
| Sheet chassis ≠ GD chassis product # | Flag it at the Step 2c confirmation; the user decides. |
| MTCE not active per the sheet | Warn and ask whether to continue; don't block. |
| Several GDs | Use the one matching the Step 1 serial; if none or several match, list them by hostname + serial and ask. |
| Section missing (older SolOS, gdh format) | List what's missing; ask the user to paste it, or write "not available in GD". |
| Possible duplicate RMA | Show it and ask whether to continue. |
| Watcher not found / ambiguous | Show candidates and ask; never guess. |
| SOL link fails or SOL key doesn't exist | Keep the RMA (the SOL key is already in the Description) and report the failure. |
| createJiraIssue fails | Show the error + the full paste-ready draft so nothing is lost. |
| PSU (or other) part# unknown | Ask the user. |

## Roadmap (not in this version)

- **Phase 2 (rest):** the Shipment Inventory Record lookup is done (Step 1b). Still to do: read the Platinum Plus Maintenance sheet for the sub-level, partner depot and Spare Qty, so Platinum+ sub-levels and Nuance (ii) can be detected too.
- support-log-buddy integration for the evidence scan.
- A post-install update mode (add install / return details to an existing RMA).
- Salesforce lookup to pre-fill case details.
