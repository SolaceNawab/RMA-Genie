# Evidence guide: what to pull from a GD per failure type

> **DRAFT: validate against real GDs and refine.** Section names and log patterns below are starting points, not verified across all SolOS versions. When a real GD shows a better signal, update this file.

Background for `extract_gd.py scan`, which implements these patterns (`LOG_PATTERNS` / `PART_SECTIONS` in the script) and is what RMA Genie (`/support-rma-genie:raise`) runs. **When you refine a pattern here, update the script too.** The manual steps below are the fallback for a deeper dig. Goal: at most 10 concise, factual findings (quote exact section names and log lines with timestamps) plus a list of sections worth including in the RMA Description.

## How to scan

1. List the available sections: `python3 <extract_gd.py> sections <gd>`.
2. Print only the sections relevant to the part type: `python3 <extract_gd.py> section <gd> "<cmd>" ...` (exit 3 just means some were missing; note which).
3. Discover log files instead of assuming a layout. For example:
   `find <gd> -type f \( -name '*.log' -o -name '*.log.*' -o -name 'event.log*' -o -name 'system.log*' -o -name 'debug.log*' \) | head -50`
   Compressed rotations (`*.gz`) can be searched with `zgrep`.
4. Grep with `-i` and limit output (`| head -40`); the files can be several MB. Prefer the most recent occurrences and the first occurrence (onset).
5. Never write files. Never paraphrase a log line as if it were quoted: quote it exactly or describe it as a summary.

Always included regardless of type: `show hardware detail`, `show product-key`.

## PSU (power module)

- Sections: `show hardware detail` (power lines: `Power redundancy configuration`, `Operational power supplies`, `Power module N: Failed`), `show system detail` (`Last Restart Reason`, IPMI SEL lines).
- Log patterns: `Power Unit: Failure detected`, `Power off/down`, `Power module`, `power supply`, `PSU`, `IPMI SEL`.
- Finding example: "Power module 2: Failed; Operational power supplies: 1 of 1+1".

## ADB (Assured Delivery Blade)

- Sections: the ADB slot block in `show hardware detail` (`Operational State`, `Flash Card State`, `Power Module State`, `Errors`, `Fatal errors`, `Mate Link`), `show message-spool`, `show message-spool detail` if present, Linux-shell `show-adb-cap-status` output if present (capacitor state).
- Log patterns: `ADB`, `flash`, `capacitor`, `supercap`, `Assured Delivery Blade`, `fatal`, `spool.*(down|fail)`, `mate link`.

## NAB (Network Acceleration Blade)

- Sections: the NAB slot block (`Firmware version`, state), `show interface`, `show hardware post`.
- Log patterns: `NAB`, `Network Acceleration`, `link down`, `link up`, `firmware`, `POST`, `PCI`.

## SFP

- Sections: the `SFP (Port N) Details` block (part number, serial, Tx/Rx power if shown), `show interface` (and `show interface <port> detail` if present).
- Log patterns: `link (down|up)`, `SFP`, `transceiver`, `Rx power`, `Tx power`, `LOS`. Count link flaps per port and give the time range.

## HBA (Host Bus Adapter)

- Sections: the HBA slot block, `show hardware detail` WWN lines, `show message-spool`, `show disk` / external-disk sections if present.
- Log patterns: `HBA`, `Fibre Channel`, `FC`, `WWN`, `LUN`, `SCSI`, `multipath`, `I/O error`.
- Note for the parent: the customer must be told the WWNs before an HBA swap (the new HBA's WWNs differ; SAN zoning must change).

## Disk / SSD

- Sections: the `Disk N:` blocks in `show hardware detail`, `show disk` / `show raid` if present.
- Log patterns: `SMART`, `smartd`, `I/O error`, `ata[0-9]`, `sd[a-z]`, `raid`, `degraded`, `medium error`, `Reallocated`.

## Fan

- Sections: `show hardware detail`, `show environment` / sensor sections if present, `show system detail`.
- Log patterns: `fan`, `RPM`, `temperature`, `thermal`, `over-temp`.

## Full appliance

- Sections: `show system detail`, `show hardware post`, `show redundancy`, plus `show version`, `show ip vrf management`, `show console`, `show product-key` (Ops needs these to pre-configure the replacement).
- Log patterns: the patterns of whichever component triggered the escalation, plus `panic`, `watchdog`, `unexpected restart`, `Last Restart Reason`, `kernel`, `MCE`, `machine check`.
- State the escalation reason (e.g. "PSU failure recurred after PSU swap; suspected power housing → full appliance").

## Output format for a manual dig

```
FINDINGS (max 10)
1. [section: show hardware detail] Power module 2: Failed; Operational power supplies: 1
2. [log: <relative path>] 2026-08-02T12:55:24 ... Power Unit: Failure detected
...
SUGGESTED SECTIONS
- show system detail — contains the IPMI SEL power failure lines
- ...
MISSING (looked for, not in GD)
- show hardware post
```
