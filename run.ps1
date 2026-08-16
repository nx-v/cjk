c:/python314/python.exe c:/Users/Admin/fonts/Scripts/cjk_diacritics_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/cjk_multigraphs_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/hangul_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/yi_html.py;
c:/python314/python.exe c:/Users/Admin/fonts/Scripts/kana_html.py;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_hangul.py --woff2-only;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_yi.py --woff2-only;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_kana.py --woff2-only;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_cjk.py --woff2-only -j 61;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/build_cjk.py --css-only;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/sync_edenian_fonts.py;
& c:/python314/python.exe c:/Users/Admin/fonts/Scripts/update_obsidian_theme_fonts.py --bake --vault "C:/Users/Admin/Dropbox" --private-only;
robocopy "c:/Users/Admin/fonts/Scripts/obsidian-edenia" "C:/Users/Admin/Dropbox/.obsidian/plugins/obsidian-edenia" /e /mir
