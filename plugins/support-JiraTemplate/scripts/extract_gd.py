#!/usr/bin/env python3
"""Read-only extractor for Solace gather-diagnostics (GD) bundles, used by the
raise-rma skill.

Subcommands (all output goes to stdout; this script never writes files):

  inventory <gd-path>
      JSON summary of the appliance: hostname, platform, chassis product/serial,
      SolOS version, power modules, blades, SFPs, disks, sections present and
      which RMA-required sections are missing.

  sections <gd-path>
      One command-section name per line.

  section <gd-path> "<cmd>" ["<cmd>" ...]
      Each requested section printed verbatim, including its original #####
      header block (a header is synthesized for the gdh format).

  discover [<dir> ...]
      JSON: extracted GDs (path, hostname, chassis serial/product) and
      gather-diagnostics archives (*.p7m / *.tgz, newest first) found up to 3
      levels below each <dir> (default: . and ~/Downloads). One call replaces
      find + inventory-per-folder + a file picker.

  scan <gd-path> <part>
      Fast evidence scan for the RMA (replaces the evidence subagent):
      current-state health checks from the CLI sections plus a grep of the
      event/system/kernel/messages logs for the part's failure patterns.
      <part> is one of: psu adb nab sfp hba disk fan full. Prints FINDINGS,
      SUGGESTED SECTIONS and MISSING as plain text, with exact quoted lines.

<gd-path> may be an extracted GD folder or the diagnostics file itself. For a
folder the lookup order is: <folder>/cli-diagnostics.txt,
<folder>/<folder.name>/cli-diagnostics.txt, then gdh-diagnostics.txt at the same
two locations, then a shallow search (depth <= 3).

Exit codes: 0 ok; 1 usage error; 2 no diagnostics file found; 3 one or more
requested sections missing (found ones are still printed, missing on stderr).

Python 3 standard library only.
"""

import gzip
import json
import os
import re
import sys
import time

REQUIRED_SECTIONS = [
    "show hardware detail",
    "show version",
    "show product-key",
    "show ip vrf management",
    "show console",
]

# Requested-name aliases (normalized form -> canonical normalized form).
ALIASES = {
    "show hardware details": "show hardware detail",
    "show hardware": "show hardware detail",
    "show product-keys": "show product-key",
    "show product key": "show product-key",
    "show system details": "show system detail",
}

OPEN_SEP_RE = re.compile(r"^\s*[=#\-]{5,}\s*$")
# Only = / # lines terminate a section; tables use --- lines.
TERM_SEP_RE = re.compile(r"^\s*[=#]{5,}\s*$")
CMD_LINE_RE = re.compile(r"^#\s*(?:[\w\-]+\s+)?command:\s*(.+?)\s*$", re.I)
HOST_LINE_RE = re.compile(r"^#\s*host(?:name)?:\s*(.*?)\s*$", re.I)
GDH_PROMPT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)[>#]\s*(\S.*?)\s*$")


class Section:
    def __init__(self, name, host, header_lines, body_lines, synthesized=False):
        self.name = name
        self.host = host
        self.header_lines = header_lines
        self.body_lines = body_lines
        self.synthesized = synthesized

    @property
    def key(self):
        return normalize(self.name)

    def body(self):
        return "\n".join(self.body_lines)

    def render(self):
        lines = list(self.header_lines) + list(self.body_lines)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)


def normalize(cmd):
    return re.sub(r"\s+", " ", cmd.strip().lower())


def canonical(cmd):
    n = normalize(cmd)
    return ALIASES.get(n, n)


# --------------------------------------------------------------------------
# Locating and parsing the diagnostics file
# --------------------------------------------------------------------------

def locate(path):
    path = os.path.expanduser(path)
    if os.path.isfile(path):
        return path
    if not os.path.isdir(path):
        return None
    base = os.path.basename(os.path.normpath(path))
    candidates = []
    for name in ("cli-diagnostics.txt", "gdh-diagnostics.txt"):
        candidates.append(os.path.join(path, name))
        candidates.append(os.path.join(path, base, name))
    for c in candidates:
        if os.path.isfile(c):
            return c
    # Shallow search fallback.
    found = {"cli-diagnostics.txt": [], "gdh-diagnostics.txt": []}
    root_depth = path.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(path):
        if dirpath.count(os.sep) - root_depth >= 3:
            dirnames[:] = []
        for name in found:
            if name in filenames:
                found[name].append(os.path.join(dirpath, name))
    for name in ("cli-diagnostics.txt", "gdh-diagnostics.txt"):
        if found[name]:
            return sorted(found[name], key=len)[0]
    return None


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def parse_cli(lines):
    """Parse the ##### header format used by cli-diagnostics.txt."""
    sections = []
    i, n = 0, len(lines)
    current = None
    while i < n:
        line = lines[i]
        # Detect a header: separator, then '# ... command:' within 3 lines.
        if OPEN_SEP_RE.match(line):
            j = i + 1
            cmd = host = None
            while j < n and j <= i + 4 and lines[j].lstrip().startswith("#") \
                    and not OPEN_SEP_RE.match(lines[j]):
                m = CMD_LINE_RE.match(lines[j].strip())
                if m:
                    cmd = m.group(1)
                h = HOST_LINE_RE.match(lines[j].strip())
                if h:
                    host = h.group(1)
                j += 1
            if cmd and j < n and OPEN_SEP_RE.match(lines[j]):
                if current:
                    sections.append(current)
                current = Section(cmd, host, lines[i:j + 1], [])
                i = j + 1
                continue
            if current and TERM_SEP_RE.match(line):
                sections.append(current)
                current = None
                i += 1
                continue
        if current is not None:
            current.body_lines.append(line)
        i += 1
    if current:
        sections.append(current)
    return sections


def parse_gdh(lines):
    """Parse 'hostname> show xyz' prompt-style output (gdh-diagnostics.txt)."""
    prompts = []
    hosts = {}
    for idx, line in enumerate(lines):
        m = GDH_PROMPT_RE.match(line)
        if m:
            prompts.append((idx, m.group(1), m.group(2)))
            hosts[m.group(1)] = hosts.get(m.group(1), 0) + 1
    if not prompts:
        return []
    main_host = max(hosts, key=hosts.get)
    prompts = [p for p in prompts if p[1] == main_host]
    sections = []
    for k, (idx, host, cmd) in enumerate(prompts):
        end = prompts[k + 1][0] if k + 1 < len(prompts) else len(lines)
        header = [
            "#################################################################",
            "# CLI command: %s" % cmd,
            "# Host:        %s" % host,
            "# (header synthesized from gdh-diagnostics.txt)",
            "#################################################################",
        ]
        sections.append(Section(cmd, host, header, lines[idx + 1:end], True))
    return sections


def load(path):
    f = locate(path)
    if not f:
        sys.stderr.write(
            "ERROR: no diagnostics file found at '%s'.\n"
            "Expected cli-diagnostics.txt or gdh-diagnostics.txt in the folder, "
            "in <folder>/<folder-name>/, or the file path itself.\n"
            "Extract the GD first (e.g. /support-gd-handler:fetch-gds).\n" % path)
        sys.exit(2)
    lines = read_text(f)
    fmt = "gdh" if os.path.basename(f).startswith("gdh") else "cli"
    sections = parse_cli(lines) if fmt == "cli" else parse_gdh(lines)
    if not sections and fmt == "cli":
        alt = parse_gdh(lines)
        if alt:
            sections, fmt = alt, "gdh"
    return f, fmt, sections


def find_sections(sections, cmd):
    want = canonical(cmd)
    exact = [s for s in sections if canonical(s.name) == want]
    if exact:
        return exact
    # Fallback: section names that extend the requested command
    # (e.g. 'show interface' -> 'show interface 1/1/*').
    return [s for s in sections if canonical(s.name).startswith(want + " ")]


# --------------------------------------------------------------------------
# Inventory parsing
# --------------------------------------------------------------------------

def kv(line):
    if ":" not in line:
        return None, None
    k, v = line.split(":", 1)
    return k.strip(), v.strip()


def indent_of(line):
    return len(line) - len(line.lstrip(" \t"))


def parse_hardware(body_lines):
    out = {
        "platform": None, "chassis_product": None, "chassis_serial": None,
        "power": {"redundancy": None, "operational": None, "modules": []},
        "blades": [], "sfps": [], "disks": [],
    }
    lines = body_lines
    n = len(lines)
    i = 0
    slot_re = re.compile(r"^\s*Slot\s+(\d+/\d+)\s*:\s*(.*)$", re.I)
    psu_re = re.compile(r"^\s*Power module\s+(\d+)\s*:\s*(.*)$", re.I)
    disk_re = re.compile(r"^\s*Disk\s+(\d+)\s*:\s*(.*)$", re.I)
    sfp_re = re.compile(r"^\s*SFP\s*\(Port\s*(\w+)\)", re.I)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        k, v = kv(stripped)
        if k is None:
            i += 1
            continue
        kl = k.lower()
        m_slot = slot_re.match(line)
        m_psu = psu_re.match(line)
        m_disk = disk_re.match(line)
        if m_slot:
            slot, desc = m_slot.group(1), m_slot.group(2).strip()
            base = indent_of(line)
            block = []
            j = i + 1
            while j < n and lines[j].strip() and indent_of(lines[j]) > base:
                block.append(lines[j])
                j += 1
            low = desc.lower()
            if low and low != "empty" and not low.startswith("in use by"):
                blade = {"slot": slot, "type": desc, "product": None,
                         "serial": None, "state": None}
                cur_sfp = None
                for b in block:
                    bk, bv = kv(b.strip())
                    ms = sfp_re.match(b)
                    if ms:
                        cur_sfp = {"blade_slot": slot, "port": ms.group(1),
                                   "part_number": None, "serial": None}
                        out["sfps"].append(cur_sfp)
                        continue
                    if bk is None:
                        continue
                    bkl = bk.lower()
                    if cur_sfp is not None and bkl in ("part number", "part #"):
                        cur_sfp["part_number"] = bv
                    elif cur_sfp is not None and bkl in ("serial #", "serial number"):
                        cur_sfp["serial"] = bv
                    elif bkl == "product #" and blade["product"] is None:
                        blade["product"] = bv
                    elif bkl in ("serial #", "serial number") and blade["serial"] is None:
                        blade["serial"] = bv
                    elif bkl == "operational state" and blade["state"] is None:
                        blade["state"] = bv
                out["blades"].append(blade)
            i = j
            continue
        if m_psu:
            out["power"]["modules"].append(
                {"id": int(m_psu.group(1)), "state": m_psu.group(2).strip()})
        elif m_disk:
            base = indent_of(line)
            disk = {"id": int(m_disk.group(1)), "model": None, "serial": None}
            inline = m_disk.group(2).strip()
            if inline:
                disk["model"] = inline
            j = i + 1
            while j < n and lines[j].strip() and indent_of(lines[j]) > base:
                dk, dv = kv(lines[j].strip())
                if dk:
                    dkl = dk.lower()
                    if dkl in ("device model", "model"):
                        disk["model"] = dv
                    elif dkl in ("serial #", "serial number"):
                        disk["serial"] = dv
                j += 1
            out["disks"].append(disk)
            i = j
            continue
        elif kl == "platform":
            out["platform"] = v
        elif kl in ("chassis product #", "chassis product"):
            out["chassis_product"] = v
        elif kl in ("chassis serial", "chassis serial #"):
            out["chassis_serial"] = v
        elif kl == "power redundancy configuration":
            out["power"]["redundancy"] = v
        elif kl == "operational power supplies":
            out["power"]["operational"] = v
        i += 1
    return out


def parse_solos(body):
    m = re.search(r"Current load is:\s*\S*?(\d+\.\d+\.\d+(?:\.\d+)?)", body)
    if m:
        return m.group(1)
    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", body)
    return m.group(1) if m else None


def cmd_inventory(path):
    f, fmt, sections = load(path)
    by_key = {}
    for s in sections:
        by_key.setdefault(canonical(s.name), s)
    hostname = next((s.host for s in sections if s.host), None)
    inv = {"source_file": os.path.abspath(f), "format": fmt, "hostname": hostname}
    hw = by_key.get("show hardware detail")
    hwd = parse_hardware(hw.body_lines) if hw else parse_hardware([])
    ver = by_key.get("show version")
    inv.update({
        "platform": hwd["platform"],
        "chassis_product": hwd["chassis_product"],
        "chassis_serial": hwd["chassis_serial"],
        "solos_version": parse_solos(ver.body()) if ver else None,
        "power": hwd["power"],
        "blades": hwd["blades"],
        "sfps": hwd["sfps"],
        "disks": hwd["disks"],
        "sections_present": [s.name for s in sections],
        "missing_required": [r for r in REQUIRED_SECTIONS if r not in by_key],
    })
    print(json.dumps(inv, indent=2))
    return 0


def cmd_sections(path):
    _, _, sections = load(path)
    seen = set()
    for s in sections:
        if s.name not in seen:
            seen.add(s.name)
            print(s.name)
    return 0


def cmd_section(path, cmds):
    _, _, sections = load(path)
    missing = []
    first = True
    for c in cmds:
        found = find_sections(sections, c)
        if not found:
            missing.append(c)
            continue
        for s in found:
            if not first:
                print()
            print(s.render())
            first = False
    if missing:
        sys.stderr.write("MISSING sections (not in this GD): %s\n"
                         % ", ".join('"%s"' % m for m in missing))
        return 3
    return 0


# --------------------------------------------------------------------------
# Evidence scan
# --------------------------------------------------------------------------

# Log patterns per part type (see context/evidence-guide.md). Each entry is
# (lowercase literal keywords, regex). The keywords are a fast prefilter run
# with bytes.find on the lowercased file; only the lines they hit are checked
# against the regex, which keeps a 50 MB event.log to well under a second.
# "common" is scanned for every part. List order = priority in the output.
# Part patterns must also contain a problem word, so healthy boot chatter
# ("Found ADB in Slot 1/3", smartd banners, driver loads) is not reported.
PROBLEM = (r"(?:fail|error|\bfault|(?-i:\bLOS\b)|\bdown\b|lost|degrad|critical|timeout"
           r"|not ready|offline|unavailable)")
LOG_PATTERNS = {
    "common": [
        (["power_module_"], r"POWER_MODULE_(?:DOWN|UP)"),
        (["power unit"], r"Power Unit"),
        (["panic", "oops:"], r"kernel panic|(?-i:Oops):"),
        (["watchdog"], r"watchdog.*(?:expir|fire|reset|timeout)"),
        (["machine check", "hardware error"], r"Machine Check|Hardware Error"),
        (["edac"], r"(?-i:EDAC).*(?:(?-i:\b[CU]E\b)|corrected|uncorrect)"),
        (["critical_hardware"], r"(?-i:CRITICAL_HARDWARE)"),
        (["pcie link training"], r"PCIe link training fail(?!ures)"),
    ],
    "psu": [(["power supply", "psu", "power module"],
             r"(?:power supply|(?-i:\bPSU\b)|power module).*" + PROBLEM)],
    "adb": [(["adb", "assured delivery", "flash card", "capacitor", "supercap",
              "mate link", "message spool", "system_ad_"],
             r"(?:(?-i:\bADB\b)|Assured Delivery|flash card|capacitor|supercap|mate link"
             r"|message spool|(?-i:SYSTEM_AD_)).*" + PROBLEM)],
    "nab": [(["nab", "network acceleration", "octeon"],
             r"(?:(?-i:\bNAB\b)|Network Acceleration|octeon).*" + PROBLEM),
            (["_link_down"], r"(?-i:_LINK_DOWN)")],
    "sfp": [(["_link_down"], r"(?-i:_LINK_DOWN)"),
            (["sfp", "transceiver", "loss of signal", "rx power", "tx power"],
             r"(?:(?-i:\bSFP\b)|transceiver|[RT]x power).*" + PROBLEM + r"|loss of signal")],
    "hba": [(["hba", "fibre channel", "qla2xxx", "multipath", "lun", "scsi", "i/o error"],
             r"(?:(?-i:\bHBA\b)|Fibre Channel|qla2xxx|multipath|(?-i:\bLUN\b)|SCSI).*"
             + PROBLEM + r"|I/O error")],
    "disk": [(["smart", "ata", "raid", "/dev/sd", "md", "i/o error", "medium error"],
              r"(?:smart|\bata\d+|\bmd\d+\b|raid|/dev/sd[a-z]).*" + PROBLEM
              + r"|I/O error|medium error")],
    "fan": [(["fan", "rpm"], r"\bfan\b.*(?:fail|fault|low|stop)|(?-i:\bRPM\b).*(?:low|fail)"),
            (["temp", "thermal"],
             r"over-?temp|thermal.*(?:trip|shutdown|critical)|temperature.*(?:high|critical)")],
    # Restarts are context, not evidence: always scanned, always listed last.
    "restarts": [(["shutdown_initiated", "unexpected"],
                  r"(?-i:SHUTDOWN_INITIATED)|unexpected (?:restart|reboot)")],
}

PART_SECTIONS = {
    "psu": ["show system detail", "show environment"],
    "adb": ["show message-spool detail", "show hardware post", "show redundancy detail"],
    "nab": ["show hardware post", "show interface detail"],
    "sfp": ["show interface detail"],
    "hba": ["show message-spool detail", "show disk detail"],
    "disk": ["show disk detail"],
    "fan": ["show environment", "show system detail"],
    "full": ["show hardware post", "show system detail", "show redundancy detail",
             "show environment", "show alarm"],
}
# Lines that contain a problem word but are routine.
NOISE_RE = re.compile(r"error reporting|error-reporting|SStatus 0 |errors=(?:panic|remount)|\bdefault\b.*\bSP [AB]\b", re.I)
LOG_NAMES = ("event.log", "system.log", "kernel.log", "messages")
TS_RE = re.compile(r"^\S+\s+(<[^>]+>\s+)?\S+\s+")
MASK_RE = re.compile(r"\b0x[0-9a-f]+\b|\[\s*\d+(?:\.\d+)?\]|\b\d{3,}\b", re.I)


def log_files(gd_dir):
    out = []
    for dirpath, _, filenames in os.walk(gd_dir):
        for name in filenames:
            if any(name == b or name.startswith(b + ".") for b in LOG_NAMES):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def read_bytes(path):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                return fh.read()
        with open(path, "rb") as fh:
            return fh.read()
    except (OSError, EOFError):
        return b""


def candidate_lines(data, keywords):
    """Lines of data containing any keyword (case-insensitive), in file order."""
    low = data.lower()
    starts = set()
    for kw in keywords:
        kwb = kw.encode()
        pos = low.find(kwb)
        while pos != -1:
            s = low.rfind(b"\n", 0, pos) + 1
            starts.add(s)
            e = low.find(b"\n", pos)
            pos = low.find(kwb, e) if e != -1 else -1
    for s in sorted(starts):
        e = data.find(b"\n", s)
        yield data[s:e if e != -1 else len(data)].decode("utf-8", "replace").rstrip("\r")


def scan_logs(gd_dir, part):
    """Group matching log lines by message (timestamp/host/pids masked) and
    return [(count, first_line, last_line, relpath)], in pattern-priority order."""
    entries = LOG_PATTERNS["common"] + LOG_PATTERNS.get(part, []) + LOG_PATTERNS["restarts"]
    keywords = sorted({k for kws, _ in entries for k in kws})
    regexes = [re.compile(rx, re.I) for _, rx in entries]
    groups = {}
    seen = set()
    for f in log_files(gd_dir):
        rel = os.path.relpath(f, gd_dir).replace(os.sep, "/")
        for line in candidate_lines(read_bytes(f), keywords):
            prio = next((i for i, rx in enumerate(regexes) if rx.search(line)), None)
            if prio is None or line in seen or NOISE_RE.search(line):
                continue
            seen.add(line)
            key = MASK_RE.sub("#", TS_RE.sub("", line, count=1)).strip()
            g = groups.get(key)
            if g is None:
                groups[key] = g = [0, line, line, rel, prio]
            g[0] += 1
            if line[:25] < g[1][:25]:
                g[1] = line
            if line[:25] > g[2][:25]:
                g[2] = line
    ranked = sorted(groups.values(), key=lambda g: (g[4], g[1][:25]))
    return [g[:4] for g in ranked]


def health_checks(by_key):
    """Current-state problems ('fault') and notable state ('info') from CLI."""
    fault, info = [], []
    hw = by_key.get("show hardware detail")
    if hw:
        for line in hw.body_lines:
            s = line.strip()
            k, v = kv(s)
            if k is None:
                continue
            kl, vl = k.lower(), v.lower()
            if re.match(r"power module \d+$", kl) and vl not in ("ok",):
                fault.append(("show hardware detail", s))
            elif kl == "operational state" and vl not in ("up", "online"):
                fault.append(("show hardware detail", s))
            elif kl in ("flash card state", "power module state", "state") and \
                    vl and vl not in ("ready", "ok", "up", "online"):
                fault.append(("show hardware detail", s))
            elif kl.startswith("mate link port") and vl != "ok":
                fault.append(("show hardware detail", s))
            elif kl in ("errors", "fatal errors") and v not in ("0", ""):
                fault.append(("show hardware detail", s))
            elif kl == "operational power supplies":
                info.append(("show hardware detail", s))
    post = by_key.get("show hardware post")
    if post:
        for line in post.body_lines:
            s = line.strip()
            if "POST Status" in s and "PASSED" not in s:
                fault.append(("show hardware post", s))
            elif s.startswith("Overall Power-On Self Test"):
                (info if "PASSED" in s else fault).append(("show hardware post", s))
    alarm = by_key.get("show alarm")
    if alarm:
        lines = [l.strip() for l in alarm.body_lines if l.strip()
                 and not l.strip().lower().startswith("alarm display")]
        if any("no current alarms" in l.lower() for l in lines):
            info.append(("show alarm", "No current alarms in the system."))
        else:
            fault.extend(("show alarm", l) for l in lines[:5])
    env = by_key.get("show environment")
    if env:
        bad = [l.strip() for l in env.body_lines
               if re.search(r"\b(fail\w*|critical|warning|not ok|non-recoverable)\b", l, re.I)
               or re.search(r"Power Redundancy\s+no\b", l, re.I)]
        fault.extend(("show environment", l) for l in bad[:5])
        if not bad:
            info.append(("show environment", "all readings OK / no failures"))
    disk = by_key.get("show disk detail")
    if disk:
        degraded = [l.strip() for l in disk.body_lines if re.search(r"\[U*_+U*\]", l)]
        fault.extend(("show disk detail", l) for l in degraded)
        if not degraded and any("[UU]" in l for l in disk.body_lines):
            info.append(("show disk detail", "all md arrays [UU]"))
    sysd = by_key.get("show system detail")
    if sysd:
        for line in sysd.body_lines:
            s = line.strip()
            if s.startswith(("System Uptime", "Last Restart Reason")):
                info.append(("show system detail", s))
    red = by_key.get("show redundancy detail")
    if red:
        for line in red.body_lines:
            s = line.strip()
            if s.startswith(("Redundancy Status", "Last Failure Reason", "Last Failure Time",
                             "Active-Standby Role", "Message Spool Status")):
                info.append(("show redundancy detail", re.sub(r"\s{2,}", " ", s)))
    return fault, info


def cmd_scan(path, part):
    part = part.lower()
    if part not in PART_SECTIONS:
        sys.stderr.write("unknown part '%s'; use one of: %s\n"
                         % (part, " ".join(sorted(PART_SECTIONS))))
        return 1
    f, _, sections = load(path)
    by_key = {}
    for s in sections:
        by_key.setdefault(canonical(s.name), s)
    gd_dir = os.path.dirname(os.path.abspath(f))
    fault, info = health_checks(by_key)
    logs = scan_logs(gd_dir, part)
    hw_logs = [g for g in logs if not re.search(r"SHUTDOWN_INITIATED", g[1])]

    if fault:
        verdict = "CURRENT FAULT in CLI state (see findings)"
    elif hw_logs:
        verdict = "NO CURRENT FAULT in CLI state; log history only (see findings)"
    else:
        verdict = "NO HARDWARE FAULT EVIDENCE in this GD"
    print("VERDICT: %s" % verdict)
    print()
    print("CURRENT STATE")
    for sec, line in fault:
        print("  FAULT [section: %s] %s" % (sec, line))
    shown = set()
    for sec, line in info:
        if line not in shown:
            shown.add(line)
            print("  ok    [section: %s] %s" % (sec, line))
    print()
    print("LOG HISTORY (grouped; count, first, last)")
    if not logs:
        print("  (no matching lines in %s)" % ", ".join(LOG_NAMES))
    for count, first, last, rel in logs[:15]:
        print("  [log: %s] x%d" % (rel, count))
        print("    first: %s" % first[:240])
        if last != first:
            print("    last:  %s" % last[:240])
    if len(logs) > 15:
        print("  ... %d more groups not shown" % (len(logs) - 15))
    print()
    print("SUGGESTED SECTIONS")
    present = [s for s in PART_SECTIONS[part] if s in by_key]
    for s in present:
        print("  - %s" % s)
    print()
    missing = [s for s in PART_SECTIONS[part] if s not in by_key]
    if part == "full":
        missing += [r for r in REQUIRED_SECTIONS if r not in by_key]
    print("MISSING (looked for, not in GD)")
    for s in missing or ["(none)"]:
        print("  - %s" % s)
    return 0


def hw_identity(diag_file):
    """(hostname, chassis_serial, chassis_product) from a diagnostics file,
    parsing only the show hardware detail section."""
    lines = read_text(diag_file)
    fmt_gdh = os.path.basename(diag_file).startswith("gdh")
    sections = parse_gdh(lines) if fmt_gdh else parse_cli(lines)
    hw = next((s for s in sections if canonical(s.name) == "show hardware detail"), None)
    host = next((s.host for s in sections if s.host), None)
    if not hw:
        return host, None, None
    d = parse_hardware(hw.body_lines)
    return host, d["chassis_serial"], d["chassis_product"]


def cmd_discover(dirs):
    dirs = dirs or [".", os.path.expanduser("~/Downloads")]
    extracted, archives, seen = [], [], set()
    for root in dirs:
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        root_depth = os.path.abspath(root).rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if os.path.abspath(dirpath).count(os.sep) - root_depth >= 3:
                dirnames[:] = []
            for name in filenames:
                full = os.path.abspath(os.path.join(dirpath, name))
                if full in seen:
                    continue
                seen.add(full)
                if name in ("cli-diagnostics.txt", "gdh-diagnostics.txt"):
                    host, serial, product = hw_identity(full)
                    extracted.append({"path": os.path.dirname(full), "hostname": host,
                                      "chassis_serial": serial, "chassis_product": product})
                elif name.startswith("gather-diagnostics") and \
                        re.search(r"\.(tgz|zip)(\.p7m)?(\.zip)?$", name):
                    st = os.stat(full)
                    archives.append({"path": full, "size_mb": round(st.st_size / 1e6, 1),
                                     "mtime": st.st_mtime,
                                     "extracted": os.path.isdir(re.sub(r"\.tgz(\.p7m)?$", "", full)
                                                                + "-extracted")})
    archives.sort(key=lambda a: -a["mtime"])
    for a in archives:
        a["modified"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(a.pop("mtime")))
    print(json.dumps({"extracted": extracted, "archives": archives[:10]}, indent=1))
    return 0


def usage():
    sys.stderr.write(
        "usage:\n"
        "  extract_gd.py inventory <gd-path>\n"
        "  extract_gd.py sections <gd-path>\n"
        "  extract_gd.py section <gd-path> \"<cmd>\" [\"<cmd>\" ...]\n"
        "  extract_gd.py discover [<dir> ...]\n"
        "  extract_gd.py scan <gd-path> <psu|adb|nab|sfp|hba|disk|fan|full>\n")
    return 1


def main(argv):
    if len(argv) >= 2 and argv[1] == "discover":
        return cmd_discover(argv[2:])
    if len(argv) < 3:
        return usage()
    sub, path = argv[1], argv[2]
    if sub == "inventory" and len(argv) == 3:
        return cmd_inventory(path)
    if sub == "sections" and len(argv) == 3:
        return cmd_sections(path)
    if sub == "section" and len(argv) >= 4:
        return cmd_section(path, argv[3:])
    if sub == "scan" and len(argv) == 4:
        return cmd_scan(path, argv[3])
    return usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
