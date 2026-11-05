@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 clean_mod_texture_suffixes_gui.py
) else (
    python clean_mod_texture_suffixes_gui.py
)
if errorlevel 1 pause
