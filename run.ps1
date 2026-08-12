c:/python314/python.exe c:/Users/Admin/fonts/Scripts/cjk_diacritics_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/cjk_digraphs_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/hangul_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/yi_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/kana_html.py;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_hangul.py --woff2-only;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_yi.py --woff2-only;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_kana.py --woff2-only;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_cjk.py --woff2-only -j 61;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/sync_obsidian_panfonts.py;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/update_obsidian_theme_fonts.py --bake --vault "C:/Users/Admin/Dropbox" --private-only
$oldPlugin = "C:/Users/Admin/Dropbox/.obsidian/plugins/obsidian-panfonts"
if (Test-Path $oldPlugin) { Remove-Item -Recurse -Force $oldPlugin }
robocopy "c:/Users/Admin/fonts/Scripts/obsidian-edenia" "C:/Users/Admin/Dropbox/.obsidian/plugins/obsidian-edenia" /E /XO
