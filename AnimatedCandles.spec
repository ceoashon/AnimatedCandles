from PyInstaller.utils.hooks import collect_all

tkmacosx_datas, tkmacosx_binaries, tkmacosx_hiddenimports = collect_all('tkmacosx')

a = Analysis(
    ['animated_candles.py'],
    pathex=[],
    binaries=tkmacosx_binaries,
    datas=tkmacosx_datas,
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.colorchooser',
        'tkmacosx',
    ] + tkmacosx_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StratCandleDrawer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='StratCandleDrawer',
)

app = BUNDLE(
    coll,
    name='StratCandleDrawer.app',
    icon='icon.icns',
    bundle_identifier='com.stratcandledrawer.app',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1',
        'NSHumanReadableCopyright': 'StratCandleDrawer',
    },
)
