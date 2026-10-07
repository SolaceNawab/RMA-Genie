<#
  Shared by read_inventory.ps1 and lookup_serial.ps1 (dot-source it): decides
  which copy of the Shipment Inventory Record to read.

  Order: an explicit -Path, then a OneDrive/SharePoint-synced local file (live),
  then a copy fetched from SharePoint through the user's signed-in Excel.
#>

$DefaultInventoryUrl = 'https://solacesystems.sharepoint.com/teams/Operations/Shared%20Documents/Ship%20Spreadsheet/Shipment%20Inventory%20Record.xlsx'

function Fail([int]$code, [string]$msg) {
  [Console]::Error.WriteLine("$([IO.Path]::GetFileNameWithoutExtension($MyInvocation.ScriptName)): $msg")
  exit $code
}

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

function Find-Inventory([string]$Name) {
  foreach ($root in Get-SyncRoots) {
    # Skip Office lock files and copies ("Copy of X", "X - Copy"); prefer the exact name, then the newest.
    $exact = $Name -replace '\*', ''
    $hit = Get-ChildItem -LiteralPath $root -Recurse -File -Filter $Name -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -notlike '~$*' -and $_.BaseName -notmatch '(?i)\bcopy\b' } |
      Sort-Object @{ Expression = { $_.Name -eq $exact }; Descending = $true }, @{ Expression = 'LastWriteTime'; Descending = $true } |
      Select-Object -First 1
    if ($hit) { return $hit.FullName }
  }
  $null
}

# Opens the workbook read-only in a private, hidden Excel instance (Excel handles the
# Solace sign-in), saves a copy to $Dest, and closes only the Excel process it started.
function Save-SharePointCopy([string]$Url, [string]$Dest) {
  if (-not ('InvSrc.Win32' -as [type])) {
    Add-Type -Namespace InvSrc -Name Win32 -MemberDefinition '[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint pid);'
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
  $tmp = [IO.Path]::Combine((Split-Path $Dest), "partial-$PID.xlsx")
  $xl = $null; $wb = $null; $xlPid = [uint32]0
  try {
    $xl = New-Object -ComObject Excel.Application
    [void][InvSrc.Win32]::GetWindowThreadProcessId([IntPtr]$xl.Hwnd, [ref]$xlPid)
    $xl.Visible = $false; $xl.DisplayAlerts = $false; $xl.AskToUpdateLinks = $false
    $xl.ScreenUpdating = $false; $xl.EnableEvents = $false
    $xl.AutomationSecurity = 3   # msoAutomationSecurityForceDisable: never run macros
    $wb = $xl.Workbooks.Open($Url, 0, $true)
    $wb.SaveCopyAs($tmp)
    $wb.Close($false)
    Move-Item -LiteralPath $tmp -Destination $Dest -Force
  } catch {
    Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
    throw "could not fetch $Url through Excel: $($_.Exception.Message)"
  } finally {
    if ($xl) { try { $xl.Quit() } catch {} }
    foreach ($o in $wb, $xl) { if ($o) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($o) } }
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    # Quit() normally ends the process; if it lingers, stop it -- only our own, windowless instance.
    if ($xlPid) {
      Start-Sleep -Milliseconds 500
      Get-Process -Id $xlPid -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -eq 'EXCEL' -and -not $_.MainWindowTitle } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    }
  }
}

# Returns @{ Path; Origin = 'path' | 'synced' | 'sharepoint'; AsOf = [datetime]; Fetched = [bool] }
function Resolve-Inventory {
  param([string]$Path, [string]$Name, [string]$StateDir, [string]$Url,
        [double]$MaxAgeMinutes = 10, [switch]$Refresh)

  if ($Path) {
    if (-not (Test-Path -LiteralPath $Path)) { Fail 3 "file not found: $Path" }
    $p = (Resolve-Path -LiteralPath $Path).ProviderPath
    return @{ Path = $p; Origin = 'path'; AsOf = (Get-Item -LiteralPath $p).LastWriteTime; Fetched = $false }
  }

  # Synced copy: OneDrive keeps it current, so it is read in place.
  $cacheFile = if ($StateDir) { Join-Path $StateDir 'inventory_path.txt' }
  $synced = $null
  if ($cacheFile -and (Test-Path -LiteralPath $cacheFile)) {
    $cached = (Get-Content -LiteralPath $cacheFile -Raw).Trim()
    # Older versions also cached explicit -Path values (e.g. a temp copy); only trust sync folders.
    $roots = @(Get-SyncRoots | ForEach-Object { (Get-Item -LiteralPath $_).FullName.TrimEnd('\') + '\' })
    if ($cached -and (Test-Path -LiteralPath $cached)) {
      $full = (Get-Item -LiteralPath $cached).FullName
      if ($roots | Where-Object { $full.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }) { $synced = $full }
    }
  }
  if (-not $synced) { $synced = Find-Inventory $Name }
  if ($synced) {
    if ($cacheFile) {
      New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
      Set-Content -LiteralPath $cacheFile -Value $synced -Encoding UTF8
    }
    return @{ Path = $synced; Origin = 'synced'; AsOf = (Get-Item -LiteralPath $synced).LastWriteTime; Fetched = $false }
  }

  # No synced copy: fetch from SharePoint, reusing a recent fetch.
  if (-not $Url) { $Url = $DefaultInventoryUrl }
  $dir = if ($StateDir) { $StateDir } else { Join-Path $env:TEMP 'support-rma' }
  $copy = Join-Path $dir 'sharepoint\Shipment Inventory Record.xlsx'
  $item = Get-Item -LiteralPath $copy -ErrorAction SilentlyContinue
  $fresh = $item -and -not $Refresh -and ((Get-Date) - $item.LastWriteTime).TotalMinutes -lt $MaxAgeMinutes
  if (-not $fresh) {
    try { Save-SharePointCopy $Url $copy }
    catch { Fail 3 ("$($_.Exception.Message). No OneDrive-synced copy was found either (" + ((Get-SyncRoots) -join '; ') + ').') }
  }
  @{ Path = $copy; Origin = 'sharepoint'; AsOf = (Get-Item -LiteralPath $copy).LastWriteTime; Fetched = -not $fresh }
}
