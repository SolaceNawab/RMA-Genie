---
name: inventory
description: Look up serial numbers in the live Shipment Inventory Record (customer, Platinum / Platinum+ tier, MTCE eligibility, chassis, destination city, address, hardware spare provided by), or list available (not crossed-off) serials. Use when the user gives a serial number to look up, asks what serials / units / appliances are in stock or available, or which replacement units can ship. Also the data source for RMA generation.
argument-hint: "[serial number(s) to look up, or a filter such as a model or sheet]"
allowed-tools: Bash, Read
---

# Shipment inventory

The inventory lives in `Shipment Inventory Record.xlsx` on the Operations
SharePoint site. The scripts read the OneDrive-synced local copy when there is
one (OneDrive keeps it live). Otherwise, **on Windows only**, they fetch a copy
from SharePoint through the user's signed-in Excel and reuse it for 10 minutes.
Never fetch the SharePoint URL any other way; it needs a Solace login and returns 403.

`inventory.sh` works on Windows, macOS and Linux. On Windows it runs the
PowerShell scripts; elsewhere it runs `inventory.py` (needs Python 3). The output
is the same on every platform. On macOS/Linux there is no Excel fetch, so the file
must be OneDrive-synced (OneDrive for Mac puts it under `~/Library/CloudStorage/OneDrive-*`)
or set through `inventory_path`.

Pick the mode from `$ARGUMENTS`:
- **Serial lookup:** the arguments are one or more serial numbers (tokens of 7+
  characters containing digits, e.g. `S009004123`, `3011-000038`).
- **Listing:** anything else (no arguments, a model such as `3560`, a sheet or
  location name), or the user asks what is available.

## Serial lookup (default when a serial is given)

```bash
"${CLAUDE_PLUGIN_ROOT}"/scripts/inventory.sh lookup $ARGUMENTS \
  --path '${user_config.inventory_path}' \
  --url '${user_config.inventory_url}' \
  --state-dir "${CLAUDE_PLUGIN_DATA}"
```

It takes a few seconds; there's no need to run it in the background. Add
`-Refresh` only if the user says the sheet was just edited and the source is a
SharePoint copy (`origin: sharepoint`).

The output is JSON: `origin` (`synced` = live OneDrive copy, `sharepoint` = copy
fetched at `as_of`, `path` = a file given explicitly), `as_of`, `results[]`,
`not_found[]` and `warnings[]`. Each result already has `support_tier` and
`mtce_eligible` computed:
- `support_tier`: `Platinum+` if Premium Onsite Support is Yes; otherwise `Platinum`, including when it's blank.
- `mtce_eligible`: true only when both "Can Support Team Provide MTCE on This Box?"
  and "Is MTCE Contract Active?" are Yes.

**Report only this**, one block per result, with nothing else added:

```
**<serial>**
- **Customer:** <customer>
- **Support tier:** <support_tier>
- **MTCE eligible:** Yes | No (can provide: <mtce_can_provide>, contract active: <mtce_contract_active>)
- **Chassis:** <chassis>
- **Dest City:** <dest_city>
- **Address:** <address>
- **Hardware spare provided by:** <hw_spare_provided_by>
```

Put one line above the blocks giving the source: "Live sheet (OneDrive sync, last
modified <as_of>)" or "SharePoint copy fetched <as_of>". Show a blank field as
`—`. Add a short note only for these exceptions:
- the serial is in `not_found`;
- the same serial matched several rows (show each, with its row number);
- `crossed_off: true`, `serial_strike: partial`, or `hidden_row: true`.

Don't mention the conditional-formatting warning, timings, sheet counts or other
columns unless the user asks.

## Listing

```bash
"${CLAUDE_PLUGIN_ROOT}"/scripts/inventory.sh \
  --path '${user_config.inventory_path}' \
  --url '${user_config.inventory_url}' \
  --state-dir "${CLAUDE_PLUGIN_DATA}" \
  -AvailableOnly
```

This reads every row of every sheet and takes several minutes on the real
workbook, so run it in the background and tell the user it is running.

- Drop `-AvailableOnly` to include crossed-off rows.
- `--serial-column "<header or column letter>"` if auto-detection picks the wrong column.
- `-Sheet "<name>"` reads one worksheet only, which is faster.

Output is JSON: `origin`, `last_modified`, `sheets[]` (per-sheet counts and the
detected serial column), `warnings[]`, and `rows[]`, each with `sheet`, `row`,
`serial`, `crossed_off`, `serial_strike` (`none` / `partial` / `full`),
`struck_fields`, `hidden_row`, and `fields` (every column, keyed by header).

Report:
1. Open with the source and its `last_modified` time.
2. List available serials grouped by sheet, with the most useful identifying
   columns from `fields`. If `$ARGUMENTS` is given, filter rows by it
   (case-insensitive match on any field).
3. Give the counts per sheet: available vs crossed off.
4. Call out anything ambiguous: `serial_strike: partial`, rows where other cells
   are struck but the serial is not, `hidden_row: true`, hidden sheets, and `warnings`.

## When it fails

Exit codes are the same for both modes: `0` ok, `3` file not found / unreadable,
`4` required columns not found.

- **Exit 3:** there is no synced copy, and (on Windows) the Excel fetch failed.
  The stderr message says why.
  - Windows: usually Excel isn't signed in to the Solace account, or the user has
    no access to the file. Ask them to open the file once in desktop Excel and sign
    in if prompted, then rerun.
  - macOS / Linux: there's no Excel fetch, so the file must be synced (below), or
    downloaded and set as the plugin's `inventory_path`.
  - macOS, message says `macOS blocked access to … (Operation not permitted)`: the
    app running Claude Code needs **Full Disk Access** (System Settings → Privacy &
    Security). After granting it, the user must quit and reopen that app. Point them
    to the repo README's "macOS setup".
  - On every platform, syncing the Operations "Ship Spreadsheet" folder (**Sync**,
    or **Add shortcut to My files** on the folder, not the file) avoids the fetch
    entirely. On a Mac, OneDrive must be signed in to the Solace account for the
    shortcut to appear under `~/Library/CloudStorage/OneDrive-*`.
- **Exit 4:** show the user the message (it lists the sheets and the missing
  headers). For listing, rerun with `--serial-column`.
- If a synced copy's `as_of` / `last_modified` is old but the user says the sheet
  was just edited, OneDrive may be paused or behind. Have them check the OneDrive
  icon (system tray on Windows, menu bar on a Mac).

Do not edit the spreadsheet. If a serial should be crossed off, tell the user
which one; the shared workbook is edited by the Operations team.
