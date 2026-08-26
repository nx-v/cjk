param(
  # Script filters: omit all → build everything. Any set → only those scripts.
  [switch]$Yi,
  [switch]$Kana,
  [switch]$Hangul,
  [switch]$Cjk,

  [switch]$CjkBase,
  [string]$CjkFaces = "",
  [switch]$CjkH,
  [switch]$KanaBase,
  [switch]$KanaH,
  [switch]$KanaT,
  [switch]$KanaQ,
  [switch]$YiBase,
  [switch]$YiH,
  [switch]$YiT,
  [switch]$YiQ,
  # Unicode spans for CJK rebuild (same as build_cjk --range). Repeatable /
  # comma-ok, e.g. -Range U+2F00-9FFF or -Range 4E00-4FFF,4E
  [string[]]$Range = @(),
  # Parallel workers for yi / kana / cjk builders (`-j` / `--jobs`).
  [Alias('j')]
  [ValidateRange(1, 512)]
  [int]$Jobs = 61
)

$py = "c:/python314/python.exe"
$scripts = "c:/Users/Admin/fonts/Scripts"

$anyScript = $Yi -or $Kana -or $Hangul -or $Cjk
$doYi = (-not $anyScript) -or $Yi
$doKana = (-not $anyScript) -or $Kana
$doHangul = (-not $anyScript) -or $Hangul
$doCjk = (-not $anyScript) -or $Cjk

# No h/t/q flags → builders use their full defaults (CJK base+h; kana/yi all
# segment faces). Any selective switch limits that script to the named faces.
$cjkFaceArgs = @()
if ($CjkBase) {
  $cjkFaceArgs += "--base"
} elseif ($CjkFaces) {
  $cjkFaceArgs += @("--faces", $CjkFaces)
} elseif ($CjkH) {
  $cjkFaceArgs += "--h"
}
foreach ($span in $Range) {
  if ($span) { $cjkFaceArgs += @("--range", $span) }
}

$kanaFaceArgs = @()
if ($KanaBase) {
  $kanaFaceArgs += "--base"
} else {
  if ($KanaH) { $kanaFaceArgs += "--h" }
  if ($KanaT) { $kanaFaceArgs += "--t" }
  if ($KanaQ) { $kanaFaceArgs += "--q" }
}

$yiFaceArgs = @()
if ($YiBase) {
  $yiFaceArgs += "--base"
} else {
  if ($YiH) { $yiFaceArgs += "--h" }
  if ($YiT) { $yiFaceArgs += "--t" }
  if ($YiQ) { $yiFaceArgs += "--q" }
}

$jobArgs = @("-j", "$Jobs")
Write-Host "Jobs: $Jobs"

if ($doCjk) {
  & $py "$scripts/cjk_diacritics_html.py"
  & $py "$scripts/cjk_multigraphs_html.py"
}
if ($doHangul) { & $py "$scripts/hangul_html.py" }
if ($doYi) { & $py "$scripts/yi_html.py" }
if ($doKana) { & $py "$scripts/kana_html.py" }

if ($doHangul) {
  & $py "$scripts/build_hangul.py" --woff2-only
}
if ($doYi) {
  & $py "$scripts/build_yi.py" --woff2-only @jobArgs @yiFaceArgs
}
if ($doKana) {
  & $py "$scripts/build_kana.py" --woff2-only @jobArgs @kanaFaceArgs
}
if ($doCjk) {
  & $py "$scripts/build_cjk.py" --woff2-only --hint-base-only @jobArgs @cjkFaceArgs
  # & $py "$scripts/build_cjk.py" --css-only @cjkFaceArgs
}

# & $py "$scripts/edenia_app.py"
& $py "$scripts/sync_edenian_fonts.py"
& $py "$scripts/update_obsidian_theme_fonts.py" --bake --vault "C:/Users/Admin/Dropbox" --private-only
robocopy "c:/Users/Admin/fonts/Scripts/obsidian-edenia" "C:/Users/Admin/Dropbox/.obsidian/plugins/obsidian-edenia" /e /mir
