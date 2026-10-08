<#
.SYNOPSIS
  Installs desktop-sense: a Python virtual environment, the `ds` command,
  hooks for Claude Code / Codex CLI / Gemini CLI, and start-at-login.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoAutostart -NoHooks
#>
param(
    [switch]$NoAutostart,
    [switch]$NoHooks
)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Ds = Join-Path $Root 'ds.py'
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$BinDir = Join-Path $Root 'bin'

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

Say "desktop-sense installer" Cyan

# --- 1. Python 3.10+ --------------------------------------------------------
function Test-Python($exe, [string[]]$extra) {
    try {
        $out = & $exe @extra -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'True')
    } catch { return $false }
}
$Py = $null; $PyArgs = @()
if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python 'py' @('-3'))) { $Py = 'py'; $PyArgs = @('-3') }
if (-not $Py) {
    foreach ($c in @('python', 'python3')) {
        if ((Get-Command $c -ErrorAction SilentlyContinue) -and (Test-Python $c @())) { $Py = $c; break }
    }
}
if (-not $Py) {
    Say "Python 3.10 or newer was not found. Install it, then run this script again:" Red
    Say "    winget install Python.Python.3.12" Yellow
    exit 1
}

# --- 2. Virtual environment + dependencies ----------------------------------
if (-not (Test-Path $VenvPy)) {
    Say "Creating virtual environment (.venv)..."
    & $Py @PyArgs -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { Say "Could not create the virtual environment." Red; exit 1 }
}
Say "Installing dependencies..."
& $VenvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Say "pip install failed (see the messages above)." Red; exit 1 }

# --- 3. `ds` on PATH (user scope, keeps %VARIABLES% unexpanded) -------------
$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
$raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$parts = @($raw -split ';' | Where-Object { $_ -ne '' })
if ($parts -notcontains $BinDir) {
    $key.SetValue('Path', (($parts + $BinDir) -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    # tell Explorer / new terminals that PATH changed
    Add-Type -Namespace DSense -Name Env -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
    $r = [UIntPtr]::Zero
    [void][DSense.Env]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r)
    Say "Added $BinDir to your PATH."
}
$key.Close()
$env:Path = "$BinDir;$env:Path"

# --- 3b. Lock the folder down if it lives outside your user profile -----------
# Folders created at a drive root (e.g. D:\dev) are writable by every local account by default:
# another account could then read your screenshots or swap the code that runs in your session.
if (-not $Root.StartsWith($env:USERPROFILE, [System.StringComparison]::OrdinalIgnoreCase)) {
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls $Root /inheritance:r /grant:r "*${me}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
    if ($LASTEXITCODE -eq 0) { Say "Restricted $Root to your account (plus SYSTEM and Administrators)." }
    else { Say "Could not restrict permissions on $Root - consider moving it under $env:USERPROFILE." Yellow }
}

# --- 4. Hooks for AI coding tools -------------------------------------------
if (-not $NoHooks) {
    Say "Connecting AI coding tools..."
    & $VenvPy $Ds setup
}

# --- 5. Start now + at login ------------------------------------------------
if (-not $NoAutostart) { & $VenvPy $Ds autostart on | Out-Null }
& $VenvPy $Ds restart   # restart, not start: an upgrade must load the new privacy rules

Say ""
Say "Done. Open a NEW terminal and try:" Green
Say "    ds status      # daemon status"
Say "    ds now         # what desktop-sense sees right now"
Say "Then just ask your AI assistant: 'look at this error' - no screenshot pasting needed."
Say ""
Say "Privacy: everything stays in $Root\data. Add your own rules in config.json (see README)."
