# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

# Base path of repository
base_dir = os.path.abspath(SPECPATH)

datas = [
    (os.path.join(base_dir, 'web'), 'web'),
]

binaries = []

hiddenimports = [
    'tv_matcher',
    'tv_matcher.browser',
    'tv_matcher.config',
    'tv_matcher.database',
    'tv_matcher.dovi',
    'tv_matcher.matcher',
    'tv_matcher.media_tags',
    'tv_matcher.metadata',
    'tv_matcher.naming',
    'tv_matcher.ocr',
    'tv_matcher.pipeline',
    'tv_matcher.renamer',
    'tv_matcher.video',
    'server',
    'server.api_server',
    'sqlite3',
]

a = Analysis(
    [os.path.join(base_dir, 'app.py')],
    pathex=[base_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'unittest', 'test'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='tvbackupmatcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='TVBackupMatcher.app',
        icon=None,
        bundle_identifier='com.tvbackupmatcher.gui',
        info_plist={
            'CFBundleName': 'TVBackupMatcher',
            'CFBundleDisplayName': 'TVBackupMatcher GUI',
            'CFBundleIdentifier': 'com.tvbackupmatcher.gui',
            'CFBundleVersion': '2.2.0',
            'CFBundleShortVersionString': '2.2.0',
            'CFBundlePackageType': 'APPL',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
        }
    )

