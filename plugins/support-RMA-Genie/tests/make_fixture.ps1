# Builds tests/fixture_inventory.xlsx with fake data covering the strikethrough
# cases read_inventory.ps1 must handle. Requires Excel (COM). Run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests\make_fixture.ps1
$ErrorActionPreference = 'Stop'
$out = Join-Path $PSScriptRoot 'fixture_inventory.xlsx'
if (Test-Path $out) { Remove-Item $out }

$xl = New-Object -ComObject Excel.Application
$xl.DisplayAlerts = $false
try {
  $wb = $xl.Workbooks.Add()
  while ($wb.Worksheets.Count -lt 2) { [void]$wb.Worksheets.Add([Type]::Missing, $wb.Worksheets.Item($wb.Worksheets.Count)) }

  # Sheet 1: title rows above the header, mixed strike styles
  $ws = $wb.Worksheets.Item(1); $ws.Name = 'Ottawa'
  $ws.Cells.Item(1, 1).Value2 = 'Shipment Inventory - FAKE TEST DATA'
  $data = @(
    @('Model', 'Serial Number', 'Received', 'Notes'),
    @('3560', 'FAKE-0001', '2026-01-15', 'available'),
    @('3560', 'FAKE-0002', '2026-01-15', 'serial cell struck'),
    @('3530', 'FAKE-0003', '2026-02-01', 'whole row struck'),
    @('3530', 'FAKE-0004', '2026-02-01', 'only part of serial struck'),
    @('3560', 'FAKE-0005', '2026-03-10', 'notes struck only'),
    @('3560', '1234567', '2026-03-11', 'numeric serial')
  )
  for ($r = 0; $r -lt $data.Count; $r++) {
    for ($c = 0; $c -lt 4; $c++) {
      $cell = $ws.Cells.Item($r + 3, $c + 1)
      # Formula = typed-in text, so Excel parses dates and numbers itself
      $cell.Formula = $data[$r][$c]
      if ($r -gt 0 -and $c -eq 2) { $cell.NumberFormat = 'yyyy-mm-dd' }
    }
  }
  $ws.Cells.Item(5, 2).Font.Strikethrough = $true                 # FAKE-0002
  $ws.Range('A6:D6').Font.Strikethrough = $true                   # FAKE-0003
  $ws.Cells.Item(7, 2).Characters(6, 4).Font.Strikethrough = $true # FAKE-0004: "0004" only
  $ws.Cells.Item(8, 4).Font.Strikethrough = $true                 # FAKE-0005 notes

  # Sheet 2: header in row 1, short "S/N" header
  $ws2 = $wb.Worksheets.Item(2); $ws2.Name = 'Toronto'
  $ws2.Cells.Item(1, 1).Value2 = 'S/N'; $ws2.Cells.Item(1, 2).Value2 = 'Location'
  $ws2.Cells.Item(2, 1).Value2 = 'FAKE-1001'; $ws2.Cells.Item(2, 2).Value2 = 'Shelf A'
  $ws2.Cells.Item(3, 1).Value2 = 'FAKE-1002'; $ws2.Cells.Item(3, 2).Value2 = 'Shelf B'
  $ws2.Cells.Item(3, 1).Font.Strikethrough = $true

  $wb.SaveAs($out, 51)  # 51 = xlOpenXMLWorkbook
  $wb.Close($false)
} finally {
  $xl.Quit()
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($xl)
}
"wrote $out"
