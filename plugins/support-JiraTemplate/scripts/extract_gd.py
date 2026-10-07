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

<gd-path> may be an extracted GD folder or the diagnostics file itself. For a
folder the lookup order is: <folder>/cli-diagnostics.txt,
<folder>/<folder.name>/cli-diagnostics.txt, then gdh-diagnostics.txt at the same
two locations, then a shallow search (depth <= 3).

Exit codes: 0 ok; 1 usage error; 2 no diagnostics file found; 3 one or more
requested sections missing (found ones are still printed, missing on stderr).

Python 3 standard library only.
"""

import json
import os
import re
import sys

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


def usage():
    sys.stderr.write(
        "usage:\n"
        "  extract_gd.py inventory <gd-path>\n"
        "  extract_gd.py sections <gd-path>\n"
        "  extract_gd.py section <gd-path> \"<cmd>\" [\"<cmd>\" ...]\n")
    return 1


def main(argv):
    if len(argv) < 3:
        return usage()
    sub, path = argv[1], argv[2]
    if sub == "inventory" and len(argv) == 3:
        return cmd_inventory(path)
    if sub == "sections" and len(argv) == 3:
        return cmd_sections(path)
    if sub == "section" and len(argv) >= 4:
        return cmd_section(path, argv[3:])
    return usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
