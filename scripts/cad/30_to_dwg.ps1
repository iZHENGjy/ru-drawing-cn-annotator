# Convert translated DXF files to DWG (AutoCAD 2010 and 2004 formats) with ODA File Converter,
# then read every DWG back (DWG -> DXF) and count the Chinese texts to prove the round trip.
#
# usage:
#   pwsh scripts/cad/30_to_dwg.ps1 -Oda "C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe" `
#        -Python .venv\Scripts\python.exe -OutDir output output\plan_CN.dxf output\plan_CN_overlay.dxf
#
# ODA File Converter is free (registration) but not open source: download it from
# https://www.opendesign.com/guestfiles/oda_file_converter - it is not shipped with this repo.
param(
    [Parameter(Mandatory = $true)][string]$Oda,
    [string]$Python = "python",
    [string]$OutDir = "output",
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Dxf
)
$ErrorActionPreference = 'Stop'
$tmp = Join-Path $env:TEMP ('rudraw_oda_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force "$tmp\in" | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null
Copy-Item $Dxf "$tmp\in"

foreach ($ver in 'ACAD2010', 'ACAD2004') {
    $o = "$tmp\$ver"; New-Item -ItemType Directory -Force $o | Out-Null
    Start-Process $Oda -ArgumentList "`"$tmp\in`" `"$o`" $ver DWG 0 1 *.DXF" -Wait | Out-Null
    $tag = $ver.Substring(4)
    foreach ($f in Get-ChildItem $o -Filter *.dwg) {
        Copy-Item $f.FullName (Join-Path $OutDir ($f.BaseName + "_$tag.dwg")) -Force
    }
    Get-ChildItem $o -Filter *.err | ForEach-Object { "ERR $($_.Name): " + (Get-Content $_.FullName -Raw) }
    # round trip check
    $back = "$tmp\back_$ver"; New-Item -ItemType Directory -Force $back | Out-Null
    Start-Process $Oda -ArgumentList "`"$o`" `"$back`" ACAD2018 DXF 0 1 *.DWG" -Wait | Out-Null
    foreach ($f in Get-ChildItem $back -Filter *.dxf) {
        & $Python -I -W ignore -c "import ezdxf,sys;d=ezdxf.readfile(sys.argv[1]);m=d.modelspace();print(sys.argv[2], 'version', d.dxfversion, 'CN texts', len(m.query('TEXT[layer==\""CN_TRANSLATION\""]')), 'all entities', len(m))" $f.FullName "$($f.Name) <- $ver"
    }
}
Remove-Item $tmp -Recurse -Force
