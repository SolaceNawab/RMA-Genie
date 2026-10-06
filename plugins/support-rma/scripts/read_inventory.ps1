<#
.SYNOPSIS
  Read the shipment inventory spreadsheet and report which serial numbers are
  crossed off (strikethrough) and which are still available.

.DESCRIPTION
  Parses the .xlsx directly (it is a zip of XML parts), so Excel is not needed
  and the file is never locked -- safe to run while OneDrive is syncing it or
  someone has it open. Strikethrough is resolved from the cell's font style and
  from rich-text runs inside the cell.

  This is the shared core for the support-rma plugin: /inventory uses it to list
  available serials, and later skills (e.g. RMA generation) consume the same JSON.

.OUTPUTS
  JSON (default) or a plain table. Exit codes: 0 ok, 3 file not found / unreadable,
  4 no serial-number column found.
#>
[CmdletBinding()]
param(
  # Path to the .xlsx. If omitted, the cached path is tried, then OneDrive sync roots are searched.
  [string]$Path,
  # File name pattern used for auto-discovery.
  [string]$Name = 'Shipment Inventory Record*.xlsx',
  # Header text (e.g. "Serial Number") or column letter (e.g. "C"). Auto-detected if omitted.
  [string]$SerialColumn,
  # Only read this worksheet. All visible and hidden sheets are read if omitted.
  [string]$Sheet,
  # Drop crossed-off rows from the output.
  [switch]$AvailableOnly,
  [ValidateSet('json', 'table')]
  [string]$Format = 'json',
  # Directory to cache the discovered spreadsheet path in (pass $CLAUDE_PLUGIN_DATA).
  [string]$StateDir
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.IO.Compression

$RelNs = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

function Fail([int]$code, [string]$msg) {
  [Console]::Error.WriteLine("read_inventory: $msg")
  exit $code
}

# --- locate the spreadsheet -------------------------------------------------

function Get-SyncRoots {
  $roots = @()
  $roots += Get-ChildItem 'HKCU:\Software\SyncEngines\Providers\OneDrive' -ErrorAction SilentlyContinue |
    ForEach-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).MountPoint }
  # SharePoint libraries synced with "Sync" land in %USERPROFILE%\<Org Name>\<Site> - <Library>
  $roots += Get-ChildItem $env:USERPROFILE -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'OneDrive*' -or $_.Name -like 'Solace Corporation*' -or $_.Name -like 'Solace Systems*' } |
    ForEach-Object { $_.FullName }
  $roots | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Sort-Object -Unique
}

function Find-Inventory {
  foreach ($root in Get-SyncRoots) {
    $hit = Get-ChildItem -LiteralPath $root -Recurse -File -Filter $Name -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -notlike '~$*' } |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($hit) { return $hit.FullName }
  }
  $null
}

$cacheFile = if ($StateDir) { Join-Path $StateDir 'inventory_path.txt' }

if (-not $Path -and $cacheFile -and (Test-Path -LiteralPath $cacheFile)) {
  $cached = (Get-Content -LiteralPath $cacheFile -Raw).Trim()
  if ($cached -and (Test-Path -LiteralPath $cached)) { $Path = $cached }
}
if (-not $Path) {
  $Path = Find-Inventory
  if (-not $Path) {
    Fail 3 ("no file matching '$Name' under any OneDrive/SharePoint sync folder (" +
      ((Get-SyncRoots) -join '; ') + "). Sync the SharePoint library or pass -Path.")
  }
}
if (-not (Test-Path -LiteralPath $Path)) { Fail 3 "file not found: $Path" }
$Path = (Resolve-Path -LiteralPath $Path).ProviderPath
if ($cacheFile) {
  New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
  Set-Content -LiteralPath $cacheFile -Value $Path -Encoding UTF8
}

# --- xlsx helpers -------------------------------------------------------------

function Read-Part($zip, [string]$name) {
  $entry = $zip.GetEntry($name)
  if (-not $entry) { return $null }
  $reader = New-Object IO.StreamReader($entry.Open())
  try { $text = $reader.ReadToEnd() } finally { $reader.Dispose() }
  # Drop the default namespace so plain XPath works; prefixed namespaces stay intact.
  $doc = New-Object Xml.XmlDocument
  $doc.LoadXml(($text -replace '\sxmlns="[^"]*"', ''))
  , $doc   # leading comma: XmlDocument is enumerable and would otherwise be unrolled
}

# $null = no strike element, $true/$false = explicit
function Get-StrikeFlag($props) {
  if (-not $props) { return $null }
  $s = $props.SelectSingleNode('strike')
  if (-not $s) { return $null }
  $v = $s.GetAttribute('val')
  -not ($v -eq '0' -or $v -eq 'false')
}

# Rich/inline string -> @{ Text; Runs = @(@{Text; Strike}) } (Runs empty for plain strings)
function Read-StringItem($node) {
  $runs = @()
  foreach ($r in @($node.SelectNodes('r'))) {
    $runs += @{ Text = (@($r.SelectNodes('t')) | ForEach-Object { $_.InnerText }) -join ''
                Strike = Get-StrikeFlag $r.SelectSingleNode('rPr') }
  }
  $text = if ($runs.Count) { ($runs | ForEach-Object { $_.Text }) -join '' }
          else { (@($node.SelectNodes('t')) | ForEach-Object { $_.InnerText }) -join '' }
  @{ Text = $text; Runs = $runs }
}

function ConvertTo-ColumnIndex([string]$letters) {
  $n = 0
  foreach ($ch in $letters.ToUpper().ToCharArray()) { $n = $n * 26 + ([int]$ch - 64) }
  $n
}

function ConvertTo-ColumnLetter([int]$n) {
  $s = ''
  while ($n -gt 0) { $m = ($n - 1) % 26; $s = [char](65 + $m) + $s; $n = [math]::Floor(($n - 1) / 26) }
  $s
}

function Test-DateFormat([int]$id, [string]$code) {
  if (($id -ge 14 -and $id -le 22) -or ($id -ge 45 -and $id -le 47)) { return $true }
  if (-not $code) { return $false }
  $bare = $code -replace '"[^"]*"', '' -replace '\[[^\]]*\]', '' -replace '\\.', ''
  $bare -match '[dmyhs]' -and $bare -notmatch '^[#0.,%E+\-\s]*$'
}

# --- read workbook -------------------------------------------------------------

try {
  $stream = [IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite, Delete')
} catch {
  Fail 3 "cannot open ${Path}: $($_.Exception.Message)"
}
$zip = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Read)

try {
  $workbook = Read-Part $zip 'xl/workbook.xml'
  if (-not $workbook) { Fail 3 "$Path is not an .xlsx workbook" }
  $date1904 = $workbook.SelectSingleNode('workbook/workbookPr[@date1904="1" or @date1904="true"]') -ne $null

  # shared strings
  $shared = New-Object Collections.Generic.List[object]
  $sst = Read-Part $zip 'xl/sharedStrings.xml'
  if ($sst) { foreach ($si in @($sst.SelectNodes('sst/si'))) { $shared.Add((Read-StringItem $si)) } }

  # styles: xf index -> font strike, date format
  $xfStrike = @(); $xfDate = @()
  $styles = Read-Part $zip 'xl/styles.xml'
  if ($styles) {
    $fonts = @($styles.SelectNodes('styleSheet/fonts/font'))
    $numFmts = @{}
    foreach ($f in @($styles.SelectNodes('styleSheet/numFmts/numFmt'))) { $numFmts[[int]$f.numFmtId] = $f.formatCode }
    foreach ($xf in @($styles.SelectNodes('styleSheet/cellXfs/xf'))) {
      $fontId = [int]("0" + $xf.GetAttribute('fontId'))
      $xfStrike += [bool](($fontId -lt $fonts.Count) -and (Get-StrikeFlag $fonts[$fontId]))
      $fmtId = [int]("0" + $xf.GetAttribute('numFmtId'))
      $xfDate += [bool](Test-DateFormat $fmtId $numFmts[$fmtId])
    }
  }

  # sheet name -> part path
  $rels = @{}
  $wbRels = Read-Part $zip 'xl/_rels/workbook.xml.rels'
  foreach ($rel in @($wbRels.SelectNodes('Relationships/Relationship'))) {
    $target = $rel.Target -replace '^/', ''
    if ($target -notlike 'xl/*') { $target = "xl/$target" }
    $rels[$rel.Id] = $target
  }

  $warnings = @()
  $rows = New-Object Collections.Generic.List[object]
  $sheetsOut = @()

  foreach ($sh in @($workbook.SelectNodes('workbook/sheets/sheet'))) {
    $sheetName = $sh.GetAttribute('name')
    if ($Sheet -and $sheetName -ne $Sheet) { continue }
    $part = Read-Part $zip $rels[$sh.GetAttribute('id', $RelNs)]
    if (-not $part) { continue }
    $state = if ($sh.GetAttribute('state')) { $sh.GetAttribute('state') } else { 'visible' }

    if ($part.SelectSingleNode('worksheet/conditionalFormatting')) {
      $warnings += "sheet '$sheetName' uses conditional formatting; strikethrough applied that way is not detected"
    }

    # parse every row into: @{ Row; Hidden; Cells = @{ colIndex = @{Text; Strike} } }
    $parsed = New-Object Collections.Generic.List[object]
    foreach ($rowNode in @($part.SelectNodes('worksheet/sheetData/row'))) {
      $cells = @{}
      $nextCol = 1
      foreach ($c in @($rowNode.SelectNodes('c'))) {
        $ref = $c.GetAttribute('r')
        $col = if ($ref -match '^([A-Za-z]+)') { ConvertTo-ColumnIndex $Matches[1] } else { $nextCol }
        $nextCol = $col + 1
        $styleIdx = [int]("0" + $c.GetAttribute('s'))
        $fontStrike = ($styleIdx -lt $xfStrike.Count) -and $xfStrike[$styleIdx]
        $vNode = $c.SelectSingleNode('v')
        $raw = if ($vNode) { $vNode.InnerText } else { $null }
        $item = $null
        switch ($c.GetAttribute('t')) {
          's'         { if ($raw -ne $null -and [int]$raw -lt $shared.Count) { $item = $shared[[int]$raw] } }
          'inlineStr' { $isNode = $c.SelectSingleNode('is'); if ($isNode) { $item = Read-StringItem $isNode } }
          'b'         { $item = @{ Text = $(if ($raw -eq '1') { 'TRUE' } else { 'FALSE' }); Runs = @() } }
          default {
            $text = $raw
            if ($raw -ne $null -and ($styleIdx -lt $xfDate.Count) -and $xfDate[$styleIdx] -and $c.GetAttribute('t') -ne 'str') {
              $num = 0.0
              if ([double]::TryParse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$num)) {
                if ($date1904) { $num += 1462 }
                $dt = [DateTime]::FromOADate($num)
                $text = if ($dt.TimeOfDay.Ticks) { $dt.ToString('yyyy-MM-dd HH:mm') } else { $dt.ToString('yyyy-MM-dd') }
              }
            }
            $item = @{ Text = $text; Runs = @() }
          }
        }
        if (-not $item -or [string]::IsNullOrWhiteSpace($item.Text)) { continue }

        # A run without its own strike element inherits the cell font.
        $strike = 'none'
        $visible = @($item.Runs | Where-Object { $_.Text.Trim() })
        if ($visible.Count) {
          $struck = @($visible | Where-Object { if ($_.Strike -eq $null) { $fontStrike } else { $_.Strike } }).Count
          if ($struck -eq $visible.Count) { $strike = 'full' } elseif ($struck) { $strike = 'partial' }
        } elseif ($fontStrike) { $strike = 'full' }

        $cells[$col] = @{ Text = $item.Text.Trim(); Strike = $strike }
      }
      if ($cells.Count) {
        $hidden = $rowNode.GetAttribute('hidden') -in '1', 'true'
        $parsed.Add(@{ Row = [int]$rowNode.GetAttribute('r'); Hidden = $hidden; Cells = $cells })
      }
    }

    # find header row + serial column
    $headerIdx = -1; $serialCol = 0
    $limit = [math]::Min($parsed.Count, 25)
    for ($i = 0; $i -lt $limit -and $headerIdx -lt 0; $i++) {
      foreach ($col in ($parsed[$i].Cells.Keys | Sort-Object)) {
        $h = $parsed[$i].Cells[$col].Text
        $match = if ($SerialColumn -match '^[A-Za-z]{1,3}$') { $col -eq (ConvertTo-ColumnIndex $SerialColumn) }
                 elseif ($SerialColumn) { $h -eq $SerialColumn }
                 else { $h -match '(?i)serial|^\s*s\s*/?\s*n\s*[#.:]?\s*$|^\s*sn\b' }
        if ($match) { $headerIdx = $i; $serialCol = $col; break }
      }
    }
    if ($headerIdx -lt 0) {
      $warnings += "sheet '$sheetName': no serial-number column found, skipped"
      $sheetsOut += [ordered]@{ name = $sheetName; state = $state; serial_column = $null; rows = 0; available = 0; crossed_off = 0 }
      continue
    }

    $headers = @{}   # plain hashtable: [ordered] treats int keys as positions
    $seen = @{}
    foreach ($col in ($parsed[$headerIdx].Cells.Keys | Sort-Object)) {
      $h = $parsed[$headerIdx].Cells[$col].Text
      if ($seen.ContainsKey($h)) { $h = "$h ($(ConvertTo-ColumnLetter $col))" }
      $seen[$h] = $true
      $headers[$col] = $h
    }
    $serialHeader = $headers[$serialCol]

    $count = 0; $avail = 0
    for ($i = $headerIdx + 1; $i -lt $parsed.Count; $i++) {
      $p = $parsed[$i]
      $serialCell = $p.Cells[$serialCol]
      if (-not $serialCell) { continue }
      # A repeated header row (e.g. a second table on the same sheet) is not data.
      if ($serialCell.Text -eq $serialHeader) { continue }

      $fields = [ordered]@{}
      $struckFields = @()
      foreach ($col in ($p.Cells.Keys | Sort-Object)) {
        $key = if ($headers.ContainsKey($col)) { $headers[$col] } else { ConvertTo-ColumnLetter $col }
        $fields[$key] = $p.Cells[$col].Text
        if ($p.Cells[$col].Strike -ne 'none') { $struckFields += $key }
      }
      $crossed = $serialCell.Strike -eq 'full'
      $count++; if (-not $crossed) { $avail++ }
      if ($AvailableOnly -and $crossed) { continue }
      $rows.Add([pscustomobject][ordered]@{
        sheet         = $sheetName
        row           = $p.Row
        serial        = $serialCell.Text
        crossed_off   = $crossed
        serial_strike = $serialCell.Strike          # none | partial | full
        struck_fields = $struckFields               # other cells in the row with strikethrough
        hidden_row    = $p.Hidden
        fields        = [pscustomobject]$fields
      })
    }
    $sheetsOut += [ordered]@{ name = $sheetName; state = $state; serial_column = $serialHeader
                              rows = $count; available = $avail; crossed_off = $count - $avail }
  }
} finally {
  $zip.Dispose(); $stream.Dispose()
}

if (-not ($sheetsOut | Where-Object { $_.serial_column })) {
  Fail 4 ("no serial-number column found in $Path. " + ($warnings -join '; ') + " -- pass -SerialColumn.")
}

$file = Get-Item -LiteralPath $Path
if ($Format -eq 'table') {
  "Source:   $Path"
  "Modified: $($file.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))"
  foreach ($s in $sheetsOut) { "Sheet '$($s.name)': $($s.available) available, $($s.crossed_off) crossed off" }
  $warnings | ForEach-Object { "Warning:  $_" }
  $rows | Select-Object sheet, row, serial, crossed_off, serial_strike | Format-Table -AutoSize | Out-String -Width 200
} else {
  [ordered]@{
    source        = $Path
    last_modified = $file.LastWriteTime.ToString('o')
    read_at       = (Get-Date).ToString('o')
    sheets        = $sheetsOut
    warnings      = $warnings
    rows          = $rows
  } | ConvertTo-Json -Depth 6
}
