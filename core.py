"""Local media utilities. Authentication remains in the dedicated browser profile."""
from __future__ import annotations

import http.cookiejar
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from runtime_paths import resource_path

VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".wmv", ".flv", ".mpeg", ".mpg", ".3gp"})


class Cancelled(Exception):
    pass


def check_cancel(cancel: threading.Event):
    if cancel.is_set():
        raise Cancelled("任务已停止，已下载的文件保留在输出文件夹中。")


def validate_course_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname != "course.pku.edu.cn"
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("请粘贴以 https://course.pku.edu.cn/ 开头的课程回放网址。")
    if not parsed.path or parsed.path == "/":
        raise ValueError("请使用具体一节课的回放网址。")
    return value


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:90]
    if not value:
        return "课程回放"
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        return "_" + value
    return value


def clean_error(error: object) -> str:
    # URLs may contain course tokens or signed media credentials.
    text = re.sub(r"https?://[^\s<>\"']+", "[网址已隐藏]", str(error))
    text = re.sub(r"(?i)(token|authorization|cookie|password)(\s*[:=]\s*)[^\s,;]+", r"\1\2[已隐藏]", text)
    return text[:1500]


def media_kind(url: str, content_type: str = "") -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    path = unquote(parsed.path).lower()
    mime = content_type.partition(";")[0].lower().strip()
    if path.endswith(".m3u8") or "mpegurl" in mime:
        return "hls"
    if path.endswith(".mpd") or mime == "application/dash+xml":
        return "dash"
    if path.endswith((".ts", ".m4s", ".aac", ".vtt")):
        return None
    if path.endswith((".mp4", ".webm", ".mkv", ".mov", ".m4v")) or mime in {"video/mp4", "video/webm"}:
        return "video"
    if path.endswith((".mp3", ".wav", ".m4a", ".flac")) or mime in {"audio/mpeg", "audio/mp4", "audio/wav"}:
        return "audio"
    return None


@dataclass
class Media:
    url: str
    kind: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        parsed = urlsplit(self.url)
        return f"{self.kind.upper()} · {parsed.hostname} · {unquote(parsed.path.rsplit('/', 1)[-1])[:65]}"


def cookie_jar(cookies: list[dict]) -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()
    for c in cookies:
        expiry = c.get("expires", -1)
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=c["name"], value=c["value"], port=None,
            port_specified=False, domain=c["domain"], domain_specified=bool(c["domain"]),
            domain_initial_dot=c["domain"].startswith("."), path=c.get("path", "/"),
            path_specified=True, secure=c.get("secure", False),
            expires=int(expiry) if expiry and expiry > 0 else None,
            discard=not expiry or expiry <= 0, comment=None, comment_url=None,
            rest={"HttpOnly": None} if c.get("httpOnly") else {}, rfc2109=False,
        ))
    return jar


def find_ffmpeg() -> str:
    local = resource_path('bin/ffmpeg.exe' if os.name == 'nt' else 'bin/ffmpeg')
    if local.is_file():
        return str(local)
    path = shutil.which("ffmpeg")
    if path:
        return path
    raise RuntimeError("需要 FFmpeg：请将 ffmpeg.exe 放入本工具的 bin 文件夹，或加入系统 PATH。")


def download_media(media: Media, cookies: list[dict], directory: Path,
                   report, cancel: threading.Event) -> Path:
    from yt_dlp import YoutubeDL

    check_cancel(cancel)
    last = [0.0]

    def progress(info):
        check_cancel(cancel)
        if info.get("status") == "downloading" and time.monotonic() - last[0] > 1:
            total = info.get("total_bytes") or info.get("total_bytes_estimate")
            got = info.get("downloaded_bytes", 0)
            message = f"下载回放：{got / total:.0%}" if total else f"下载回放：{got / 1024**2:.1f} MB"
            report(message)
            last[0] = time.monotonic()

    class Logger:
        def debug(self, _): pass
        def warning(self, message): report("下载提示：" + clean_error(message))
        def error(self, _): pass

    opts = {
        "outtmpl": str(directory / "replay.%(ext)s"),
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "ffmpeg_location": find_ffmpeg(),
        "http_headers": media.headers,
        "progress_hooks": [progress], "logger": Logger(),
        "noplaylist": True, "quiet": True, "no_warnings": False,
        "retries": 4, "fragment_retries": 4, "socket_timeout": 30,
        "continuedl": True, "concurrent_fragment_downloads": 3,
        "skip_unavailable_fragments": False,
    }
    with YoutubeDL(opts) as ydl:
        # CookieJar enforces host and path scope for every request and redirect.
        for c in cookie_jar(cookies):
            ydl.cookiejar.set_cookie(c)
        info = ydl.extract_info(media.url, download=True)
        check_cancel(cancel)
        expected = Path(ydl.prepare_filename(info))
    files = [p for p in directory.glob("replay.*")
             if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".mp3", ".m4a", ".wav", ".flac"}
             and not re.search(r"\.f[^.]+\.", p.name)]
    if expected in files:
        return expected
    if len(files) == 1:
        return files[0]
    raise RuntimeError("下载器未返回完整音视频文件，请查看输出文件夹并重新尝试。")


def accepts_video(accept: str, suffix: str) -> bool:
    tokens = {item.strip().lower() for item in accept.split(",") if item.strip()}
    mime = mimetypes.guess_type("lecture" + suffix.lower())[0]
    return not tokens or bool(tokens & {"*/*", "video/*", suffix.lower(), mime})


def prepare_video(source: Path, directory: Path, report, cancel: threading.Event) -> Path:
    """Keep the complete video and give each uploaded recording a unique title."""
    check_cancel(cancel)
    source = source.resolve()
    if not source.is_file() or source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("请选择完整的课程视频文件，例如 MP4、MKV 或 MOV。")
    probe = Path(find_ffmpeg()).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    executable = str(probe) if probe.is_file() else shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("需要 FFprobe 检查视频画面和声音，请将 ffprobe.exe 与 ffmpeg.exe 放在一起。")
    report("正在检查完整视频的画面和声音轨道。")
    try:
        result = subprocess.run([executable, "-v", "error", "-show_streams", "-of", "json", str(source)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        streams = json.loads(result.stdout).get("streams", []) if result.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise RuntimeError("视频文件读取失败，请确认文件完整且能正常播放。") from None
    if not any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams):
        raise ValueError("文件中未检测到课程视频画面，请选择完整的视频回放。")
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise ValueError("视频中未检测到声音轨道，请选择包含教师声音的完整回放。")
    check_cancel(cancel)
    target = directory / (directory.name + source.suffix.lower())
    if source == target.resolve():
        return source
    if source.parent == directory.resolve() and source.name.startswith("replay."):
        source.replace(target)
    else:
        # An existing video remains in place. A hard link avoids copying large
        # files on the same volume; other volumes use a cancellable byte copy.
        try:
            os.link(source, target)
        except OSError:
            partial = target.with_suffix(target.suffix + ".partial")
            with source.open("rb") as original, partial.open("wb") as output:
                while chunk := original.read(8 * 1024 * 1024):
                    check_cancel(cancel)
                    output.write(chunk)
            check_cancel(cancel)
            partial.replace(target)
    report("完整课堂视频已就绪，将直接上传给通义听悟。")
    return target


def verify_word(path: Path) -> bool:
    """Only mark an actual, nonempty Word document as a completed transcript."""
    if path.suffix.lower() == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            return bool("".join(n.text or "" for n in root.iter(ns + "t")).strip())
        except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
            return False
    # Legacy .doc is an OLE binary; content rendering still requires Word.
    if path.suffix.lower() == ".doc":
        with path.open("rb") as stream:
            return stream.read(8) == bytes.fromhex("D0CF11E0A1B11AE1") and path.stat().st_size > 512
    return False
