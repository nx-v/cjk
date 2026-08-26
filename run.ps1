param(
  # Script filters: omit all → build everything. Any set → only those scripts.
  # Hangul is -Jamo (alias -Hangul) so -H can mean half-faces, not Hangul.
  [switch]$Yi,
  [switch]$Kana,
  [Alias('Hangul')]
  [switch]$Jamo,
  [switch]$Cjk,

  # Shared face flags (apply to every script being built). Additive: -H -T →
  # h+t only; -Base -H -T → base+h+t. Per-script -KanaH / -YiT / … still work.
  [switch]$Base,
  [switch]$H,
  [switch]$T,
  [switch]$Q,

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

$anyScript = $Yi -or $Kana -or $Jamo -or $Cjk
$doYi = (-not $anyScript) -or $Yi
$doKana = (-not $anyScript) -or $Kana
$doHangul = (-not $anyScript) -or $Jamo
$doCjk = (-not $anyScript) -or $Cjk

# No face flags → builders use their full defaults (CJK base+h; kana/yi all
# segment faces). Selective switches are additive and do not imply base.
$cjkFaceArgs = @()
if ($CjkFaces) {
  $cjkFaceArgs += @("--faces", $CjkFaces)
} else {
  if ($CjkBase -or $Base) { $cjkFaceArgs += "--base" }
  if ($CjkH -or $H) { $cjkFaceArgs += "--h" }
}
foreach ($span in $Range) {
  if ($span) { $cjkFaceArgs += @("--range", $span) }
}

$kanaFaceArgs = @()
if ($KanaBase -or $Base) { $kanaFaceArgs += "--base" }
if ($KanaH -or $H) { $kanaFaceArgs += "--h" }
if ($KanaT -or $T) { $kanaFaceArgs += "--t" }
if ($KanaQ -or $Q) { $kanaFaceArgs += "--q" }

$yiFaceArgs = @()
if ($YiBase -or $Base) { $yiFaceArgs += "--base" }
if ($YiH -or $H) { $yiFaceArgs += "--h" }
if ($YiT -or $T) { $yiFaceArgs += "--t" }
if ($YiQ -or $Q) { $yiFaceArgs += "--q" }

$jobArgs = @("-j", "$Jobs")
Write-Host "Jobs: $Jobs"
if ($kanaFaceArgs.Count) { Write-Host ("Kana faces: " + ($kanaFaceArgs -join ' ')) }
if ($yiFaceArgs.Count) { Write-Host ("Yi faces: " + ($yiFaceArgs -join ' ')) }
if ($cjkFaceArgs.Count) { Write-Host ("CJK faces: " + ($cjkFaceArgs -join ' ')) }

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
