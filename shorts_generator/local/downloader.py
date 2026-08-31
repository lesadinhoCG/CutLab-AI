"""Local YouTube download via yt-dlp.

Returns a local video path so the rest of the local pipeline can read it
directly from disk. YouTube authentication is loaded from Firefox and Deno is
enabled for JavaScript challenge solving.
"""

import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from ..config import LOCAL_OUTPUT_DIR


def _import_ytdlp():
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is required for --mode local. Install it with:\n"
            "    pip install -U \"yt-dlp[default]\""
        ) from exc
    return yt_dlp


def _format_for(fmt: str) -> str:
    """Map the project's '720' / '1080' shorthand to a yt-dlp selector."""
    try:
        height = int(fmt)
    except (TypeError, ValueError):
        height = 720

    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={height}][ext=mp4]/best"
    )


def _extract_youtube_video_id(source: str) -> Optional[str]:
    """Best-effort extraction of a YouTube video ID from a URL."""
    parsed = urlparse(source)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if host in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
        return video_id or None

    if "youtube.com" in host:
        if parsed.path.startswith("/watch"):
            query = parse_qs(parsed.query)
            video_id = query.get("v", [""])[0]
            return video_id or None

        match = re.search(r"/(?:shorts|embed|live)/([^/?#&]+)", parsed.path)
        if match:
            return match.group(1)

    return None


def _resolve_local_path(source: str) -> Optional[str]:
    """Return a local filesystem path if the input already points to one."""
    parsed = urlparse(source)

    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            raw_path = f"//{parsed.netloc}{raw_path}"

        # ``urlparse`` keeps the slash required by the file-URI grammar in
        # front of a Windows drive letter (``file:///C:/video.mp4`` becomes
        # ``/C:/video.mp4``).  That is not a valid absolute Windows path, so
        # normalize it before constructing Path.  Do not strip the leading
        # slashes of UNC paths such as ``file://server/share/video.mp4``.
        if (
            os.name == "nt"
            and len(raw_path) >= 3
            and raw_path[0] == "/"
            and raw_path[1].isalpha()
            and raw_path[2] == ":"
        ):
            raw_path = raw_path[1:]

        candidate = Path(raw_path).expanduser()
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())
        raise RuntimeError(f"Local file URL does not exist: {source}")

    if parsed.scheme in ("http", "https"):
        return None

    candidate = Path(source).expanduser()
    if candidate.exists() and candidate.is_file():
        return str(candidate.resolve())

    if (
        any(separator in source for separator in (os.sep, "/"))
        or source.startswith("~")
        or source.startswith(".")
    ):
        raise RuntimeError(f"Local file path does not exist: {source}")

    return None


def _existing_download(
    out_dir: str,
    video_id: str,
    quality: str,
) -> Optional[str]:
    """Return a cached download only when it matches the requested quality."""

    for extension in (".mp4", ".mkv", ".webm"):
        candidate = os.path.join(
            out_dir,
            f"source_{video_id}_{quality}{extension}",
        )

        if os.path.exists(candidate):
            return candidate

    return None


def probe_youtube_quality(video_url: str) -> dict:
    """Return the best video height offered by YouTube without downloading.

    Metadata extraction can be temporarily unavailable even though a later
    download succeeds, so callers should treat a zero height as "unknown".
    """
    yt_dlp = _import_ytdlp()
    attempts = [
        ("firefox", {"cookiesfrombrowser": ("firefox",)}),
        ("anonymous", {}),
    ]
    best_height = 0
    best_mode = None

    for mode, auth_options in attempts:
        try:
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "skip_download": True,
                "socket_timeout": 20,
                "retries": 2,
                "js_runtimes": {"deno": {}},
                "remote_components": {"ejs:github"},
                **auth_options,
            }) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except Exception:
            continue

        heights = [
            int(item.get("height") or 0)
            for item in (info.get("formats") or [])
            if item.get("vcodec", "none") != "none"
            and item.get("protocol") != "mhtml"
            and item.get("ext") != "mhtml"
        ]
        height = max(heights, default=0)
        if height > best_height:
            best_height = height
            best_mode = mode
        if best_height >= 1080:
            break

    return {"max_height": best_height, "mode": best_mode}


def download_youtube_local(
    video_url: str,
    fmt: str = "720",
    out_dir: Optional[str] = None,
) -> str:
    """Download a remote URL or return an existing local file unchanged."""
    local_path = _resolve_local_path(video_url)
    if local_path:
        print(f"[download/local] using local file: {local_path}", flush=True)
        return local_path

    yt_dlp = _import_ytdlp()
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    # The FastAPI frontend controls quality through the environment so this
    # remains compatible with main.py even when it still passes its old 720
    # default explicitly.
    requested_quality = (
        os.getenv("CUTLAB_DOWNLOAD_QUALITY", str(fmt or "720"))
        .strip()
    )

    if requested_quality not in {"720", "1080"}:
        requested_quality = "720"

    fmt = requested_quality

    video_id = _extract_youtube_video_id(video_url)
    if video_id:
        cached = _existing_download(
            out_dir,
            video_id,
            requested_quality,
        )
        if cached:
            print(f"[download/local] reusing cached download: {cached}", flush=True)
            return cached

    print(f"[download/local] {video_url} @ {fmt}p -> {out_dir}/", flush=True)

    base_ydl_opts = {
        "format": _format_for(fmt),
        "outtmpl": os.path.join(
            out_dir,
            f"source_%(id)s_{requested_quality}.%(ext)s",
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Deno is the recommended runtime for current YouTube JS challenges.
        "js_runtimes": {"deno": {}},
        # Allow official EJS scripts as a fallback when the package is absent.
        "remote_components": {"ejs:github"},
    }

    # A stale or experiment-bound logged-in YouTube session can return
    # "The page needs to be reloaded" while the same public video works
    # anonymously. Keep cookies as the first choice for restricted videos,
    # but retry public videos without browser cookies when that session fails.
    attempts = [
        ("firefox", {"cookiesfrombrowser": ("firefox",)}),
        ("anonymous", {}),
    ]
    last_error = None

    for mode, auth_options in attempts:
        try:
            with yt_dlp.YoutubeDL({**base_ydl_opts, **auth_options}) as ydl:
                info = ydl.extract_info(video_url, download=True)
                path = ydl.prepare_filename(info)

                # merge_output_format may rename the extension after merging.
                if not os.path.exists(path):
                    stem, _ = os.path.splitext(path)
                    for extension in (".mp4", ".mkv", ".webm"):
                        candidate = stem + extension
                        if os.path.exists(candidate):
                            path = candidate
                            break
            break
        except Exception as exc:
            last_error = exc
            if mode == attempts[-1][0]:
                raise
            print(
                f"[download/local] {mode} session failed; retrying anonymously: {exc}",
                flush=True,
            )
    else:  # Defensive: attempts is currently always non-empty.
        raise RuntimeError("YouTube download failed") from last_error

    print(f"[download/local] ready: {path}", flush=True)
    return path


