param(
  [switch]$CjkBaseOnly,
  [string]$CjkFaces = "",
  [switch]$CjkH,
  [switch]$CjkT,
  [switch]$CjkQ,
  [switch]$CjkQv,
  [switch]$CjkQh
)

$py = "c:/python314/python.exe"
$scripts = "c:/Users/Admin/fonts/Scripts"

$cjkFaceArgs = @()
if ($CjkBaseOnly) {
  $cjkFaceArgs += "--base-only"
} elseif ($CjkFaces) {
  $cjkFaceArgs += @("--faces", $CjkFaces)
} else {
  if ($CjkH) { $cjkFaceArgs += "--h" }
  if ($CjkT) { $cjkFaceArgs += "--t" }
  if ($CjkQ) { $cjkFaceArgs += "--q" }
  if ($CjkQv) { $cjkFaceArgs += "--qv" }
  if ($CjkQh) { $cjkFaceArgs += "--qh" }
}

& $py "$scripts/cjk_diacritics_html.py"
& $py "$scripts/cjk_multigraphs_html.py"
& $py "$scripts/hangul_html.py"
& $py "$scripts/yi_html.py"
& $py "$scripts/kana_html.py"
# & $py "$scripts/build_hangul.py" --woff2-only
# & $py "$scripts/build_yi.py" --woff2-only
# & $py "$scripts/build_kana.py" --woff2-only
& $py "$scripts/build_cjk.py" --woff2-only --hint-base-only -j 61 @cjkFaceArgs
# & $py "$scripts/build_cjk.py" --css-only @cjkFaceArgs
# & $py "$scripts/edenia_app.py"
& $py "$scripts/sync_edenian_fonts.py"
& $py "$scripts/update_obsidian_theme_fonts.py" --bake --vault "C:/Users/Admin/Dropbox" --private-only
robocopy "c:/Users/Admin/fonts/Scripts/obsidian-edenia" "C:/Users/Admin/Dropbox/.obsidian/plugins/obsidian-edenia" /e /mir