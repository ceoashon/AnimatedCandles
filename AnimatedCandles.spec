# Build with: pyinstaller AnimatedCandles.spec
# Produces:   dist/AnimatedCandles.app  (macOS .app bundle)
# Requires:   pip install pyinstaller

block_cipher = None

a = Analysis(
    ['animated_candles.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.colorchooser',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zlib_archive, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AnimatedCandles',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # windowed=True — no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,   # macOS: let Tkinter handle events natively
    target_arch=None,       # universal binary when None; set 'arm64' or 'x86_64' to pin
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AnimatedCandles',
)

app = BUNDLE(
    coll,
    name='AnimatedCandles.app',
    icon=None,              # replace with 'AnimatedCandles.icns' if you have one
    bundle_identifier='com.animatedcandles.app',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1',
        'NSHumanReadableCopyright': 'AnimatedCandles',
    },
)
