from __future__ import annotations

import ipaddress
import logging
import mimetypes
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PLATFORM_HOSTS = {
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "instagram.com",
    "tiktok.com",
    "vimeo.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "fb.watch",
    "twitch.tv",
    "reddit.com",
    "v.redd.it",
    "dailymotion.com",
    "dai.ly",
}
DIRECT_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".m4v"}
MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-msvideo": ".avi",
}
logger = logging.getLogger(__name__)


class RemoteImportError(Exception):
    pass


class RemoteImportCancelled(RemoteImportError):
    pass


PLATFORM_LABELS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "vimeo.com": "Vimeo",
    "twitter.com": "X",
    "x.com": "X",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "twitch.tv": "Twitch",
    "reddit.com": "Reddit",
    "dailymotion.com": "Dailymotion",
}


def remote_source_details(url: str) -> dict:
    """Validate a link before queueing work and return user-facing support details."""
    parsed = validate_public_https_url(url)
    host = normalized_host(parsed.hostname or "")
    if is_direct_video_url(parsed):
        return {
            "platform": "Direct video",
            "kind": "direct",
            "message": "Direct public video file detected. The server will verify its type and playable video track.",
        }
    if not matches_host(host, platform_hosts()):
        raise RemoteImportError(
            "This website is not supported yet. Use YouTube, Instagram, TikTok, Vimeo, X, Facebook, Twitch, Reddit, Dailymotion, or a direct HTTPS video file."
        )
    label = next((name for domain, name in PLATFORM_LABELS.items() if host == domain or host.endswith(f".{domain}")), "Public video")
    return {
        "platform": label,
        "kind": "platform",
        "message": f"{label} link detected. It must be public, available without login, and not DRM-protected.",
    }


@dataclass(frozen=True)
class RemoteMedia:
    path: Path
    title: str
    source_url: str
    mime_type: str


def estimated_format_size(format_info: dict, duration: float) -> int | None:
    """Return yt-dlp's size, or estimate it from bitrate and duration."""
    size = format_info.get("filesize") or format_info.get("filesize_approx")
    if size:
        return int(size)
    bitrate = format_info.get("tbr")
    if bitrate and duration:
        return int(float(bitrate) * 1000 * duration / 8)
    return None


def choose_platform_format(info: dict, max_bytes: int) -> str:
    """Choose a browser-friendly MP4 that fits before downloading any streams."""
    duration = float(info.get("duration") or 0)
    budget = int(max_bytes * 0.96)
    formats = [item for item in info.get("formats", []) if item.get("format_id")]

    def height(item: dict) -> int:
        return int(item.get("height") or 0)

    def is_h264(item: dict) -> bool:
        return str(item.get("vcodec") or "").lower().startswith(("avc1", "h264"))

    def is_aac(item: dict) -> bool:
        return str(item.get("acodec") or "").lower().startswith(("mp4a", "aac"))

    progressive: list[tuple[tuple[int, int, int], str]] = []
    for item in formats:
        if item.get("vcodec") == "none" or item.get("acodec") == "none" or height(item) > 720:
            continue
        size = estimated_format_size(item, duration)
        if not size or size > budget:
            continue
        score = (height(item), int(is_h264(item) and is_aac(item)), int(item.get("ext") == "mp4"))
        progressive.append((score, str(item["format_id"])))

    video_formats = [
        item
        for item in formats
        if item.get("vcodec") not in (None, "none")
        and item.get("acodec") == "none"
        and item.get("ext") == "mp4"
        and is_h264(item)
        and 0 < height(item) <= 720
    ]
    audio_formats = [
        item
        for item in formats
        if item.get("vcodec") == "none"
        and item.get("acodec") not in (None, "none")
        and item.get("ext") in {"m4a", "mp4"}
        and is_aac(item)
    ]
    pairs: list[tuple[tuple[int, float], str]] = []
    for video in video_formats:
        video_size = estimated_format_size(video, duration)
        if not video_size:
            continue
        for audio in audio_formats:
            audio_size = estimated_format_size(audio, duration)
            if not audio_size or video_size + audio_size > budget:
                continue
            score = (height(video), float(audio.get("abr") or audio.get("tbr") or 0))
            pairs.append((score, f"{video['format_id']}+{audio['format_id']}"))

    choices: list[tuple[tuple[int, int, float], str]] = [
        ((score[0], 2, score[1]), format_id) for score, format_id in pairs
    ]
    choices.extend(((score[0], score[1], float(score[2])), format_id) for score, format_id in progressive)
    if not choices:
        # Several extractors omit both filesize and bitrate metadata even when
        # they expose a perfectly usable progressive MP4. Prefer that format
        # over rejecting the link before attempting a download. Keep the
        # fallback conservative because the actual size cannot be estimated;
        # yt-dlp's max_filesize and our post-download check remain authoritative.
        fallback_progressive = [
            item for item in formats
            if item.get("ext") == "mp4"
            and is_h264(item)
            and is_aac(item)
            and 0 < height(item) <= 360
        ]
        if fallback_progressive:
            return max(fallback_progressive, key=lambda item: height(item))["format_id"]

        # Some sites expose H.264 video and AAC audio separately, also without
        # size metadata. A low-resolution pair is still safer than failing
        # every import from that extractor; the download size is checked below.
        fallback_video = [item for item in video_formats if height(item) <= 360]
        if fallback_video and audio_formats:
            video = max(fallback_video, key=lambda item: height(item))
            audio = max(audio_formats, key=lambda item: float(item.get("abr") or item.get("tbr") or 0))
            return f"{video['format_id']}+{audio['format_id']}"

        raise RemoteImportError(
            "This platform did not provide a size estimate for a browser-compatible video under the import limit. Try a shorter video or upload a local copy."
        )
    return max(choices, key=lambda choice: choice[0])[1]


def has_readable_video(path: Path, ffmpeg_executable: str) -> bool:
    """Reject audio-only leftovers before they are saved as successful imports."""
    try:
        result = subprocess.run(
            [
                ffmpeg_executable,
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def normalized_host(hostname: str) -> str:
    return hostname.lower().rstrip(".")


def matches_host(hostname: str, allowed: set[str]) -> bool:
    host = normalized_host(hostname)
    return any(host == item or host.endswith(f".{item}") for item in allowed)


def public_addresses(hostname: str) -> list[str]:
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RemoteImportError("The link hostname could not be resolved") from exc
    addresses = sorted({record[4][0] for record in records})
    if not addresses:
        raise RemoteImportError("The link hostname did not resolve to a public address")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise RemoteImportError("Private or local network links are not allowed")
    return addresses


def validate_public_https_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RemoteImportError("Use a public HTTPS link")
    if parsed.username or parsed.password:
        raise RemoteImportError("Links containing credentials are not allowed")
    public_addresses(parsed.hostname)
    return parsed


def platform_hosts() -> set[str]:
    configured = {
        normalized_host(item.strip())
        for item in os.getenv("REMOTE_MEDIA_HOSTS", "").split(",")
        if item.strip()
    }
    return PLATFORM_HOSTS | configured


def is_direct_video_url(parsed: urllib.parse.ParseResult) -> bool:
    return Path(urllib.parse.unquote(parsed.path)).suffix.lower() in DIRECT_VIDEO_EXTENSIONS


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        validate_public_https_url(new_url)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def download_direct_video(
    url: str,
    target_dir: Path,
    video_id: str,
    max_bytes: int,
    deadline: float | None = None,
    progress_callback: Callable[..., None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> RemoteMedia:
    parsed = validate_public_https_url(url)
    if not is_direct_video_url(parsed):
        raise RemoteImportError("The direct link must end in a supported video extension")

    opener = urllib.request.build_opener(SafeRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "UnderstandVideo/0.1"})
    try:
        response = opener.open(request, timeout=30)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RemoteImportError("The direct video could not be downloaded") from exc

    with response:
        final_url = response.geturl()
        validate_public_https_url(final_url)
        content_length = response.headers.get("Content-Length")
        try:
            if content_length and int(content_length) > max_bytes:
                raise RemoteImportError("The remote video exceeds the configured upload limit")
        except ValueError:
            content_length = None
        mime_type = response.headers.get_content_type()
        if not (mime_type.startswith("video/") or mime_type == "application/octet-stream"):
            raise RemoteImportError("The link did not return a video file")
        suffix = Path(urllib.parse.unquote(urllib.parse.urlparse(final_url).path)).suffix.lower()
        if suffix not in DIRECT_VIDEO_EXTENSIONS:
            suffix = MIME_EXTENSIONS.get(mime_type, ".mp4")
        target = target_dir / f"{video_id}{suffix}"
        size = 0
        started_at = time.monotonic()
        try:
            with target.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    if cancel_check and cancel_check():
                        raise RemoteImportCancelled("The online video import was cancelled")
                    if deadline and time.monotonic() >= deadline:
                        raise RemoteImportError("The online video import timed out")
                    size += len(chunk)
                    if size > max_bytes:
                        raise RemoteImportError("The remote video exceeds the configured upload limit")
                    output.write(chunk)
                    if progress_callback:
                        total = int(content_length) if content_length else max_bytes
                        elapsed = max(time.monotonic() - started_at, 0.001)
                        speed = int(size / elapsed)
                        progress_callback(
                            min(90, 10 + int(size / max(total, 1) * 80)),
                            downloaded_bytes=size,
                            total_bytes=int(content_length) if content_length else None,
                            speed_bps=speed,
                            eta_seconds=max(0, int((total - size) / speed)) if content_length and speed else None,
                            stage="Downloading direct video",
                        )
        except Exception:
            target.unlink(missing_ok=True)
            raise

    if size == 0:
        target.unlink(missing_ok=True)
        raise RemoteImportError("The link returned an empty video file")
    title = Path(urllib.parse.unquote(parsed.path)).stem or parsed.hostname or "remote-video"
    return RemoteMedia(target, title, url, mime_type if mime_type.startswith("video/") else "video/mp4")


def download_platform_video(
    url: str,
    target_dir: Path,
    video_id: str,
    max_bytes: int,
    max_duration_seconds: int,
    deadline: float | None = None,
    progress_callback: Callable[..., None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> RemoteMedia:
    parsed = validate_public_https_url(url)
    if not matches_host(parsed.hostname or "", platform_hosts()):
        raise RemoteImportError(
            "This page is not on a supported public video platform. Use a direct HTTPS video link instead."
        )
    try:
        import yt_dlp
        import imageio_ffmpeg
    except ImportError as exc:
        raise RemoteImportError("Online video import is not installed on this server") from exc

    def match_filter(info: dict, *, incomplete: bool) -> str | None:
        duration = info.get("duration")
        if duration and duration > max_duration_seconds:
            return f"Video duration exceeds the {max_duration_seconds // 60}-minute remote import limit"
        if info.get("is_live"):
            return "Live streams cannot be imported"
        return None

    highest_progress = 10
    started_at = time.monotonic()

    def enforce_deadline(status: dict) -> None:
        nonlocal highest_progress
        if cancel_check and cancel_check():
            raise RemoteImportCancelled("The online video import was cancelled")
        if deadline and time.monotonic() >= deadline:
            raise RemoteImportError("The online video import timed out")
        if status.get("status") == "downloading":
            downloaded = int(status.get("downloaded_bytes") or 0)
            total = int(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
            if total:
                highest_progress = max(highest_progress, min(88, 15 + int(downloaded / total * 70)))
                if progress_callback:
                    elapsed = max(time.monotonic() - started_at, 0.001)
                    speed = int(downloaded / elapsed)
                    progress_callback(
                        highest_progress,
                        downloaded_bytes=downloaded,
                        total_bytes=total,
                        speed_bps=speed,
                        eta_seconds=max(0, int((total - downloaded) / speed)) if speed else None,
                        stage="Downloading platform video",
                    )

    output_template = str(target_dir / f"{video_id}.%(ext)s")
    base_options = {
        "merge_output_format": "mp4",
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "outtmpl": output_template,
        "noplaylist": True,
        "playlist_items": "1",
        "max_filesize": max_bytes,
        "match_filter": match_filter,
        "progress_hooks": [enforce_deadline],
        "postprocessor_hooks": [enforce_deadline],
        "socket_timeout": 30,
        "retries": 2,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    try:
        for leftover in target_dir.glob(f"{video_id}.*"):
            leftover.unlink(missing_ok=True)
        metadata_options = {
            **base_options,
            "skip_download": True,
            "progress_hooks": [],
            "postprocessor_hooks": [],
        }
        with yt_dlp.YoutubeDL(metadata_options) as downloader:
            preview = downloader.extract_info(url, download=False)
        if progress_callback:
            progress_callback(12)
        format_selector = choose_platform_format(preview, max_bytes)
        options = {**base_options, "format": format_selector}
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
    except Exception as exc:
        for partial in target_dir.glob(f"{video_id}.*"):
            partial.unlink(missing_ok=True)
        logger.warning("Platform import failed for host %s: %s", parsed.hostname, exc)
        message = str(exc)
        if isinstance(exc, RemoteImportCancelled) or "cancelled" in message.lower():
            raise RemoteImportCancelled("The online video import was cancelled") from exc
        if "timed out" in message.lower():
            detail = "The online video import timed out"
        elif "duration exceeds" in message.lower():
            detail = f"Video duration exceeds the {max_duration_seconds // 60}-minute remote import limit"
        elif "live streams" in message.lower():
            detail = "Live streams cannot be imported"
        elif "cannot fit the import limit" in message.lower():
            detail = str(exc)
        elif any(marker in message.lower() for marker in ("private video", "this video is private", "private account")):
            detail = "This video is private. Import a public link or upload a file you are allowed to use."
        elif any(marker in message.lower() for marker in ("sign in", "login required", "cookies")):
            detail = "This platform requires a signed-in session. For privacy, this importer does not use your browser login; upload an authorized copy instead."
        elif "drm" in message.lower():
            detail = "This video is DRM-protected and cannot be imported. Upload a non-protected copy you are allowed to use."
        elif any(marker in message.lower() for marker in ("geo", "country", "region")):
            detail = "This video is not available from the server's region. Upload an authorized local copy instead."
        elif any(marker in message.lower() for marker in ("429", "too many requests", "temporarily unavailable", "service unavailable")):
            detail = "The platform is temporarily limiting downloads. The import will retry automatically."
        else:
            detail = "This public video could not be imported. Check that it is public, available without login, and not DRM-protected."
        raise RemoteImportError(detail) from exc

    candidates = [
        path for path in target_dir.glob(f"{video_id}.*")
        if path.is_file() and path.suffix.lower() in DIRECT_VIDEO_EXTENSIONS
    ]
    if not candidates:
        raise RemoteImportError("The platform did not provide a downloadable video")
    readable = [path for path in candidates if has_readable_video(path, imageio_ffmpeg.get_ffmpeg_exe())]
    if not readable:
        for downloaded in target_dir.glob(f"{video_id}.*"):
            downloaded.unlink(missing_ok=True)
        raise RemoteImportError("The platform did not provide a playable video track")
    target = max(readable, key=lambda path: path.stat().st_size)
    for downloaded in candidates:
        if downloaded != target:
            downloaded.unlink(missing_ok=True)
    if target.stat().st_size > max_bytes:
        for downloaded in target_dir.glob(f"{video_id}.*"):
            downloaded.unlink(missing_ok=True)
        raise RemoteImportError("The remote video exceeds the configured upload limit")
    mime_type = mimetypes.guess_type(target.name)[0] or "video/mp4"
    title = str(info.get("title") or "Online video").strip()[:180]
    if progress_callback:
        progress_callback(94)
    return RemoteMedia(target, title, url, mime_type)


def import_remote_video(
    url: str,
    target_dir: Path,
    video_id: str,
    max_bytes: int,
    max_duration_seconds: int,
    timeout_seconds: int,
    progress_callback: Callable[..., None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> RemoteMedia:
    deadline = time.monotonic() + timeout_seconds
    parsed = validate_public_https_url(url)
    if is_direct_video_url(parsed):
        return download_direct_video(
            url, target_dir, video_id, max_bytes, deadline, progress_callback, cancel_check
        )
    return download_platform_video(
        url,
        target_dir,
        video_id,
        max_bytes,
        max_duration_seconds,
        deadline,
        progress_callback,
        cancel_check,
    )
