# support-rma

Reads the live **Shipment Inventory Record** spreadsheet. It does two things:

- **Serial lookup** (a few seconds): returns the RMA facts for a serial, and
  nothing else: customer, Platinum / Platinum+ tier, MTCE eligibility, chassis,
  destination city, address and hardware spare provided by.
- **Listing** (several minutes): reports which serial numbers are still
  available (not crossed off).

It is the inventory half of a future automated RMA workflow.

## How "live" works

The spreadsheet lives on the Operations SharePoint site, which needs a Solace
login. The scripts find a copy in this order:

1. `inventory_path`, if you set it when enabling the plugin.
2. **A OneDrive-synced copy** (recommended). OneDrive keeps it current, so it is
   read in place and every run is live.
3. **A SharePoint copy fetched through Excel.** Desktop Excel is already signed
   in to your Solace account, so the scripts open the file read-only in a hidden
   Excel, save a copy to the plugin's data folder, and close that Excel. This
   takes about 30–40 s. The copy is reused for 10 minutes (`-MaxAgeMinutes`;
   `-Refresh` forces a new fetch).

To set up the synced copy (one time):
1. Open the Operations site's **Shared Documents** in a browser.
2. On the **Ship Spreadsheet** folder (the folder itself, not the file inside it),
   choose **Add shortcut to My files**. You can also click **Sync** on the library
   toolbar if it's offered. Using "Add shortcut" on the *file* only creates a `.url`
   link, and that doesn't work.
3. Wait for OneDrive to finish. The file appears under
   `C:\Users\<you>\Solace Corporation\Operations - Ship Spreadsheet\`.

The scripts find the file by themselves. They skip copies named "Copy of …" or
"… - Copy".

## Usage

| | |
|---|---|
| `/support-rma:inventory S009004123` | RMA facts for one serial |
| `/support-rma:inventory S009004123 S009004156` | several serials in one pass |
| `/support-rma:inventory` | list available serial numbers |
| `/support-rma:inventory 3560` | list, filtered by model, location, etc. |
| "is FAKE-0002 still in stock?" | the skill also triggers on plain questions |

## Scripts

```bash
scripts/inventory.sh lookup SERIAL... [--path FILE] [--url URL] [--state-dir DIR] [-Refresh] [-MaxAgeMinutes N]
scripts/inventory.sh [--path FILE] [--url URL] [--state-dir DIR] [--serial-column HDR|LETTER] \
                     [-Sheet NAME] [-AvailableOnly] [-Format json|table] [-Refresh]
```

- `scripts/lookup_serial.ps1`: the serial lookup. A small C# scanner, compiled
  once and cached as a DLL in the state dir, streams the sheet XML a single time.
  It compares only the serial column of each row and reads the rest of a row only
  when its serial matches. Sheets without the needed columns are dropped as soon
  as their header row is read. The scan takes under 1 s on the real workbook
  (7,700 rows × 86 columns).
- `scripts/read_inventory.ps1`: the full listing, and the shared core for later
  skills such as `/rma`. It parses the `.xlsx` XML directly, so Excel isn't needed
  to read it and the file is never locked.
- `scripts/inventory_source.ps1`: finds the file, and fetches it from SharePoint
  when needed. Both scripts use it.

Strikethrough detection, the same in both scripts:
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
