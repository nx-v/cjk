param(
  [switch]$CjkBaseOnly,
  [string]$CjkFaces = "",
  [switch]$CjkH,
  [switch]$KanaH,
  [switch]$KanaT,
  [switch]$KanaQ,
  [switch]$YiH,
  [switch]$YiT,
  [switch]$YiQ,
  # Unicode spans for CJK rebuild (same as build_cjk --range). Repeatable /
  # comma-ok, e.g. -Range U+2F00-9FFF or -Range 4E00-4FFF,4E
  [string[]]$Range = @()
)

$py = "c:/python314/python.exe"
$scripts = "c:/Users/Admin/fonts/Scripts"

# No h/t/q flags → builders use their full defaults (CJK base+h; kana/yi all
# segment faces). Any selective switch limits that script to the named faces.
$cjkFaceArgs = @()
if ($CjkBaseOnly) {
  $cjkFaceArgs += "--base-only"
} elseif ($CjkFaces) {
  $cjkFaceArgs += @("--faces", $CjkFaces)
} elseif ($CjkH) {
  $cjkFaceArgs += "--h"
}
foreach ($span in $Range) {
  if ($span) { $cjkFaceArgs += @("--range", $span) }
}

$kanaFaceArgs = @()
if ($KanaH) { $kanaFaceArgs += "--h" }
if ($KanaT) { $kanaFaceArgs += "--t" }
if ($KanaQ) { $kanaFaceArgs += "--q" }

$yiFaceArgs = @()
if ($YiH) { $yiFaceArgs += "--h" }
if ($YiT) { $yiFaceArgs += "--t" }
if ($YiQ) { $yiFaceArgs += "--q" }

& $py "$scripts/cjk_diacritics_html.py"
& $py "$scripts/cjk_multigraphs_html.py"
& $py "$scripts/hangul_html.py"
& $py "$scripts/yi_html.py"
& $py "$scripts/kana_html.py"
& $py "$scripts/build_hangul.py" --woff2-only
& $py "$scripts/build_yi.py" --woff2-only -j 61 @yiFaceArgs
& $py "$scripts/build_kana.py" --woff2-only -j 61 @kanaFaceArgs
# & $py "$scripts/build_cjk.py" --woff2-only --hint-base-only -j 61 @cjkFaceArgs
& $py "$scripts/build_cjk.py" --css-only @cjkFaceArgs
# & $py "$scripts/edenia_app.py"
& $py "$scripts/sync_edenian_fonts.py"
& $py "$scripts/update_obsidian_theme_fonts.py" --bake --vault "C:/Users/Admin/Dropbox" --private-only
robocopy "c:/Users/Admin/fonts/Scripts/obsidian-edenia" "C:/Users/Admin/Dropbox/.obsidian/plugins/obsidian-edenia" /e /mir
