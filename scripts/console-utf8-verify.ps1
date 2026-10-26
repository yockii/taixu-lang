# console-utf8-verify.ps1 -- RT-10 real-console verification anchor (D80; feedback #2).
# ASCII-only by design; Chinese verdict chars are built from [char] code points.
# .NET Framework / PowerShell 5.1 compatible (no ProcessStartInfo.ArgumentList).
#
# Dual-form x dual-route verification of print console output:
#   Form A (real console direct-run -- program-side fix evidence): a child
#           PowerShell (own console) sets chcp 936 (GBK, zh-CN default), runs
#           the program IN that console, then reads its own CONOUT$ screen
#           buffer; assert TAI-XU present and the GBK-misread char absent
#           (WriteConsoleW is code-page-agnostic).
#   Form B (pipe -- machine contract evidence): run the program with redirected
#           stdout, take the RAW bytes, strict-decode as UTF-8, assert TAI-XU
#           (pipe bytes are always UTF-8 -- D80 sec 0-3 documents the consumer
#           side for legacy PowerShell sessions).
#   Routes: CLI `taixu run` + `taixu aot` product.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\console-utf8-verify.ps1 [-Taixu <path>]
# Exit: 0 = all pass, 1 = failure(s). Summary lines go to stdout for worklog quotes.

param(
    [string]$Taixu = "",
    [string]$WorkDir = "",
    [switch]$Child,
    [string]$ChildOut = "",
    [string]$ChildProg = "",
    [string]$ChildArgs = ""
)

# PowerShell variable names are case-insensitive: never name a variable that
# collides with $Taixu here (use $taiXuChars).
$taiXuChars = [string][char]0x592A + [string][char]0x865A   # TAI-XU
$zhanChar = [string][char]0x6FB6                            # first GBK-misread char

function Note($msg) { [Console]::Out.WriteLine($msg) }

# ---------------------------------------------------------------------------
# Child mode: runs inside its OWN console window (allocated by the parent's
# Start-Process). Sets CP 936, runs the program, reads own screen buffer.
# ---------------------------------------------------------------------------
if ($Child) {
    $ErrorActionPreference = "Continue"
    $errs = New-Object System.Text.StringBuilder
    cmd /c "chcp 936 >nul"
    if ($ChildArgs.Length -gt 0) {
        cmd /c "`"$ChildProg`" $ChildArgs"
    } else {
        cmd /c "`"$ChildProg`""
    }
    try { Write-Host "" } catch { [void]$errs.AppendLine("marker: $_") }
    # Read own console screen buffer (CONOUT$ of the current console).
    Add-Type -Namespace TaixuVerify -Name Con -MemberDefinition @"
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr tmpl);
[DllImport("kernel32.dll", SetLastError = true)] public static extern bool CloseHandle(IntPtr h);
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern bool ReadConsoleOutputCharacterW(IntPtr h, ushort[] ch, uint n, COORD coord, out uint read);
[DllImport("kernel32.dll", SetLastError = true)] public static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CONSOLE_SCREEN_BUFFER_INFO info);
[StructLayout(LayoutKind.Sequential, Pack = 1)] public struct COORD { public ushort X; public ushort Y; }
[StructLayout(LayoutKind.Sequential, Pack = 1)] public struct SMALL_RECT { public ushort L; public ushort T; public ushort R; public ushort B; }
[StructLayout(LayoutKind.Sequential, Pack = 1)] public struct CONSOLE_SCREEN_BUFFER_INFO { public COORD size; public COORD cursor; public uint attr; public SMALL_RECT win; public COORD max; }
"@
    $h = [TaixuVerify.Con]::CreateFileW("CONOUT$", [uint32]3221225472, 0x3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)
    if ($h -eq [IntPtr](-1)) { [System.IO.File]::WriteAllText($ChildOut, "ERR cannot open CONOUT"); exit 2 }
    $info = New-Object TaixuVerify.Con+CONSOLE_SCREEN_BUFFER_INFO
    if (-not [TaixuVerify.Con]::GetConsoleScreenBufferInfo($h, [ref]$info)) {
        [TaixuVerify.Con]::CloseHandle($h) | Out-Null
        [System.IO.File]::WriteAllText($ChildOut, "ERR cannot get buffer info"); exit 2
    }
    $sb = New-Object System.Text.StringBuilder
    $yTop = 0
    $yBot = $info.win.B
    if ($info.cursor.Y -gt $yBot) { $yBot = $info.cursor.Y }
    $width = [uint32]($info.win.R - $info.win.L + 1)
    $rowbuf = New-Object 'uint16[]' ([int]$width)
    $read = [uint32]0
    for ($y = $yTop; $y -le $yBot; $y++) {
        $coord = New-Object TaixuVerify.Con+COORD
        $coord.X = [uint16]$info.win.L; $coord.Y = [uint16]$y
        if ([TaixuVerify.Con]::ReadConsoleOutputCharacterW($h, $rowbuf, $width, $coord, [ref]$read)) {
            for ($i = 0; $i -lt $read; $i++) { [void]$sb.Append([char]$rowbuf[$i]) }
        }
        [void]$sb.Append("`n")
    }
    [TaixuVerify.Con]::CloseHandle($h) | Out-Null
    [void]$errs.AppendLine("rect T=" + $info.win.T + " B=" + $info.win.B + " cursorY=" + $info.cursor.Y)
    [System.IO.File]::WriteAllText($ChildOut, $sb.ToString() + "`n---ERRS---`n" + $errs.ToString(), [System.Text.UTF8Encoding]::new($false))
    exit 0
}

# ---------------------------------------------------------------------------
# Parent mode
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
$script:fail = 0

if ($Taixu -eq "") { $Taixu = $env:TAIXU_NATIVE }
if ([string]::IsNullOrEmpty($Taixu)) { $Taixu = Join-Path $PSScriptRoot "..\work\native\bin\taixu.exe" }
if ($WorkDir -eq "") { $WorkDir = Join-Path $env:TEMP ("taixu-console-verify-" + [guid]::NewGuid().ToString("N").Substring(0, 8)) }

if (-not (Test-Path $Taixu)) { Note "FAIL: taixu not found: $Taixu"; exit 1 }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# -- program under test (fixed Chinese string; its UTF-8 bytes read as GBK
#    necessarily contain U+6FB6) --
$helloTx = Join-Path $WorkDir "hello-cjk.tx"
$src = "export function main(args: []string): int {`n" +
       "    args;`n" +
       ('    print("Hello, ' + $taiXuChars + '!");') + "`n" +
       "    return 0;`n" +
       "}`n"
[System.IO.File]::WriteAllText($helloTx, $src, [System.Text.UTF8Encoding]::new($false))

$self = $MyInvocation.MyCommand.Path
$childPs = (Get-Command powershell).Source

# Form A: real console direct-run (chcp 936 + program in child's own console).
function Test-ConsoleDirect([string]$label, [string]$exePath, [string]$argLine) {
    $tag = [guid]::NewGuid().ToString("N").Substring(0, 8)
    $screenFile = Join-Path $script:WorkDir ("screen-" + $tag + ".txt")
    $inner = "-NoProfile -ExecutionPolicy Bypass -File `"$self`" -Child -ChildOut `"$screenFile`" -ChildProg `"$exePath`""
    if ($argLine.Length -gt 0) { $inner += " -ChildArgs `"$argLine`"" }
    $p = Start-Process -FilePath $childPs -ArgumentList $inner -WindowStyle Minimized -PassThru
    if (-not $p.WaitForExit(45000)) {
        try { cmd /c "taskkill /T /F /PID $($p.Id)" 2>$null | Out-Null } catch {}
        Note "FAIL [$label]: child timed out"; return $false
    }
    if (-not (Test-Path $screenFile)) { Note "FAIL [$label]: no screen capture"; return $false }
    $screen = [System.IO.File]::ReadAllText($screenFile)
    $okT = $screen.Contains($taiXuChars)
    $okZ = -not $screen.Contains($zhanChar)
    $dump = $screenFile + ".dump.txt"
    [System.IO.File]::WriteAllText($dump, $screen, [System.Text.UTF8Encoding]::new($false))
    if ($okT -and $okZ) { Note "PASS [$label]: chcp 936 console screen reads TAI-XU (no mojibake char)"; return $true }
    Note "FAIL [$label]: screen assert (taiXu=$okT noZhan=$okZ)"; return $false
}

# Form B: redirected pipe; RAW bytes strict-decoded as UTF-8 (machine contract).
function Test-PipeUtf8([string]$label, [string]$exePath, [string[]]$argArr) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exePath
    if ($argArr.Count -gt 0) {
        $quoted = $argArr | ForEach-Object { "`"$_`"" }
        $psi.Arguments = $quoted -join " "
    }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.WaitForExit(60000) | Out-Null
    $ms = New-Object System.IO.MemoryStream
    $p.StandardOutput.BaseStream.CopyTo($ms)
    $bytes = $ms.ToArray()
    $text = [System.Text.UTF8Encoding]::new($false, $true).GetString($bytes)  # throwOnInvalid
    if ($text.Contains($taiXuChars)) { Note "PASS [$label]: pipe raw bytes strict-decode as UTF-8 TAI-XU"; return $true }
    $head = [Math]::Min(40, $bytes.Length)
    Note "FAIL [$label]: pipe assert; bytes=$([System.BitConverter]::ToString($bytes, 0, $head))"; return $false
}

$cp = (Get-Item $Taixu).FullName
Note "== RT-10 console-utf8-verify (workdir $WorkDir) =="

# -- CLI route --
if (-not (Test-ConsoleDirect "FormA CLI run" $cp "run `"$helloTx`"")) { $script:fail = 1 }
if (-not (Test-PipeUtf8 "FormB CLI run" $cp @("run", $helloTx))) { $script:fail = 1 }

# -- AOT product route --
$aotDir = Join-Path $WorkDir "aot-out"
& $cp aot $helloTx --out $aotDir 2>&1 | Out-Null
$prod = Join-Path $aotDir "hello-cjk.exe"
if (Test-Path $prod) {
    if (-not (Test-ConsoleDirect "FormA AOT product" $prod "")) { $script:fail = 1 }
    if (-not (Test-PipeUtf8 "FormB AOT product" $prod @())) { $script:fail = 1 }
} else {
    Note "FAIL: AOT build failed ($aotDir)"; $script:fail = 1
}

if ($script:fail -eq 0) { Note "== RESULT: all pass (2 forms x 2 routes) ==" } else { Note "== RESULT: failures present ==" }
exit $script:fail
