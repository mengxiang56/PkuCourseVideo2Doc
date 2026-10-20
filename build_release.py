"""Assemble a clean portable ZIP and the current user's executable entry."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import zipfile

from version import VERSION

ROOT = Path(__file__).resolve().parent
APP = ROOT/'release'/'课程文字稿'


def copy_licenses():
    target = APP/'第三方许可'
    target.mkdir(exist_ok=True)
    components = ['playwright','yt-dlp','requests','pycryptodomex','urllib3','certifi','charset-normalizer','idna','greenlet','pyee','typing_extensions','PyInstaller']
    versions = {}
    for name in components:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        versions[name] = dist.version
        for file in dist.files or []:
            if '.dist-info/' not in str(file).replace('\\','/'):
                continue
            if not any(word in Path(file).name.upper() for word in ('LICENSE','COPYING','NOTICE')):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file():
                folder = target/name
                folder.mkdir(exist_ok=True)
                shutil.copy2(source,folder/Path(file).name)
    import playwright
    driver = Path(playwright.__file__).parent/'driver'
    for name, source in [('Node-LICENSE',driver/'LICENSE'),('Playwright-Node-LICENSE',driver/'package'/'LICENSE'),('Playwright-NOTICE',driver/'package'/'NOTICE')]:
        if source.is_file():
            shutil.copy2(source,target/(name+'.txt'))
    ffmpeg = Path(shutil.which('ffmpeg'))
    license_file = ffmpeg.parent.parent/'LICENSE.txt'
    if license_file.is_file():
        shutil.copy2(license_file,target/'FFmpeg-LICENSE.txt')
    config = subprocess.run([str(ffmpeg),'-version'],capture_output=True,text=True,encoding='utf-8',errors='replace',check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    (target/'FFmpeg-build.txt').write_text(config.stdout+config.stderr,encoding='utf-8')
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license,target/'Python-LICENSE.txt')
    for library in ('tcl8.6','tk8.6'):
        source = Path(sys.base_prefix)/'tcl'/library/'license.terms'
        if source.is_file():
            shutil.copy2(source,target/(library+'-LICENSE.txt'))
    (target/'组件版本.json').write_text(json.dumps(versions,ensure_ascii=False,indent=2),encoding='utf-8')
    (target/'说明.txt').write_text('本目录保留应用运行组件的原始许可与说明。\nFFmpeg 项目与源代码：https://ffmpeg.org/\nPython：https://www.python.org/\nPlaywright：https://playwright.dev/python/\nyt-dlp：https://github.com/yt-dlp/yt-dlp\n',encoding='utf-8-sig')


def deploy_current():
    # A same-volume hard link saves space while keeping a normal self-contained
    # folder. No accounts, browser profiles or recordings are copied.
    payload = [APP/'课程文字稿.exe', *[path for path in (APP/'_internal').rglob('*') if path.is_file()]]
    for source in payload:
        destination = ROOT/source.relative_to(APP)
        if not destination.resolve().is_relative_to(ROOT):
            raise RuntimeError('Unexpected deployment path')
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            if os.path.samefile(source,destination):
                continue
            destination.unlink()
        try:
            os.link(source,destination)
        except OSError:
            shutil.copy2(source,destination)


def assemble():
    if not (APP/'课程文字稿.exe').is_file():
        raise RuntimeError('先运行 PyInstaller 生成程序。')
    shutil.copy2(ROOT/'使用说明.txt',APP/'使用说明.txt')
    copy_licenses()
    archive = ROOT/'release'/f'课程文字稿_{VERSION}_Windows_x64.zip'
    # Positive selection keeps user data and logs out even after a smoke check.
    approved = ['课程文字稿.exe','使用说明.txt','第三方许可','_internal']
    paths = []
    for name in approved:
        path = APP/name
        paths.extend([path] if path.is_file() else [file for file in path.rglob('*') if file.is_file()])
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for path in sorted(paths):
            package.write(path,Path('课程文字稿')/path.relative_to(APP))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert not any(len(PurePosixPath(name).parts) > 1 and PurePosixPath(name).parts[1] in {'data','output','.private','.venv'} for name in names)
        assert package.testzip() is None
    deploy_current()
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(paths),'local_exe':str(ROOT/'课程文字稿.exe'),'sha256':digest},ensure_ascii=True),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--assemble-only',action='store_true')
    args = parser.parse_args()
    if not args.assemble_only:
        subprocess.run([sys.executable,str(ROOT/'make_icon.py')],check=True)
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(ROOT/'release'),'--workpath',str(ROOT/'build'),str(ROOT/'course_transcript.spec')],cwd=ROOT,check=True)
    assemble()
