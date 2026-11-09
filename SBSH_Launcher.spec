# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [
    ('app_icon.ico', '.'),
    ('Modl', 'Modl'),
    ('ui', 'ui'),
    ('modules', 'modules'),
]
binaries = []
hiddenimports = [
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'search',
    'Modl',
    'Modl.Steam_api',
    'Modl.steamCmd',
    'Modl.workshop',
    'Modl.Nexus_api',
    'Nexus_api',
    'modules',
    'modules.qb',
    'modules.favorites',
    'modules.saved_games',
    'modules.download_queue',
    'modules.installed_mods',
    'modules.downloaded_mods',
    'modules.mod_pipeline',
    'modules.duplicate_cleaner',
    'modules.download_router',
    'ui',
    'ui.styles',
    'ui.games_tab',
    'ui.mods_tab',
    'ui.favorites_tab',
    'ui.downloads_tab',
    'ui.settings_tab',
    'dotenv',
    'bs4',
    'requests',
    'browser_cookie3',
    'curl_cffi',
    'curl_cffi.requests',
]

for pkg in ['curl_cffi', 'browser_cookie3']:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ['launcher.py'],
    pathex=['.', 'Modl'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'scipy', 'cv2', 'opencv_python', 'opencv_contrib_python', 'mediapipe', 'UnityPy'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SBSH_Launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
