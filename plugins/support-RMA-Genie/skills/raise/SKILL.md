---
name: raise
description: "Raises Operations (OPS) RMA Jira tickets for Solace hardware appliance replacements (PSU, ADB, NAB, SFP, HBA, disk, fan, or full appliance) following the Hardware Replacement Workflow. Asks for the chassis serial first and looks up the support tier, customer and shipping address in the Shipment Inventory Record (built-in lookup), finds or decrypts the gather-diagnostics, runs a fast scripted evidence scan, applies the Platinum / Platinum+ / exception Summary conventions, drafts the Description for user review, checks for duplicate RMAs, then creates the ticket via the Atlassian MCP (or produces paste-ready text). Use when a support case needs a hardware replacement, an RMA, a part shipment from HQ, or a Flash/Maintech partner backfill ticket."
argument-hint: "[serial | extracted-GD-folder]"
---

# Raise an OPS RMA ticket

This skill implements **Step 3 of the Hardware Replacement Workflow**: raising the OPS RMA Jira once a Salesforce case and the gather-diagnostics (GD) have confirmed a hardware fault. The goal is a consistent, complete ticket that Ops can act on without a back-and-forth: correct `RMA:` Summary, every field the runbook requires, and the CLI evidence pasted verbatim.

**Target: about 5 minutes end to end.** The user should answer at most four prompts: the serial, one combined question (GD / part / row / MTCE), the case-details reply, and the approval. Every step below is built to batch work into one tool call and one question. Don't add extra confirmation rounds.

Reference files:
- `${CLAUDE_PLUGIN_ROOT}/context/rma-jira-fields.md`: tiers, Summary decision table, Description template, field values, glossary. **Read it in parallel with the Step 1 command** (not later), so it never costs a round trip of its own.
- `${CLAUDE_PLUGIN_ROOT}/scripts/extract_gd.py`: read-only GD helper. Run it; don't read it. Subcommands: `discover`, `inventory`, `scan`, `sections`, `section`.
- `${CLAUDE_PLUGIN_ROOT}/context/evidence-guide.md`: background for the patterns `scan` uses. Only read it if the user asks for a deeper manual dig.

## Hard rules (and why)

1. **Customer PII never touches disk.** Names, addresses and phone numbers live only in this conversation and in the Jira ticket. No scratch files, progress files, temp drafts or `/tmp` copies. If the session is interrupted, re-run the commands rather than having saved state.
2. **The user edits before anything is created.** Always show the preview and loop on edits until the user explicitly approves. The engineer owns the ticket.
3. **No guessing.** Never guess watchers, never mix two appliances' GDs (an HA mate looks almost identical), never fabricate CLI output (missing = "not available in GD").
4. **Never block on a hidden window.** Don't open native folder pickers. A file picker is only a fallback the user picks explicitly ("Browse…"), and runs with a 120000 ms timeout. If it times out, kill it and ask in chat.

## Shell conventions (every command)

Resolve Python inline in each Bash call; on Windows `python3` is often the Microsoft Store stub (exit 49):

```bash
PY=$(for c in python3 python py; do "$c" -c "import sys" >/dev/null 2>&1 && { echo "$c"; break; }; done)
X="${CLAUDE_PLUGIN_ROOT}/scripts/extract_gd.py"
```

Use `"$PY" -I "$X" ...`. Quote every path. Pass Windows paths in `C:/...` form to Python.

## Progress checklist

Keep this in chat, one line per step, ticking as you go:

```
[ ] 1 Serial + entitlement + GD discovery + duplicate check
[ ] 2 GD ready + serial confirmed
[ ] 3 Part (+ row / MTCE / tier if needed)
[ ] 4 Evidence scan
[ ] 5 Case details + preview
[ ] 6 Approved → created (or paste-ready)
```

---

## Step 1: Serial, then one batched lookup

**Serial.** Take it from the argument (7+ characters containing digits and not an existing path, e.g. `S009004123`) or from the conversation. Otherwise ask in plain text: *What's the chassis serial number of the faulty appliance?* If the argument is an extracted GD folder, take the serial from its `inventory` output instead.

**Then, in one assistant turn, run these in parallel:**

1. **One Bash call** for the entitlement plus GD discovery:
   ```bash
   PY=$(for c in python3 python py; do "$c" -c "import sys" >/dev/null 2>&1 && { echo "$c"; break; }; done)
   X="${CLAUDE_PLUGIN_ROOT}/scripts/extract_gd.py"
   INV="${CLAUDE_PLUGIN_ROOT}/scripts/inventory.sh"
   echo "PY=$PY"
   SD="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/plugins/data/support-rma-genie-coop-support-hack}"
   INV_JSON=$(bash "$INV" lookup "<serial>" --path '${user_config.inventory_path}' --url '${user_config.inventory_url}' --state-dir "$SD"); rc=$?
   printf '%s\n' "$INV_JSON"; echo "inventory_exit=$rc"
   # Platinum Plus Maintenance sheet: ONLY for Platinum+ customers
   if printf '%s' "$INV_JSON" | grep -q '"support_tier": "Platinum+"'; then
     "$PY" -I "${CLAUDE_PLUGIN_ROOT}/scripts/inventory.py" ppm -Serial "<serial>" -Path '${user_config.ppm_path}' -StateDir "$SD"; echo "ppm_exit=$?"
   else echo "ppm: skipped (not Platinum+)"; fi
   "$PY" -I "$X" discover . ~/Downloads
   ```
2. **Read** `rma-jira-fields.md`.
3. **Atlassian MCP:** find the server that exposes `createJiraIssue` / `searchJiraIssuesUsingJql` / `lookupJiraAccountId` / `createIssueLink` (either `mcp__atlassian__*` or `mcp__claude_ai_Atlassian__*`). Call `searchJiraIssuesUsingJql` (cloudId `f76135b4-3004-44fe-a1bc-bd189b6e79f3`) with `project = OPS AND issuetype = RMA AND text ~ "<serial>" ORDER BY created DESC`, fields `summary,status,created`, maxResults 10. That call is both the access check and the duplicate check. No MCP or an auth error means **DRAFT MODE**: say so in one line. Also use DRAFT MODE whenever the user says not to create the ticket ("just show me the output").

**Using the inventory result.** The output is JSON. Each `results[]` entry has `customer`, `support_tier` (`Platinum+` when Premium Onsite Support is Yes / Hybrid / In-Country Local Spares; the raw value is in `premium_onsite_support`), `mtce_eligible`, `mtce_can_provide`, `mtce_contract_active`, `chassis`, `dest_city`, `address` (usually ends with `Attn: <name>, <phone>`), `hw_spare_provided_by`, `sheet` and `row`. Keep it in the conversation only (rule 1). It's the only lookup for this serial, so don't re-run it later.
- Rows that agree on customer, tier, chassis and address count as **settled**.
- Rows that differ, or `mtce_eligible: false`, become questions in Step 3's combined call. Don't ask them separately.
- Not found or a non-zero exit: say why in one line (exit 3: no synced copy / Excel not signed in on Windows / macOS Full Disk Access; see `/support-rma-genie:inventory`). The tier then becomes a Step 3 question and the address a Step 5 question.

**Platinum Plus Maintenance (Platinum+ only).** Run it **only** when the customer is Platinum+: from the inventory sheet in Step 1, or, if the lookup failed, when the user picks Platinum+ in Step 3 (then run the same `ppm` command once, before Step 4). Never read it for Platinum customers, and don't mention it to them. The output is JSON: `results[]` has one row per spare-able part for the serial, with `solace_part`, `partner_part`, `support_by`, `response_time`, `sub_level` (`4H` → Yes, `4H PART ONLY` / `HYBRID` → Hybrid, `BEST EFFORT…` / `SHIPS NBD…` → In-Country Local Spares), `blades_excluded` (response time says "NO BLADES"), `status` (`COVERED` / `NO SPARE`), `bin`, `partner` + `depot` (from the bin, e.g. `FLASH HEATHROW` → Flash / Heathrow), `spare_qty`, `in_stock`, `spare_serial` (assigned spare units, `;`-separated) and `covered_qty`. Keep it in the conversation only (rule 1). If it is used, it **overrides** the inventory sheet for these defaults:
- **Partner and location** = `partner` + `depot` of the rows. Truncated depots (`Manchest`, `Greensbor`, `Mississau`) get the full city name and are marked to confirm in Step 5.
- **Sub-level** = `sub_level`. If it differs from the inventory sheet's `premium_onsite_support`, show both and recommend the Platinum Plus value in Step 3.
- **The chosen part's row** (Step 3) gives the **Solace orderable part #** (`solace_part`) and stock. Match the part from the GD by family: ADB → `ADB-`, NAB → `NAB-`, HBA → `HBA-`, PSU → `CHS-PWRAC`, fan → `CHS-FAN`, disk → `CHS-SSD`, full appliance → `PKG-`. If the GD's product # differs from `solace_part` (e.g. the blade was upgraded), flag it in the preview.
- **No stock** (`in_stock: false`, i.e. Spare Qty 0 or `NO SPARE`) → recommend **Nuance (ii)** (partner out of stock; ship from HQ and backfill `<partner> <depot>`). **A blade on a "NO BLADES" contract** (`blades_excluded`) → recommend **Nuance (i)** (not stocked at the depot).
- **Replacement S/N candidate** = `spare_serial` of the chosen part's row (for a full appliance, the spare appliance). Show it as "suggested from Platinum Plus Maintenance, confirm with Ops". Never treat it as confirmed.
- Serial not in the sheet, file not found, or a non-zero exit: one line on why, then continue with the inventory-sheet defaults.

Derived defaults: **tier** = `support_tier`. **Customer** = `customer`. **Country** = the end of `address`. **Shipping address** = `address` without the `Attn:` part, with the first comma-separated part as the company and the rest as the street line. **Contact** = the `Attn:` part. **Platinum+ sub-level** = `premium_onsite_support` (Yes / Hybrid / In-Country Local Spares), pre-selected in Step 3. **Platinum+ partner and location** = from `hw_spare_provided_by`, which the sheet often truncates. The first word gives the partner: `Flash…` → Flash, `Main…` / `Maintech…` → **Maintech**, `Fuji…` → Fujitsu. The rest is the depot location (e.g. `Main Charlotte` → Maintech Charlotte). If the location looks cut off (e.g. `Flash Manchest`), use the full city name (Manchester) and mark it to confirm in Step 5. If `hw_spare_provided_by` is empty, suggest `dest_city`. **Expected chassis** = `chassis`.

Show one short block with the entitlement and any duplicate RMAs found (key / summary / status / created). If there's a duplicate, add it as a question in Step 3; don't stop just for that.

## Step 2: Get the GD

Use the `discover` JSON. Don't run `find` or open a picker:

1. **An extracted GD whose `chassis_serial` = the serial:** use it. No question.
2. **None matches.** Ask about the GD source as **the first question of the Step 3 combined call**, so it's the same prompt. Options:
   - the newest unextracted archives from `discover` (up to 2), labelled by hostname from the file name + modified date,
   - `Fetch from filedrop`,
   - `Browse…` (file picker, 120 s timeout).

   "Other" lets the user paste a path. Also list the extracted GDs that *don't* match (hostname + serial) in the question text, so a wrong serial is obvious right away.
3. **Decrypt and extract**, once an archive is chosen:
   - **Windows:** the binary is the newest `~/.claude/plugins/cache/support-marketplace/support-gd-handler/*/scripts/decrypt-cms.exe`. **Linux/macOS:** `command -v decrypt-cms`, else `/home/public/RND/loads/decrypt-cms/main/current/linux/amd64/decrypt-cms`. If none is found, say where you looked and fall back to filedrop or a pasted path.
   - Start it with `run_in_background` and a 600000 ms timeout: `"<decrypt-cms>" "<archive>.tgz.p7m"`. It writes `<archive>.tgz` next to the input. In the **same turn**, run a short foreground wait for the sign-in code: `for i in $(seq 1 20); do grep -qE "Enter the code|Successfully decrypted|rror" "<output file>" && break; sleep 1; done; grep -E "https://|Enter the code|Successfully|rror" "<output file>"`. Relay the URL and code at once, then end the turn. You're re-invoked when it exits.
   - **Auth or Vault error:** show it verbatim and stop. Never add `-skip-vault`, `-key` or similar flags.
   - Extract next to the archive. Don't ask for an output folder: `mkdir -p "<base>-extracted" && tar --force-local -xzf "<base>.tgz" -C "<base>-extracted"` (`--force-local` is needed on Windows). Exit 2 caused only by `Cannot create symlink` lines is fine if the link targets exist: mention it in one line and continue.
   - In the **same Bash call**, run `"$PY" -I "$X" inventory "<extracted dir>"`.
4. **Filedrop** (`Fetch from filedrop`): ask for the Salesforce case number and the filedrop customer name (lowercase prefix, e.g. "rbc"). Use support-gd-handler's fixed paths (`FD=~/.claude/plugins/marketplaces/support-marketplace/plugins/support-gd-handler/scripts/filedrop.py`): first `"$PY" "$FD" find <customer> "<case>"`, then `"$PY" "$FD" get <customer> <path>...` in the background, relaying the sign-in code. Then re-run `discover`. On any error, show it verbatim and point to `/support-gd-handler:fetch-gds`.
5. **No GD at all:** use only the CLI output the user pastes.

**Serial check (automatic).** If the GD `chassis_serial` ≠ the serial, **stop**: it's probably the HA mate, and an RMA against the wrong serial ships the wrong hardware. In the same turn, run the inventory lookup on the GD serial, then ask one question: `Use the GD's unit <gd serial> (<customer>, <tier>)` / `Pick another archive` / `Stop`. If the user switches serials, the new lookup replaces Step 1's result. Also re-run the duplicate JQL for the new serial.

If the GD `chassis_product` ≠ the sheet's `chassis`, flag it in the inventory table (the user decides in the preview).

Show one compact table: hostname, platform, chassis product # and serial, SolOS, power redundancy and each power module, blades (slot, type, product #, serial, state), and `missing_required`.

## Step 3: One combined question

Use **one** AskUserQuestion call with up to 4 questions. Include only the ones that apply:

1. **GD source**: only if Step 2 found no matching extracted GD.
2. **Part to replace** (single select). Build the options from the inventory, with likely-faulty components first: `Full appliance <chassis product #>`, `ADB <product #> slot <x/y>`, `NAB …`, `PSU (power module N: <state>)`. "Other" covers SFP / HBA / disk / fan. If the GD isn't decrypted yet, offer generic options (Full appliance / PSU / ADB / NAB).
3. **Sheet row**: only if the rows differ. One option per row (row, customer, dest city).
4. **Entitlement**: only if MTCE is inactive, the tier is unknown, or there's a duplicate RMA. Merge these into one question, e.g. `Continue (MTCE inactive, flag it in the ticket)` / `Stop` / `Draft only, don't create`. If the tier is unknown, offer `Platinum` / `Platinum+` / `Nuances` instead.

Follow up only for **Platinum+**. Ask the sub-level, with the Platinum Plus Maintenance `sub_level` (or else the sheet's `premium_onsite_support`) as the first, recommended option, plus any Nuance it recommends (no stock / no blades) (`Yes (part + FE within 4h)` / `Hybrid (part, no FE)` / `In-Country Local Spares` / `Nuance: ship from HQ`). For a Nuance, also ask `(i) part not stocked at partner depot` / `(ii) partner out of stock (Spare Qty = 0)`. Put both in one AskUserQuestion. Platinum needs no follow-up.

For "Other" parts, take the part number from rma-jira-fields.md §5. Ask for the fan and the Solace orderable SFP / disk part numbers in Step 5. For a PSU, use the §5 part number for the platform and only ask if the platform isn't listed.

## Step 4: Evidence scan (scripted, about 2 s)

Run it in the foreground, in the same Bash call as the CLI extraction:

```bash
"$PY" -I "$X" scan "<gd>" <psu|adb|nab|sfp|hba|disk|fan|full>
"$PY" -I "$X" section "<gd>" "show hardware detail" "show product-key" [full: "show version" "show ip vrf management" "show console"] <SUGGESTED SECTIONS from a previous scan, if known>
```

`scan` prints a `VERDICT` plus `CURRENT STATE` (FAULT / ok lines quoted from the CLI), `LOG HISTORY` (matching log lines grouped, with count, first and last, quoted exactly), `SUGGESTED SECTIONS` and `MISSING`. Summarise it in at most 6 bullets. Don't spawn a subagent. Only dig further by hand (grep under the GD, using evidence-guide.md) if the user asks, or if `scan` errors.

**Section choice (no extra question):** include `SUGGESTED SECTIONS` that show something relevant to the part, plus the always-included ones. Include the key `LOG HISTORY` lines as one verbatim code block of their own. The user can drop any of these in the preview.

If the verdict is **NO HARDWARE FAULT EVIDENCE** or **NO CURRENT FAULT**, say so plainly. The Issue summary will then need the user's reason for the replacement (Step 5).

## Step 5: Case details and preview, in one message

Send **one** message containing:

**(a) A compact preview.** It must be complete except for the CLI bodies:
```
Summary:   RMA: ...                       (rma-jira-fields.md §2)
Priority:  Medium | High (reason)
Labels:    Flash | Maintech | (none)
Link:      Relates → SOL-xxxxx
Watchers:  <names> (added manually after creation)
Header:    Company / Shipping address code block / SolOS / Support level / [WO] / Salesforce case / Related Support Jira
CLI:       show hardware detail (N lines) · show product-key (N) · … · <command>: not available in GD
Evidence:  <log excerpt lines, verbatim>
Issue summary: <draft narrative>
```
Print the header block and the Issue summary in full. **Don't print the CLI bodies** in the preview: they're copied verbatim from the GD by the script, and printing them doubles the generation time. Show them only if the user asks.

**(b) What's still missing**, as a checklist. Pre-fill values from the sheet, marked `(from inventory sheet)`, and skip anything already known. Leave out the Platinum+ lines for Platinum customers.
```
- Salesforce case number:
- Related Support Jira (SOL-xxxxx):
- Priority: Medium (default) or High (+ reason):
- Watchers (names):
- Symptoms / troubleshooting done / RCA wanted?  (needed when the scan found no current fault)
- Customer short name / country / shipping address / contact: <pre-filled> (from inventory sheet)
- Data center address (only if different) · Booking ref (optional)
[Platinum+] Partner, location, work order #, replacement S/N ("TBD" is fine)
[Nuance (ii)] Partner + depot to backfill
[fan / SFP / disk, or a PSU on an unlisted platform] Solace part number
```
End with: *"Reply with the missing values and any edits. Say **create** (or **draft**) when it's right."*

If the reply fills in values without approving, show only the changed parts and ask again. Anything still missing after one more ask becomes `not provided`, and you point that out. Silence or a question is not approval.

## Step 6: Create, link, report

Build the full Description from rma-jira-fields.md §3 **once**, at this step: the header, each CLI section in its own fenced code block copied verbatim from the `section` output (header included), the evidence block, then the Issue summary. Write `<command>: not available in GD` for missing sections.

**DRAFT MODE:** output the Summary, the full Description in one copyable block, and the field list (OPS / RMA / priority / labels / Relates → SOL / watchers). Done.

**MCP mode**, in one turn where possible:
1. `createJiraIssue`: cloudId as above, `projectKey: OPS`, `issueTypeName: RMA`, `summary`, `description`, `contentFormat: markdown`, `additional_fields: {"priority": {"name": "<Medium|High>"}}`, plus `"labels": ["Flash"]` or `["Maintech"]` for Platinum+.
2. Then, in parallel: `createIssueLink` (type `Relates`, new OPS key ↔ the SOL key) and `lookupJiraAccountId` for each watcher. One clear match: use it. Several: show them and ask. None: ask for a better name. Never pick one yourself.

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
| MCP missing, auth fails, or the user says don't create | DRAFT MODE: paste-ready output. |
| `PY` empty | Say Python 3 isn't installed (Windows: `winget install Python.Python.3.12`). Continue with the manual paste fallback. |
| GD chassis serial ≠ the serial | Stop. Look up the GD serial and ask (Step 2). Never mix nodes. |
| Picker or decrypt waiting on the user | Relay the sign-in code once. Never wait on a hidden window (rule 4). |
| Inventory lookup unavailable or serial not found | One line on why. The tier goes into Step 3's question, the address into Step 5. |
| Several sheet rows differ / MTCE inactive / duplicate RMA | One combined Step 3 question. Flag MTCE inactive in the Description header. |
| Sheet chassis ≠ GD chassis product # | Flag it in the Step 2 table. The user decides. |
| Section missing | `<command>: not available in GD`. List it in the preview. |
| `scan` fails | Show stderr, then fall back to a manual grep using evidence-guide.md. |
| Watcher not found or ambiguous | Show the candidates and ask. Never guess. |
| SOL link fails | Keep the RMA (the SOL key is in the Description) and report the failure. |
| createJiraIssue fails | Show the error and the full paste-ready draft. |

## Roadmap (not in this version)

- Detect "Spare at Cust" (customer-held spare, so only an FE is needed) once it's clear which Platinum Plus Maintenance column holds it.
- A post-install update mode (add install / return details to an existing RMA).
- A Salesforce lookup to pre-fill the case details.
