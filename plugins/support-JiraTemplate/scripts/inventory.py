#!/usr/bin/env python3
"""
Cross-platform (macOS / Linux) port of lookup_serial.ps1 and read_inventory.ps1.

inventory.sh calls this when powershell.exe isn't available. It produces the same
JSON and exit codes as the PowerShell scripts, so the skills read either one.

    inventory.py lookup -Serial S1,S2 [-Path FILE] [-StateDir DIR] [-Url URL] [-MaxAgeMinutes N] [-Refresh]
    inventory.py list   [-Path FILE] [-StateDir DIR] [-SerialColumn HDR|LETTER] [-Sheet NAME]
                        [-AvailableOnly] [-Format json|table] [-Url URL] [-MaxAgeMinutes N] [-Refresh]

Flags are the PowerShell parameter names and are matched case-insensitively.

Locating the file: an explicit -Path, then a OneDrive/SharePoint-synced copy (on
macOS under ~/Library/CloudStorage/OneDrive-*). The Windows-only fallback of
fetching the file from SharePoint through a hidden Excel (COM) isn't available
here, so the file must be synced or passed with -Path.

Exit codes: 0 ok, 2 bad arguments, 3 file not found / unreadable, 4 required columns not found.
Standard library only.
"""

import fnmatch
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree.ElementTree import XMLParser, iterparse
import xml.etree.ElementTree as ET

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
M = "{%s}" % MAIN

DEFAULT_NAME = "Shipment Inventory Record*.xlsx"
SERIAL_HEADER = re.compile(r"(?i)serial|^\s*s\s*/?\s*n\s*[#.:]?\s*$|^\s*sn\b")
YES = re.compile(r"(?i)^\s*y(es)?\s*$")

# Output key -> sheet header (matched trimmed, case-insensitive). Same as lookup_serial.ps1.
FIELD_MAP = [
    ("customer", "Customer"),
    ("premium_onsite_support", "Premium Onsite Support"),
    ("mtce_can_provide", "Can Support Team Provide MTCE on This Box?"),
    ("mtce_contract_active", "Is MTCE Contract Active?"),
    ("chassis", "Chassis"),
    ("dest_city", "Dest City"),
    ("address", "Address"),
    # Optional from here on: blank when a sheet lacks the column.
    ("hw_spare_provided_by", "Hardware spare provided by"),
]
REQUIRED_COUNT = 7

SCRIPT = "inventory"


def fail(code, msg):
    sys.stderr.write("%s: %s\n" % (SCRIPT, msg))
    sys.exit(code)


def iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat()


# --- arguments ------------------------------------------------------------------

FLAGS = {  # lowercased flag -> (key, takes_value)
    "-serial": ("serial", True), "-path": ("path", True), "-name": ("name", True),
    "-statedir": ("state_dir", True), "-url": ("url", True),
    "-maxageminutes": ("max_age", True), "-refresh": ("refresh", False),
    "-serialcolumn": ("serial_column", True), "-sheet": ("sheet", True),
    "-availableonly": ("available_only", False), "-format": ("format", True),
}


def parse_args(argv):
    if not argv or argv[0] not in ("lookup", "list"):
        fail(2, "usage: inventory.py lookup|list [flags] (see the header of this file)")
    opts = {"mode": argv[0], "serial": [], "name": DEFAULT_NAME, "max_age": 10.0,
            "refresh": False, "available_only": False, "format": "json"}
    i = 1
    while i < len(argv):
        a = argv[i]
        spec = FLAGS.get(a.lower().replace("--", "-", 1)) if a.startswith("-") else None
        if spec is None:
            if opts["mode"] == "lookup" and not a.startswith("-"):
                opts["serial"].append(a); i += 1; continue
            fail(2, "unknown argument: %s" % a)
        key, takes = spec
        if not takes:
            opts[key] = True; i += 1; continue
        if i + 1 >= len(argv):
            fail(2, "%s needs a value" % a)
        v = argv[i + 1]
        if key == "serial":
            opts["serial"].append(v)
        elif key == "max_age":
            try:
                opts["max_age"] = float(v)
            except ValueError:
                fail(2, "-MaxAgeMinutes needs a number")
        else:
            opts[key] = v
        i += 2
    if opts["format"] not in ("json", "table"):
        fail(2, "-Format must be json or table")
    return opts


# --- locating the spreadsheet ---------------------------------------------------

def sync_roots():
    home = Path.home()
    cands = []
    cloud = home / "Library" / "CloudStorage"   # macOS OneDrive / SharePoint sync
    if cloud.is_dir():
        cands += [p for p in cloud.iterdir() if p.is_dir() and p.name.lower().startswith(("onedrive", "sharepoint"))]
    # Legacy macOS OneDrive locations, and the Windows layout if this runs there.
    for pat in ("OneDrive*", "Solace Corporation*", "Solace Systems*"):
        cands += [p for p in home.glob(pat) if p.is_dir()]
    seen, out = set(), []
    for p in cands:
        r = str(p.resolve())
        if r not in seen:
            seen.add(r); out.append(r)
    return sorted(out)


BLOCKED = []   # sync roots macOS privacy settings (TCC) wouldn't let us list


def walk_error(err):
    if isinstance(err, PermissionError) and err.filename not in BLOCKED:
        BLOCKED.append(err.filename)


def find_inventory(pattern):
    exact = pattern.replace("*", "").lower()
    best = None
    for root in sync_roots():
        hits = []
        for dirpath, dirnames, filenames in os.walk(root, onerror=walk_error):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames:
                if f.startswith("~$") or not fnmatch.fnmatch(f.lower(), pattern.lower()):
                    continue
                if re.search(r"(?i)\bcopy\b", os.path.splitext(f)[0]):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    hits.append((f.lower() == exact, os.path.getmtime(p), p))
                except OSError:
                    pass
        if hits:
            hits.sort(reverse=True)   # exact name first, then newest
            return hits[0][2]
    return best


def under_roots(path, roots):
    full = os.path.realpath(path).lower()
    return any(full.startswith(r.rstrip(os.sep).lower() + os.sep) for r in roots)


def resolve_inventory(opts):
    """Returns dict(path, origin, as_of, fetched) like Resolve-Inventory."""
    path = opts.get("path")
    if path:
        if not os.path.isfile(path):
            fail(3, "file not found: %s" % path)
        p = os.path.realpath(path)
        return {"path": p, "origin": "path", "as_of": os.path.getmtime(p), "fetched": False}

    state = opts.get("state_dir")
    cache = os.path.join(state, "inventory_path.txt") if state else None
    roots = sync_roots()
    synced = None
    if cache and os.path.isfile(cache):
        try:
            cached = Path(cache).read_text(encoding="utf-8-sig").strip()
        except OSError:
            cached = ""
        if cached and os.path.isfile(cached) and under_roots(cached, roots):
            synced = os.path.realpath(cached)
    if not synced:
        synced = find_inventory(opts.get("name") or DEFAULT_NAME)
    if synced:
        if cache:
            os.makedirs(state, exist_ok=True)
            Path(cache).write_text(synced, encoding="utf-8")
        return {"path": synced, "origin": "synced", "as_of": os.path.getmtime(synced), "fetched": False}

    # A SharePoint copy fetched earlier on Windows (same state dir) is still usable if fresh.
    if state:
        copy = os.path.join(state, "sharepoint", "Shipment Inventory Record.xlsx")
        if os.path.isfile(copy) and not opts["refresh"] and (time.time() - os.path.getmtime(copy)) / 60 < opts["max_age"]:
            return {"path": copy, "origin": "sharepoint", "as_of": os.path.getmtime(copy), "fetched": False}

    if BLOCKED and sys.platform == "darwin":
        fail(3, "macOS blocked access to %s (Operation not permitted), so the synced spreadsheet can't be read. "
                "Give the app running Claude Code (e.g. Terminal, iTerm, Termius, IntelliJ) access in System Settings > "
                "Privacy & Security > Full Disk Access (or Files and Folders), then quit and reopen that app."
                % "; ".join(BLOCKED))
    fail(3, "no OneDrive-synced copy of '%s' was found (searched: %s). On macOS/Linux the file can't be "
            "fetched from SharePoint through Excel. Sync it instead: in the browser, open the Operations site's "
            "Shared Documents and choose 'Add shortcut to My files' on the 'Ship Spreadsheet' folder (OneDrive "
            "for Mac then shows it under ~/Library/CloudStorage/OneDrive-*), or download the file and set the "
            "plugin's inventory_path (or pass --path)." % (opts.get("name") or DEFAULT_NAME, "; ".join(roots) or "none"))


# --- xlsx parsing ---------------------------------------------------------------

def load_xml(zf, name):
    try:
        with zf.open(name) as f:
            return ET.parse(f, parser=XMLParser()).getroot()
    except KeyError:
        return None


def strike_flag(props):
    """props: rPr / font element or None -> -1 (no strike element), 0 or 1."""
    if props is None:
        return -1
    s = props.find(M + "strike")
    if s is None:
        return -1
    return 0 if s.get("val") in ("0", "false") else 1


def read_string_item(el):
    """<si> / <is> -> (text, run strike flags of visible runs or None for plain strings)."""
    text, runs = [], None
    for child in el:
        if child.tag == M + "t":
            text.append(child.text or "")
        elif child.tag == M + "r":
            if runs is None:
                runs = []
            rt = "".join(t.text or "" for t in child.findall(M + "t"))
            text.append(rt)
            if rt.strip():
                runs.append(strike_flag(child.find(M + "rPr")))
        # rPh / phoneticPr ignored
    return "".join(text), runs


def is_date_format(fid, code):
    if 14 <= fid <= 22 or 45 <= fid <= 47:
        return True
    if not code:
        return False
    bare = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code)
    return bool(re.search(r"[dmyhs]", bare)) and not re.match(r"^[#0.,%E+\-\s]*$", bare)


def to_int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def col_index(ref, fallback):
    n, i = 0, 0
    while i < len(ref) and ref[i].isalpha():
        n = n * 26 + (ord(ref[i].upper()) - 64); i += 1
    return n if i else fallback


def col_letter(n):
    s = ""
    while n > 0:
        m = (n - 1) % 26; s = chr(65 + m) + s; n = (n - 1) // 26
    return s


class Workbook:
    def __init__(self, path):
        try:
            self.zf = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            fail(3, "cannot open %s: %s" % (path, e))
        wb = load_xml(self.zf, "xl/workbook.xml")
        if wb is None:
            fail(3, "%s is not an .xlsx workbook" % path)
        pr = wb.find(M + "workbookPr")
        self.date1904 = pr is not None and pr.get("date1904") in ("1", "true")

        self.sst = []
        sst = load_xml(self.zf, "xl/sharedStrings.xml")
        if sst is not None:
            self.sst = [read_string_item(si) for si in sst.findall(M + "si")]

        self.xf_strike, self.xf_date = [], []
        st = load_xml(self.zf, "xl/styles.xml")
        if st is not None:
            fonts = [strike_flag(f) == 1 for f in st.findall(M + "fonts/" + M + "font")]
            fmts = {to_int(f.get("numFmtId")): f.get("formatCode") for f in st.findall(M + "numFmts/" + M + "numFmt")}
            for xf in st.findall(M + "cellXfs/" + M + "xf"):
                fid, nid = to_int(xf.get("fontId")), to_int(xf.get("numFmtId"))
                self.xf_strike.append(fid < len(fonts) and fonts[fid])
                self.xf_date.append(is_date_format(nid, fmts.get(nid)))

        rels = {}
        rd = load_xml(self.zf, "xl/_rels/workbook.xml.rels")
        if rd is not None:
            for e in rd.findall("{%s}Relationship" % PKG_REL):
                t = e.get("Target", "").lstrip("/")
                if not t.startswith("xl/"):
                    t = "xl/" + t
                rels[e.get("Id")] = t
        self.sheets = []   # (name, state, part)
        for sh in wb.findall(M + "sheets/" + M + "sheet"):
            part = rels.get(sh.get("{%s}id" % REL))
            if part and part in self.zf.namelist():
                self.sheets.append((sh.get("name"), sh.get("state") or "visible", part))

    def cell_text(self, c):
        """c: <c> element -> (trimmed text, strike none|partial|full)."""
        t, s = c.get("t"), to_int(c.get("s"))
        v = c.find(M + "v")
        raw = v.text if v is not None else None
        runs = None
        if t == "s":
            i = to_int(raw) if raw is not None else -1
            if raw is None or not (0 <= i < len(self.sst)):
                return "", "none"
            text, runs = self.sst[i]
        elif t == "inlineStr":
            el = c.find(M + "is")
            text, runs = read_string_item(el) if el is not None else ("", None)
        elif t == "b":
            text = "TRUE" if raw == "1" else "FALSE"
        else:
            text = raw or ""
            if raw is not None and t != "str" and s < len(self.xf_date) and self.xf_date[s]:
                try:
                    num = float(raw) + (1462 if self.date1904 else 0)
                    dt = datetime(1899, 12, 30) + timedelta(days=num)
                    text = dt.strftime("%Y-%m-%d %H:%M") if (dt.hour or dt.minute or dt.second or dt.microsecond) else dt.strftime("%Y-%m-%d")
                except (ValueError, OverflowError):
                    pass
        text = text.strip()
        if not text:
            return "", "none"
        font = s < len(self.xf_strike) and self.xf_strike[s]
        strike = "none"
        if runs:
            struck = sum(1 for f in runs if f == 1 or (f == -1 and font))
            strike = "full" if struck == len(runs) else "partial" if struck else "none"
        elif font:
            strike = "full"
        return text, strike

    def rows(self, part):
        """Yields ('row', rownum, hidden, [(col, <c>)]) per row, then ('cf',) if the sheet has conditional formatting."""
        cf = False
        with self.zf.open(part) as f:
            for event, el in iterparse(f, events=("end",)):
                if el.tag == M + "row":
                    cells, nxt = [], 1
                    for c in el.findall(M + "c"):
                        col = col_index(c.get("r") or "", nxt); nxt = col + 1
                        cells.append((col, c))
                    yield ("row", to_int(el.get("r")), el.get("hidden") in ("1", "true"), cells)
                    el.clear()
                elif el.tag == M + "conditionalFormatting":
                    cf = True
        if cf:
            yield ("cf",)


# --- lookup ---------------------------------------------------------------------

def run_lookup(opts):
    serials = []
    for s in opts["serial"]:
        for tok in re.split(r"[,\s]+", s):
            tok = tok.strip()
            if tok and tok.upper() not in (x.upper() for x in serials):
                serials.append(tok)
    if not serials:
        fail(2, "no serial number given")
    want = {s.upper() for s in serials}
    wanted = [h for _, h in FIELD_MAP]

    t0 = time.time()
    src = resolve_inventory(opts)
    t1 = time.time()
    wb = Workbook(src["path"])
    matches, searched, cf_sheets, skipped = [], [], [], []

    for name, state, part in wb.sheets:
        serial_col, seen_rows, wanted_cols, serial_header = 0, 0, None, None
        done = False
        for item in wb.rows(part):
            if item[0] == "cf":
                if serial_col and name not in cf_sheets:
                    cf_sheets.append(name)
                continue
            if done:
                continue
            _, rownum, hidden, cells = item
            if not cells:
                continue
            if not serial_col:   # still looking for the header row
                texts = {}
                for col, c in cells:
                    t, _ = wb.cell_text(c)
                    if t:
                        texts[col] = t
                if not texts:
                    continue
                seen_rows += 1
                if seen_rows > 25:
                    done = True; continue
                found = next((col for col, _ in cells if col in texts and SERIAL_HEADER.search(texts[col])), 0)
                if not found:
                    continue
                wanted_cols, missing = [], []
                for i, h in enumerate(wanted):
                    col = next((k for k, v in texts.items() if v.strip().lower() == h.lower()), 0)
                    wanted_cols.append(col)
                    if not col and i < REQUIRED_COUNT:
                        missing.append(h)
                if missing:
                    skipped.append("%s (missing: %s)" % (name, ", ".join(missing)))
                    done = True; continue
                serial_col, serial_header = found, texts[found]
                searched.append(name)
                continue

            sc = next((c for col, c in cells if col == serial_col), None)
            if sc is None:
                continue
            serial, strike = wb.cell_text(sc)
            if not serial or serial == serial_header or serial.upper() not in want:
                continue
            by_col = {col: c for col, c in cells}
            values = [wb.cell_text(by_col[wc])[0] if wc in by_col else "" for wc in wanted_cols]
            matches.append({"sheet": name, "row": rownum, "hidden": hidden, "serial": serial,
                            "strike": strike, "values": values})
    t2 = time.time()

    if not searched:
        fail(4, "no sheet has all of the columns: %s. Skipped: %s" % (", ".join(wanted[:REQUIRED_COUNT]), "; ".join(skipped)))

    keys = [k for k, _ in FIELD_MAP]
    results = []
    for m in matches:
        f = dict(zip(keys, m["values"]))
        results.append({
            "serial": m["serial"],
            "sheet": m["sheet"],
            "row": m["row"],
            "customer": f["customer"],
            "support_tier": "Platinum+" if YES.match(f["premium_onsite_support"]) else "Platinum",
            "premium_onsite_support": f["premium_onsite_support"],
            "mtce_eligible": bool(YES.match(f["mtce_can_provide"]) and YES.match(f["mtce_contract_active"])),
            "mtce_can_provide": f["mtce_can_provide"],
            "mtce_contract_active": f["mtce_contract_active"],
            "chassis": f["chassis"],
            "dest_city": f["dest_city"],
            "address": f["address"],
            "hw_spare_provided_by": f["hw_spare_provided_by"],
            "crossed_off": m["strike"] == "full",
            "serial_strike": m["strike"],
            "hidden_row": m["hidden"],
        })
    found = {m["serial"].upper() for m in matches}
    out = {
        "source": src["path"],
        "origin": src["origin"],
        "as_of": iso(src["as_of"]),
        "fetched": src["fetched"],
        "seconds": {"locate": round(t1 - t0, 1), "scan": round(t2 - t1, 1)},
        "searched": searched,
        "results": results,
        "not_found": [s for s in serials if s.upper() not in found],
        "warnings": ["sheet '%s' uses conditional formatting; strikethrough applied that way is not detected" % s for s in cf_sheets],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


# --- listing --------------------------------------------------------------------

def run_list(opts):
    src = resolve_inventory(opts)
    wb = Workbook(src["path"])
    want_col = opts.get("serial_column")
    letter = want_col and re.match(r"^[A-Za-z]{1,3}$", want_col)
    warnings, rows_out, sheets_out = [], [], []

    for name, state, part in wb.sheets:
        if opts.get("sheet") and name != opts["sheet"]:
            continue
        parsed, has_cf = [], False
        for item in wb.rows(part):
            if item[0] == "cf":
                has_cf = True; continue
            _, rownum, hidden, cells = item
            cmap = {}
            for col, c in cells:
                t, s = wb.cell_text(c)
                if t:
                    cmap[col] = (t, s)
            if cmap:
                parsed.append((rownum, hidden, cmap))
        if has_cf:
            warnings.append("sheet '%s' uses conditional formatting; strikethrough applied that way is not detected" % name)

        header_idx, serial_col = -1, 0
        for i in range(min(len(parsed), 25)):
            for col in sorted(parsed[i][2]):
                h = parsed[i][2][col][0]
                if letter:
                    match = col == col_index(want_col, 0)
                elif want_col:
                    match = h == want_col
                else:
                    match = bool(SERIAL_HEADER.search(h))
                if match:
                    header_idx, serial_col = i, col
                    break
            if header_idx >= 0:
                break
        if header_idx < 0:
            warnings.append("sheet '%s': no serial-number column found, skipped" % name)
            sheets_out.append({"name": name, "state": state, "serial_column": None, "rows": 0, "available": 0, "crossed_off": 0})
            continue

        headers, seen = {}, set()
        for col in sorted(parsed[header_idx][2]):
            h = parsed[header_idx][2][col][0]
            if h in seen:
                h = "%s (%s)" % (h, col_letter(col))
            seen.add(h); headers[col] = h
        serial_header = headers[serial_col]

        count = avail = 0
        for rownum, hidden, cmap in parsed[header_idx + 1:]:
            cell = cmap.get(serial_col)
            if not cell or cell[0] == serial_header:
                continue
            fields, struck = {}, []
            for col in sorted(cmap):
                key = headers.get(col, col_letter(col))
                fields[key] = cmap[col][0]
                if cmap[col][1] != "none":
                    struck.append(key)
            crossed = cell[1] == "full"
            count += 1
            if not crossed:
                avail += 1
            if opts["available_only"] and crossed:
                continue
            rows_out.append({"sheet": name, "row": rownum, "serial": cell[0], "crossed_off": crossed,
                             "serial_strike": cell[1], "struck_fields": struck, "hidden_row": hidden, "fields": fields})
        sheets_out.append({"name": name, "state": state, "serial_column": serial_header,
                           "rows": count, "available": avail, "crossed_off": count - avail})

    if not any(s["serial_column"] for s in sheets_out):
        fail(4, "no serial-number column found in %s. %s -- pass -SerialColumn." % (src["path"], "; ".join(warnings)))

    mtime = os.path.getmtime(src["path"])
    if opts["format"] == "table":
        print("Source:   %s (%s)" % (src["path"], src["origin"]))
        print("Modified: %s" % datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"))
        for s in sheets_out:
            print("Sheet '%s': %d available, %d crossed off" % (s["name"], s["available"], s["crossed_off"]))
        for w in warnings:
            print("Warning:  %s" % w)
        print("%-20s %6s  %-24s %-11s %s" % ("sheet", "row", "serial", "crossed_off", "serial_strike"))
        for r in rows_out:
            print("%-20s %6d  %-24s %-11s %s" % (r["sheet"][:20], r["row"], r["serial"][:24], r["crossed_off"], r["serial_strike"]))
        return
    print(json.dumps({
        "source": src["path"],
        "origin": src["origin"],
        "last_modified": iso(mtime),
        "read_at": datetime.now().astimezone().isoformat(),
        "sheets": sheets_out,
        "warnings": warnings,
        "rows": rows_out,
    }, indent=2, ensure_ascii=False))


def main():
    opts = parse_args(sys.argv[1:])
    if opts["mode"] == "lookup":
        run_lookup(opts)
    else:
        run_list(opts)


if __name__ == "__main__":
    main()
