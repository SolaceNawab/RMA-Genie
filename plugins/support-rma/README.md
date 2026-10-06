# support-rma

Reads the live **Shipment Inventory Record** spreadsheet and reports which serial
numbers are still available (not crossed off). It is the inventory half of a
future automated RMA workflow.

## How "live" works

The spreadsheet lives on the Operations SharePoint site, which needs a Solace
login, so the plugin does not fetch the URL. Instead OneDrive syncs the document
library to your machine and keeps the local file current, and the plugin reads
that file each time it runs.

One-time setup:
1. Open the Operations site's document library (the folder containing
   `Shipment Inventory Record.xlsx`) in a browser.
2. Click **Sync** (or **Add shortcut to My files**).
3. Wait for OneDrive to finish. The file appears under
   `C:\Users\<you>\Solace Corporation\...` or inside `OneDrive - Solace Corporation`.

The plugin finds the file by itself. To pin a specific path, set the
`inventory_path` option when enabling the plugin.

## Usage

| | |
|---|---|
| `/support-rma:inventory` | list available serial numbers |
| `/support-rma:inventory 3560` | filter by model, location, serial, etc. |
| "is FAKE-0002 still in stock?" | the skill also triggers on plain questions |

## Core script

`scripts/read_inventory.ps1` is the shared core that later skills (e.g. `/rma`)
call. It parses the `.xlsx` XML directly, so Excel is not needed and the file is
never locked. It works while OneDrive is syncing or someone has the file open.

```bash
scripts/inventory.sh [--path FILE] [--state-dir DIR] [--serial-column HDR|LETTER] \
                     [-Sheet NAME] [-AvailableOnly] [-Format json|table]
```

Strikethrough detection:
- cell font strikethrough (a struck serial cell, or a whole struck row) → crossed off
- rich text with only part of the serial struck → `serial_strike: partial`, counted
  as available and flagged
- strikethrough applied via conditional formatting is **not** detected; a warning
  is emitted when a sheet uses conditional formatting

## Tests

`tests/fixture_inventory.xlsx` is fake data covering each strike case. Rebuild it
with Excel installed:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File tests/make_fixture.ps1
scripts/inventory.sh --path tests/fixture_inventory.xlsx -Format table
```

Expected: Ottawa 4 available / 2 crossed off (FAKE-0004 partial), Toronto 1 / 1.
