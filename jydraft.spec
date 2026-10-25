# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
datas = [
    (str(ROOT / 'jydraft' / 'blueprint.json'), 'jydraft'),
    (str(ROOT / 'jydraft' / 'native-resource-catalog.json'), 'jydraft'),
    (str(ROOT / 'jydraft' / 'web' / 'index.html'), 'jydraft/web'),
    (str(ROOT / 'jydraft' / 'codec_dlopen.cpp'), 'jydraft'),
]
binaries = []
for name in ('ffmpeg', 'ffprobe'):
    for candidate in (ROOT / 'bin' / name, ROOT / 'bin' / (name + '.exe')):
        if candidate.is_file():
            binaries.append((str(candidate), '.'))
            break
if sys.platform == 'darwin' and (ROOT / 'bin' / 'jycodec').is_file():
    binaries.append((str(ROOT / 'bin' / 'jycodec'), '.'))

a = Analysis(
    [str(ROOT / 'app.py')],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
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
    name='jydraft',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='jydraft',
)
