<#
.SYNOPSIS
  Look up one or more serial numbers in the Shipment Inventory Record and return
  only the RMA facts: customer, support tier, MTCE eligibility, chassis, dest city,
  address and who provides the hardware spare.

.DESCRIPTION
  Built for speed. A compiled scanner (C#, cached as a DLL after the first run)
  streams the sheet XML once, compares only the serial column of each row, and
  reads the remaining cells of a row only when its serial matches. Sheets that
  lack the needed columns are abandoned as soon as their header row is seen.

  The file is located like read_inventory.ps1 does (explicit path, synced copy,
  then a SharePoint copy fetched through Excel and reused for -MaxAgeMinutes).

.OUTPUTS
  JSON. Exit codes: 0 ok (check not_found), 3 file not found / unreadable,
  4 no sheet has the required columns.
#>
[CmdletBinding()]
param(
  # One or more serials; commas or spaces also separate them.
  [Parameter(Mandatory)] [string[]]$Serial,
  [string]$Path,
  [string]$Name = 'Shipment Inventory Record*.xlsx',
  [string]$StateDir,
  [string]$Url,
  [double]$MaxAgeMinutes = 10,
  [switch]$Refresh
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'inventory_source.ps1')

# Output key -> sheet header (matched trimmed, case-insensitive).
$FieldMap = [ordered]@{
  customer               = 'Customer'
  premium_onsite_support = 'Premium Onsite Support'
  mtce_can_provide       = 'Can Support Team Provide MTCE on This Box?'
  mtce_contract_active   = 'Is MTCE Contract Active?'
  chassis                = 'Chassis'
  dest_city              = 'Dest City'
  address                = 'Address'
  # Optional from here on: blank when a sheet lacks the column.
  hw_spare_provided_by   = 'Hardware spare provided by'
}

$RequiredCount = 7   # the first 7 FieldMap entries must exist on a sheet

$serials = @($Serial | ForEach-Object { $_ -split '[,\s]+' } | Where-Object { $_ } | ForEach-Object { $_.Trim() } | Select-Object -Unique)
if (-not $serials.Count) { Fail 2 'no serial number given' }

# --- compiled scanner ----------------------------------------------------------

$cs = @'
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;

namespace SupportRma {
  public class LookupMatch {
    public string Sheet; public string State; public int Row; public bool Hidden;
    public string Serial; public string SerialStrike; public string[] Values;
  }
  public class LookupResult {
    public List<LookupMatch> Matches = new List<LookupMatch>();
    public List<string> SheetsSearched = new List<string>();
    public List<string> ConditionalFormatting = new List<string>();
    public List<string> Skipped = new List<string>();   // "sheet: headers" for sheets lacking the columns
  }

  public static class SerialScanner {
    const string Main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
    const string Rel  = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
    static readonly Regex SerialHeader = new Regex(@"(?i)serial|^\s*s\s*/?\s*n\s*[#.:]?\s*$|^\s*sn\b");

    class Cell { public int Col; public string T; public int S; public string V; public string Inline; public sbyte[] InlineRuns; }

    // per shared string: text, and strike flags of visible rich-text runs (null = plain)
    static List<string> sst; static Dictionary<int, sbyte[]> sstRuns;
    static bool[] xfStrike, xfDate; static bool date1904;

    public static LookupResult Run(string path, string[] serials, string[] wanted, int required) {
      var want = new HashSet<string>(serials, StringComparer.OrdinalIgnoreCase);
      var res = new LookupResult();
      using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
      using (var zip = new ZipArchive(fs, ZipArchiveMode.Read)) {
        var wb = LoadDoc(zip, "xl/workbook.xml");
        if (wb == null) throw new InvalidDataException(path + " is not an .xlsx workbook");
        var ns = new XmlNamespaceManager(wb.NameTable); ns.AddNamespace("x", Main); ns.AddNamespace("r", Rel);
        var pr = wb.SelectSingleNode("/x:workbook/x:workbookPr", ns) as XmlElement;
        date1904 = pr != null && (pr.GetAttribute("date1904") == "1" || pr.GetAttribute("date1904") == "true");
        LoadSharedStrings(zip);
        LoadStyles(zip);

        var rels = new Dictionary<string, string>();
        var relDoc = LoadDoc(zip, "xl/_rels/workbook.xml.rels");
        if (relDoc != null) foreach (XmlElement e in relDoc.GetElementsByTagName("Relationship")) {
          string t = e.GetAttribute("Target").TrimStart('/');
          if (!t.StartsWith("xl/")) t = "xl/" + t;
          rels[e.GetAttribute("Id")] = t;
        }

        foreach (XmlElement sh in wb.SelectNodes("/x:workbook/x:sheets/x:sheet", ns)) {
          string rid = sh.GetAttribute("id", Rel), part;
          if (!rels.TryGetValue(rid, out part)) continue;
          var entry = zip.GetEntry(part);
          if (entry == null) continue;
          string state = sh.GetAttribute("state"); if (state == "") state = "visible";
          using (var s = entry.Open()) ScanSheet(s, sh.GetAttribute("name"), state, want, wanted, required, res);
        }
      }
      sst = null; sstRuns = null;
      return res;
    }

    static XmlReaderSettings Settings() {
      var st = new XmlReaderSettings(); st.IgnoreWhitespace = false; st.IgnoreComments = true;
      st.DtdProcessing = DtdProcessing.Prohibit; st.XmlResolver = null; return st;
    }

    static XmlDocument LoadDoc(ZipArchive zip, string name) {
      var e = zip.GetEntry(name); if (e == null) return null;
      var d = new XmlDocument(); d.XmlResolver = null;
      using (var s = e.Open()) using (var r = XmlReader.Create(s, Settings())) d.Load(r);
      return d;
    }

    static sbyte StrikeFlag(XmlReader r) {   // positioned on <strike>
      string v = r.GetAttribute("val");
      return (sbyte)((v == "0" || v == "false") ? 0 : 1);
    }

    // Positioned on an element; returns its text and leaves the reader on its end tag.
    static string ReadTextContent(XmlReader r) {
      if (r.IsEmptyElement) return "";
      var sb = new StringBuilder(); int d = r.Depth;
      while (r.Read()) {
        if (r.NodeType == XmlNodeType.EndElement && r.Depth == d) break;
        if (r.NodeType == XmlNodeType.Text || r.NodeType == XmlNodeType.CDATA ||
            r.NodeType == XmlNodeType.Whitespace || r.NodeType == XmlNodeType.SignificantWhitespace) sb.Append(r.Value);
      }
      return sb.ToString();
    }

    // Positioned on <si> or <is>: text of direct <t> and <r><t> (phonetic <rPh> is ignored),
    // plus the strike flag of each visible rich-text run (-1 = inherits the cell font).
    static string ReadStringItem(XmlReader r, out sbyte[] runs) {
      runs = null;
      if (r.IsEmptyElement) return "";
      var text = new StringBuilder(); List<sbyte> flags = null;
      StringBuilder run = null; sbyte runStrike = -1;
      int d = r.Depth;
      while (r.Read()) {
        if (r.NodeType == XmlNodeType.EndElement) {
          if (r.Depth == d) break;
          if (run != null && r.Depth == d + 1 && r.LocalName == "r") {
            string rt = run.ToString(); text.Append(rt);
            if (rt.Trim().Length > 0) flags.Add(runStrike);
            run = null;
          }
          continue;
        }
        if (r.NodeType != XmlNodeType.Element) continue;
        if (r.Depth == d + 1) {
          if (r.LocalName == "t") text.Append(ReadTextContent(r));
          else if (r.LocalName == "r") { if (flags == null) flags = new List<sbyte>(); if (!r.IsEmptyElement) { run = new StringBuilder(); runStrike = -1; } }
          else ReadTextContent(r);   // rPh, phoneticPr: consume and ignore
        } else if (run != null && r.Depth == d + 2 && r.LocalName == "t") run.Append(ReadTextContent(r));
        else if (run != null && r.Depth == d + 3 && r.LocalName == "strike") runStrike = StrikeFlag(r);
      }
      if (flags != null) runs = flags.ToArray();
      return text.ToString();
    }

    static void LoadSharedStrings(ZipArchive zip) {
      sst = new List<string>(); sstRuns = new Dictionary<int, sbyte[]>();
      var e = zip.GetEntry("xl/sharedStrings.xml"); if (e == null) return;
      using (var s = e.Open()) using (var r = XmlReader.Create(s, Settings())) {
        while (r.Read()) {
          if (r.NodeType == XmlNodeType.Element && r.LocalName == "si") {
            sbyte[] runs; string t = ReadStringItem(r, out runs);
            if (runs != null) sstRuns[sst.Count] = runs;
            sst.Add(t);
          }
        }
      }
    }

    static bool IsDateFormat(int id, string code) {
      if ((id >= 14 && id <= 22) || (id >= 45 && id <= 47)) return true;
      if (string.IsNullOrEmpty(code)) return false;
      string bare = Regex.Replace(code, "\"[^\"]*\"|\\[[^\\]]*\\]|\\\\.", "");
      return Regex.IsMatch(bare, "[dmyhs]") && !Regex.IsMatch(bare, @"^[#0.,%E+\-\s]*$");
    }

    static void LoadStyles(ZipArchive zip) {
      xfStrike = new bool[0]; xfDate = new bool[0];
      var d = LoadDoc(zip, "xl/styles.xml"); if (d == null) return;
      var ns = new XmlNamespaceManager(d.NameTable); ns.AddNamespace("x", Main);
      var fontStrike = new List<bool>();
      foreach (XmlElement f in d.SelectNodes("/x:styleSheet/x:fonts/x:font", ns)) {
        var st = f.SelectSingleNode("x:strike", ns) as XmlElement;
        fontStrike.Add(st != null && st.GetAttribute("val") != "0" && st.GetAttribute("val") != "false");
      }
      var fmts = new Dictionary<int, string>();
      foreach (XmlElement f in d.SelectNodes("/x:styleSheet/x:numFmts/x:numFmt", ns))
        fmts[int.Parse(f.GetAttribute("numFmtId"))] = f.GetAttribute("formatCode");
      var xs = d.SelectNodes("/x:styleSheet/x:cellXfs/x:xf", ns);
      xfStrike = new bool[xs.Count]; xfDate = new bool[xs.Count];
      for (int i = 0; i < xs.Count; i++) {
        var xf = (XmlElement)xs[i];
        int fid = ToInt(xf.GetAttribute("fontId")), nid = ToInt(xf.GetAttribute("numFmtId"));
        xfStrike[i] = fid < fontStrike.Count && fontStrike[fid];
        string code; fmts.TryGetValue(nid, out code);
        xfDate[i] = IsDateFormat(nid, code);
      }
    }

    static int ToInt(string s) { int n; return int.TryParse(s, out n) ? n : 0; }

    static int ColIndex(string cellRef, int fallback) {
      int n = 0, i = 0;
      while (i < cellRef.Length && char.IsLetter(cellRef[i])) { n = n * 26 + (char.ToUpperInvariant(cellRef[i]) - 'A' + 1); i++; }
      return i == 0 ? fallback : n;
    }

    // Reads the cells of the current <row>; returns false for an empty row element.
    // Reads the cells of the current <row>, leaving the reader on its end tag.
    static List<Cell> ReadRow(XmlReader r) {
      var cells = new List<Cell>();
      if (r.IsEmptyElement) return cells;
      int d = r.Depth, next = 1;
      while (r.Read()) {
        if (r.NodeType == XmlNodeType.EndElement && r.Depth == d) break;
        if (r.NodeType != XmlNodeType.Element || r.Depth != d + 1 || r.LocalName != "c") continue;
        var c = new Cell();
        string cref = r.GetAttribute("r");
        c.Col = cref == null ? next : ColIndex(cref, next); next = c.Col + 1;
        c.T = r.GetAttribute("t"); c.S = ToInt(r.GetAttribute("s"));
        if (!r.IsEmptyElement) {
          int cd = r.Depth;
          while (r.Read()) {
            if (r.NodeType == XmlNodeType.EndElement && r.Depth == cd) break;
            if (r.NodeType != XmlNodeType.Element || r.Depth != cd + 1) continue;
            if (r.LocalName == "v") c.V = ReadTextContent(r);
            else if (r.LocalName == "is") { sbyte[] runs; c.Inline = ReadStringItem(r, out runs); c.InlineRuns = runs; }
            else ReadTextContent(r);   // <f> etc.
          }
        }
        cells.Add(c);
      }
      return cells;
    }

    // Cell text plus strike state: none | partial | full (same rules as read_inventory.ps1).
    static string Text(Cell c, out string strike) {
      strike = "none";
      string text; sbyte[] runs = null;
      if (c.T == "s") {
        int i; if (c.V == null || !int.TryParse(c.V, out i) || i < 0 || i >= sst.Count) return "";
        text = sst[i]; sstRuns.TryGetValue(i, out runs);
      } else if (c.T == "inlineStr") { text = c.Inline ?? ""; runs = c.InlineRuns; }
      else if (c.T == "b") text = c.V == "1" ? "TRUE" : "FALSE";
      else {
        text = c.V ?? "";
        double num;
        if (c.V != null && c.T != "str" && c.S < xfDate.Length && xfDate[c.S] &&
            double.TryParse(c.V, NumberStyles.Float, CultureInfo.InvariantCulture, out num)) {
          if (date1904) num += 1462;
          try { var dt = DateTime.FromOADate(num); text = dt.TimeOfDay.Ticks != 0 ? dt.ToString("yyyy-MM-dd HH:mm") : dt.ToString("yyyy-MM-dd"); } catch (ArgumentException) { }
        }
      }
      text = text.Trim();
      if (text.Length == 0) return "";
      bool font = c.S < xfStrike.Length && xfStrike[c.S];
      if (runs != null && runs.Length > 0) {
        int struck = 0;
        foreach (var f in runs) if (f == 1 || (f == -1 && font)) struck++;
        strike = struck == runs.Length ? "full" : struck > 0 ? "partial" : "none";
      } else if (font) strike = "full";
      return text;
    }

    static string Text(Cell c) { string s; return Text(c, out s); }

    static void ScanSheet(Stream s, string name, string state, HashSet<string> want, string[] wanted, int required, LookupResult res) {
      int serialCol = 0, seenRows = 0; int[] wantedCols = null; string serialHeader = null;
      using (var r = XmlReader.Create(s, Settings())) {
        while (r.Read()) {
          if (r.NodeType != XmlNodeType.Element) continue;
          if (r.LocalName == "conditionalFormatting") { if (serialCol > 0 && !res.ConditionalFormatting.Contains(name)) res.ConditionalFormatting.Add(name); continue; }
          if (r.LocalName != "row") continue;
          bool hidden = r.GetAttribute("hidden") == "1" || r.GetAttribute("hidden") == "true";
          int rowNum = ToInt(r.GetAttribute("r"));
          var cells = ReadRow(r);
          if (cells.Count == 0) continue;

          if (serialCol == 0) {   // still looking for the header row
            var texts = new Dictionary<int, string>();
            foreach (var c in cells) { string t = Text(c); if (t.Length > 0) texts[c.Col] = t; }
            if (texts.Count == 0) continue;
            if (++seenRows > 25) return;
            int found = 0;
            foreach (var c in cells) { string t; if (texts.TryGetValue(c.Col, out t) && SerialHeader.IsMatch(t)) { found = c.Col; break; } }
            if (found == 0) continue;
            wantedCols = new int[wanted.Length];
            var missing = new List<string>();
            for (int i = 0; i < wanted.Length; i++) {
              foreach (var kv in texts) if (string.Equals(kv.Value.Trim(), wanted[i], StringComparison.OrdinalIgnoreCase)) { wantedCols[i] = kv.Key; break; }
              if (wantedCols[i] == 0 && i < required) missing.Add(wanted[i]);
            }
            if (missing.Count > 0) { res.Skipped.Add(name + " (missing: " + string.Join(", ", missing) + ")"); return; }
            serialCol = found; serialHeader = texts[found];
            res.SheetsSearched.Add(name);
            continue;
          }

          Cell sc = null;
          foreach (var c in cells) if (c.Col == serialCol) { sc = c; break; }
          if (sc == null) continue;
          string strike, serial = Text(sc, out strike);
          if (serial.Length == 0 || serial == serialHeader || !want.Contains(serial)) continue;

          var m = new LookupMatch { Sheet = name, State = state, Row = rowNum, Hidden = hidden, Serial = serial, SerialStrike = strike, Values = new string[wanted.Length] };
          for (int i = 0; i < wanted.Length; i++) {
            m.Values[i] = "";
            foreach (var c in cells) if (c.Col == wantedCols[i]) { m.Values[i] = Text(c); break; }
          }
          res.Matches.Add(m);
        }
      }
    }
  }
}
'@

$hash = [BitConverter]::ToString((New-Object Security.Cryptography.SHA1Managed).ComputeHash([Text.Encoding]::UTF8.GetBytes($cs))).Replace('-', '').Substring(0, 12)
if (-not ('SupportRma.SerialScanner' -as [type])) {
  $dllDir = if ($StateDir) { $StateDir } else { Join-Path $env:TEMP 'support-rma' }
  $dll = Join-Path $dllDir "SerialScanner-$hash.dll"
  if (-not (Test-Path -LiteralPath $dll)) {
    New-Item -ItemType Directory -Force -Path $dllDir | Out-Null
    Get-ChildItem -LiteralPath $dllDir -Filter 'SerialScanner-*.dll' -ErrorAction SilentlyContinue | Remove-Item -ErrorAction SilentlyContinue
    Add-Type -TypeDefinition $cs -ReferencedAssemblies System.Xml, System.IO.Compression, System.IO.Compression.FileSystem -OutputAssembly $dll -OutputType Library
  }
  if (-not ('SupportRma.SerialScanner' -as [type])) { Add-Type -LiteralPath $dll }
}

# --- run -------------------------------------------------------------------------

$t0 = Get-Date
$src = Resolve-Inventory -Path $Path -Name $Name -StateDir $StateDir -Url $Url -MaxAgeMinutes $MaxAgeMinutes -Refresh:$Refresh
$t1 = Get-Date
try {
  $res = [SupportRma.SerialScanner]::Run($src.Path, [string[]]$serials, [string[]]@($FieldMap.Values), $RequiredCount)
} catch {
  Fail 3 "cannot read $($src.Path): $($_.Exception.InnerException.Message)$($_.Exception.Message)"
}
$t2 = Get-Date

if (-not $res.SheetsSearched.Count) {
  Fail 4 ("no sheet has all of the columns: " + (@($FieldMap.Values)[0..($RequiredCount - 1)] -join ', ') + ". Skipped: " + ($res.Skipped -join '; '))
}

$isYes = { param($v) $v -match '^\s*y(es)?\s*$' }
$keys = @($FieldMap.Keys)
$results = foreach ($m in $res.Matches) {
  $f = @{}; for ($i = 0; $i -lt $keys.Count; $i++) { $f[$keys[$i]] = $m.Values[$i] }
  [ordered]@{
    serial                 = $m.Serial
    sheet                  = $m.Sheet
    row                    = $m.Row
    customer               = $f.customer
    support_tier           = $(if (& $isYes $f.premium_onsite_support) { 'Platinum+' } else { 'Platinum' })
    premium_onsite_support = $f.premium_onsite_support
    mtce_eligible          = (& $isYes $f.mtce_can_provide) -and (& $isYes $f.mtce_contract_active)
    mtce_can_provide       = $f.mtce_can_provide
    mtce_contract_active   = $f.mtce_contract_active
    chassis                = $f.chassis
    dest_city              = $f.dest_city
    address                = $f.address
    hw_spare_provided_by   = $f.hw_spare_provided_by
    crossed_off            = $m.SerialStrike -eq 'full'
    serial_strike          = $m.SerialStrike
    hidden_row             = $m.Hidden
  }
}
$foundSet = @{}; foreach ($m in $res.Matches) { $foundSet[$m.Serial.ToUpperInvariant()] = $true }

$warnings = @()
foreach ($s in $res.ConditionalFormatting) { $warnings += "sheet '$s' uses conditional formatting; strikethrough applied that way is not detected" }

[ordered]@{
  source     = $src.Path
  origin     = $src.Origin           # path | synced | sharepoint
  as_of      = $src.AsOf.ToString('o')
  fetched    = $src.Fetched          # true = downloaded from SharePoint on this run
  seconds    = [ordered]@{ locate = [math]::Round(($t1 - $t0).TotalSeconds, 1); scan = [math]::Round(($t2 - $t1).TotalSeconds, 1) }
  searched   = @($res.SheetsSearched)
  results    = @($results)
  not_found  = @($serials | Where-Object { -not $foundSet[$_.ToUpperInvariant()] })
  warnings   = $warnings
} | ConvertTo-Json -Depth 5
