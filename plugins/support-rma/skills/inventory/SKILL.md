---
name: inventory
description: List available (not crossed-off) serial numbers from the live Shipment Inventory Record spreadsheet. Use when the user asks what serials / units / appliances are in stock or available, which replacement units can ship, or to look up a serial number in the inventory record. Also the data source for RMA generation.
argument-hint: "[filter, e.g. a model, sheet/location, or serial]"
allowed-tools: Bash, Read
---

# Shipment inventory

The inventory lives in `Shipment Inventory Record.xlsx` on the Operations
SharePoint site. It is read from the user's **OneDrive-synced local copy**, which
OneDrive keeps current — so every run reflects the live sheet. Never try to fetch
the SharePoint URL; it needs a Solace login and returns 403.

A serial is **crossed off** (used / shipped) when its cell is formatted
strikethrough. Everything else is available.

## Run

```bash
"${CLAUDE_PLUGIN_ROOT}"/scripts/inventory.sh \
  --path "${user_config.inventory_path}" \
  --state-dir "${CLAUDE_PLUGIN_DATA}" \
  -AvailableOnly
```

- With no configured path the script searches the OneDrive / SharePoint sync
  folders and caches what it finds in `${CLAUDE_PLUGIN_DATA}`.
- Drop `-AvailableOnly` to include crossed-off rows (e.g. "was FAKE-0002 used?").
- `--serial-column "<header or column letter>"` if auto-detection picks the wrong column.
- `-Sheet "<name>"` to read one worksheet only.

Output is JSON: `sheets[]` (per-sheet counts and the detected serial column),
`warnings[]`, and `rows[]`, each with `sheet`, `row`, `serial`, `crossed_off`,
`serial_strike` (`none` / `partial` / `full`), `struck_fields`, `hidden_row`, and
`fields` (every column of that row keyed by header).

Exit codes: `0` ok, `3` file not found / unreadable, `4` no serial column found.

## Report

1. Open with the source file's `last_modified` time so the user knows how fresh it is.
2. List available serials grouped by sheet, with the most useful identifying
   columns from `fields` (model / part number / location, whatever the sheet has).
   If `$ARGUMENTS` is given, filter rows by it (case-insensitive match on any field).
3. Give the counts: available vs crossed off, per sheet.
4. Call out anything ambiguous instead of silently deciding:
   - `serial_strike: partial` — only some characters are struck; counted as
     available, but ask the user to check it.
   - Rows where other cells are struck (`struck_fields`) but the serial is not.
   - `hidden_row: true`, hidden sheets (`state` ≠ `visible`), and any `warnings`.

## When it fails

- **Exit 3 (not found):** the SharePoint library is not synced to this machine.
  Tell the user to open the Operations site's document library in a browser, click
  **Sync** (or **Add shortcut to My files**), wait for OneDrive to finish, and rerun.
  If they know the local path, they can set it as the plugin's `inventory_path` option.
- **Exit 4 (no serial column):** show the user the sheet's header row and rerun
  with `--serial-column`.
- If `last_modified` is old but the user says the sheet was just edited, OneDrive
  may be paused or behind — have them check the OneDrive tray icon.

Do not edit the spreadsheet. If a serial should be crossed off, tell the user
which one; the shared workbook is edited by the Operations team.
