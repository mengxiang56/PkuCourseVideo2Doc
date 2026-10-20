"""Offline smoke check executed by the actual frozen application."""
import asyncio
import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import traceback
import zipfile


def run_check(destination):
    result = {'ok':False, 'checks':{}}
    try:
        from core import Media, download_media, find_ffmpeg, prepare_video, verify_word
        from engine import Workflow
        from runtime_paths import app_home, resource_path
        from version import VERSION
        result['version'] = VERSION
        result['checks']['bundle_resources'] = resource_path('assets/app.ico').is_file() and resource_path('使用说明.txt').is_file()
        result['checks']['bundled_ffmpeg'] = Path(find_ffmpeg()).resolve() == resource_path('bin/ffmpeg.exe').resolve()
        with tempfile.TemporaryDirectory(prefix='app-check-', dir=destination.parent) as directory:
            folder = Path(directory)
            # A local, encrypted test stream exercises the bundled downloader,
            # crypto libraries, FFmpeg and FFprobe without user accounts.
            (folder/'play').mkdir()
            (folder/'keys').mkdir()
            key = folder/'keys'/'key.bin'
            key.write_bytes(os.urandom(16))
            key_hits = []
            class Handler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_GET(self):
                    if self.path == '/keys/key.bin':
                        valid = 'session=authorized' in self.headers.get('Cookie','')
                        key_hits.append(valid)
                        if not valid:
                            self.send_error(401)
                            return
                    return super().do_GET()
            server = http.server.ThreadingHTTPServer(('127.0.0.1',0), functools.partial(Handler,directory=str(folder)))
            serving = threading.Thread(target=server.serve_forever,daemon=True)
            serving.start()
            previous_proxy = os.environ.get('NO_PROXY')
            os.environ['NO_PROXY'] = '127.0.0.1,localhost'
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                keyinfo = folder/'keyinfo.txt'
                keyinfo.write_text(base+'/keys/key.bin\n'+key.as_posix()+'\n',encoding='utf-8')
                subprocess.run([find_ffmpeg(),'-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc=size=160x90:rate=10','-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-hls_time','1','-hls_key_info_file',str(keyinfo),'-hls_playlist_type','vod',str(folder/'play'/'playlist.m3u8')],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                output = folder/'output'
                output.mkdir()
                cookies = [{'name':'session','value':'authorized','domain':'127.0.0.1','path':'/keys','secure':False,'expires':-1}]
                media = download_media(Media(base+'/play/playlist.m3u8','hls'),cookies,output,lambda _:None,threading.Event())
                video = prepare_video(media,output,lambda _:None,threading.Event())
                result['checks']['encrypted_video_download'] = bool(key_hits) and all(key_hits) and video.is_file()
            finally:
                server.shutdown()
                server.server_close()
                serving.join(timeout=3)
                if previous_proxy is None:
                    os.environ.pop('NO_PROXY',None)
                else:
                    os.environ['NO_PROXY'] = previous_proxy
            async def browser_check():
                workflow = Workflow(folder/'state',lambda *args:None)
                workflow.background_browser = True
                async with workflow.browser_session():
                    page = await workflow.context.new_page()
                    await page.set_content('<input type="file" accept="video/*">')
                    await page.locator('input').set_input_files(str(video))
                    return workflow.context.headless and await page.locator('input').evaluate('e=>e.files.length') == 1
            result['checks']['background_browser_and_video_input'] = asyncio.run(browser_check())
            word = folder/'fixture.docx'
            with zipfile.ZipFile(word,'w') as archive:
                archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>检查文字稿</w:t></w:r></w:p></w:body></w:document>')
            result['checks']['word_validation'] = verify_word(word)
            import tkinter as tk
            from app import App, ROOT
            window = tk.Tk()
            window.withdraw()
            try:
                app = App(window)
                window.update_idletasks()
                result['checks']['desktop_ui'] = len(app.sources.tabs()) == 3
                result['checks']['data_folder_beside_exe'] = ROOT == app_home()
            finally:
                window.destroy()
        result['ok'] = all(result['checks'].values())
    except Exception:
        result['error'] = traceback.format_exc()
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1
