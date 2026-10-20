# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import shutil
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH)
ffmpeg = shutil.which('ffmpeg')
if not ffmpeg:
    raise RuntimeError('Building requires FFmpeg and FFprobe on PATH.')
ffprobe = Path(ffmpeg).with_name('ffprobe.exe')
if not ffprobe.is_file():
    raise RuntimeError('FFprobe must be beside FFmpeg.')

a = Analysis(
    [str(root/'app_entry.py')],
    pathex=[str(root)],
    binaries=[(ffmpeg,'bin'), (str(ffprobe),'bin')] + collect_dynamic_libs('Cryptodome'),
    datas=[(str(root/'assets'/'app.ico'),'assets'), (str(root/'使用说明.txt'),'.')] + copy_metadata('yt-dlp') + copy_metadata('playwright'),
    hiddenimports=collect_submodules('yt_dlp') + ['Cryptodome.Cipher.AES'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PIL', 'numpy', 'matplotlib', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='课程文字稿', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root/'assets'/'app.ico'), version=str(root/'assets'/'version_info.txt'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='课程文字稿')
