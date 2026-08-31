"""Local clipping with intelligent 9:16 reframing, synchronized captions,
and NVIDIA NVENC acceleration.

Reframing modes:
  - auto: decide automatically between person crop and content canvas.
  - person: force a true 9:16 crop around the detected speaker/face.
            If no face is found, use a centered 9:16 crop.
  - content: preserve the full frame on a 9:16 canvas with blurred background.

Strategy:
  1. Analyze only a few representative frames with OpenCV.
  2. If the clip is mostly a person talking, use a fixed 9:16 crop centered
     around the detected speaker.
  3. If the clip contains dense visual information (news, screenshots, sites,
     slides, charts, etc.), preserve the full frame on a vertical canvas with
     a blurred background.
  4. Render directly from the source video with FFmpeg.
  5. Optionally burn synchronized styled captions from the source SRT.
  6. Encode the final MP4 with NVIDIA NVENC when available.

This avoids per-frame rendering in Python/OpenCV and avoids temporary .cut.mp4
files that can stay locked on Windows.
"""

import os
import re
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import LOCAL_OUTPUT_DIR


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _ratio(aspect_ratio: str) -> float:
    """Parse '9:16' -> 9/16."""
    try:
        w, h = aspect_ratio.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9.0 / 16.0


def _safe_filename(name: str, max_length: int = 100) -> str:
    """Create a Windows-safe filename."""

    name = str(name or "").strip()

    # Remove characters that Windows does not allow in filenames.
    name = re.sub(r'[<>:"/\\|?*]', "", name)

    # Collapse repeated spaces.
    name = re.sub(r"\s+", " ", name)

    # Windows does not like filenames ending in dots or spaces.
    name = name.strip(" .")

    if not name:
        name = "Untitled"

    if len(name) > max_length:
        name = name[:max_length].rstrip(" .")

    return name


def _normalize_reframe_mode(
    value: Optional[str] = None,
) -> str:
    """Normalize reframing mode.

    If value is None/empty, read CUTLAB_REFRAME_MODE from the environment.
    This is how the FastAPI frontend controls the clipper without changing
    the main.py command-line interface.
    """

    raw = str(value or "").strip().lower()

    if not raw:
        raw = (
            os.getenv(
                "CUTLAB_REFRAME_MODE",
                "auto",
            )
            .strip()
            .lower()
        )

    aliases = {
        "auto": "auto",
        "person": "person",
        "face": "person",
        "faces": "person",
        "speaker": "person",
        "portrait": "person",
        "crop": "person",
        "content": "content",
        "canvas": "content",
        "fullframe": "content",
        "full-frame": "content",
        "preserve": "content",
        "blur": "content",
    }

    return aliases.get(
        raw,
        "auto",
    )


def _load_opencv():
    """Load OpenCV."""

    try:
        import cv2  # type: ignore

    except ImportError as e:
        raise RuntimeError(
            "opencv-python is required for local content analysis.\n"
            "Install it with:\n"
            "    pip install opencv-python==4.10.0.84"
        ) from e

    if not hasattr(cv2, "VideoCapture"):
        raise RuntimeError(
            "Invalid OpenCV installation: VideoCapture is unavailable."
        )

    return cv2


# ---------------------------------------------------------------------------
# NVIDIA NVENC detection
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _has_nvenc() -> bool:
    """Return True when FFmpeg exposes NVIDIA h264_nvenc."""

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-encoders",
            ],
            check=True,
            capture_output=True,
            text=True,
            errors="ignore",
        )

        output = (
            (result.stdout or "")
            + (result.stderr or "")
        )

        return "h264_nvenc" in output

    except Exception:
        return False


def _video_encoder_args() -> List[str]:
    """Prefer NVIDIA NVENC and fall back to CPU/libx264."""

    if _has_nvenc():
        return [
            "-c:v",
            "h264_nvenc",

            "-preset",
            "p5",

            "-tune",
            "hq",

            "-rc",
            "vbr",

            "-cq",
            "20",

            "-b:v",
            "0",

            "-pix_fmt",
            "yuv420p",
        ]

    print(
        "[clip/local] warning: h264_nvenc not found; "
        "falling back to CPU/libx264",
        flush=True,
    )

    return [
        "-c:v",
        "libx264",

        "-preset",
        "fast",

        "-crf",
        "20",

        "-pix_fmt",
        "yuv420p",
    ]


# ---------------------------------------------------------------------------
# Face detection
# ---------------------------------------------------------------------------

def _create_face_detector(cv2):
    """Create Haar face detector when available."""

    if not hasattr(
        cv2,
        "CascadeClassifier",
    ):
        return None

    try:
        cascade_path = (
            cv2.data.haarcascades
            + "haarcascade_frontalface_default.xml"
        )

        if not os.path.exists(
            cascade_path
        ):
            return None

        detector = cv2.CascadeClassifier(
            cascade_path
        )

        if detector.empty():
            return None

        return detector

    except Exception:
        return None


def _detect_faces(
    cv2,
    detector,
    frame,
) -> List[Tuple[int, int, int, int]]:
    """Detect faces using a downscaled image for speed."""

    if detector is None:
        return []

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    # Analyze half resolution for speed.
    scale = 0.5

    small = cv2.resize(
        gray,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_AREA,
    )

    try:
        faces = detector.detectMultiScale(
            small,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30),
        )

    except Exception:
        return []

    result: List[
        Tuple[int, int, int, int]
    ] = []

    for x, y, w, h in faces:

        result.append(
            (
                int(x / scale),
                int(y / scale),
                int(w / scale),
                int(h / scale),
            )
        )

    return result


# ---------------------------------------------------------------------------
# Content detection
# ---------------------------------------------------------------------------

def _content_metrics(
    cv2,
    frame,
) -> Tuple[float, float]:
    """Return overall and side content scores.

    Edge density is used as a lightweight indication of information-dense
    frames such as:

    - news pages
    - screenshots
    - websites
    - charts
    - slides
    - tweets
    - text-heavy frames

    side_content_score is especially important because it detects useful
    information close to the left/right edges that a vertical crop might cut.
    """

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    # Small analysis resolution keeps CPU use very low.
    small = cv2.resize(
        gray,
        (320, 180),
        interpolation=cv2.INTER_AREA,
    )

    edges = cv2.Canny(
        small,
        80,
        160,
    )

    overall_density = (
        float((edges > 0).sum())
        / float(edges.size)
    )

    # Analyze the outer 25% of each side independently.
    side_width = max(
        1,
        int(
            edges.shape[1]
            * 0.25
        ),
    )

    left = edges[
        :,
        :side_width,
    ]

    right = edges[
        :,
        -side_width:,
    ]

    side_pixels = float(
        (left > 0).sum()
        + (right > 0).sum()
    )

    side_total = float(
        left.size
        + right.size
    )

    if side_total > 0:
        side_density = (
            side_pixels
            / side_total
        )
    else:
        side_density = 0.0

    # Normalize roughly into 0..1.
    overall_score = min(
        1.0,
        overall_density / 0.16,
    )

    side_score = min(
        1.0,
        side_density / 0.16,
    )

    return (
        overall_score,
        side_score,
    )


# ---------------------------------------------------------------------------
# Highlight analysis
# ---------------------------------------------------------------------------

def _analyze_range(
    source_path: str,
    start_time: float,
    end_time: float,
    reframe_mode: Optional[str] = None,
) -> Dict:
    """Analyze only a few frames from the selected highlight.

    No temporary clip is generated.

    OpenCV seeks directly into the original source video.
    """

    cv2 = _load_opencv()

    cap = cv2.VideoCapture(
        source_path
    )

    if not cap.isOpened():
        raise RuntimeError(
            f"could not open {source_path}"
        )

    src_w = int(
        cap.get(
            cv2.CAP_PROP_FRAME_WIDTH
        )
    )

    src_h = int(
        cap.get(
            cv2.CAP_PROP_FRAME_HEIGHT
        )
    )

    if src_w <= 0 or src_h <= 0:

        cap.release()

        raise RuntimeError(
            f"invalid video dimensions: "
            f"{source_path}"
        )

    detector = _create_face_detector(
        cv2
    )

    duration = max(
        0.1,
        end_time - start_time,
    )

    # Only eight frames are analyzed.
    sample_count = 8

    face_centers: List[
        Tuple[int, int]
    ] = []

    face_areas: List[
        float
    ] = []

    overall_scores: List[
        float
    ] = []

    side_scores: List[
        float
    ] = []

    valid_samples = 0
    face_samples = 0

    try:

        for i in range(
            sample_count
        ):

            fraction = (
                i
                / max(
                    1,
                    sample_count - 1,
                )
            )

            timestamp = (
                start_time
                + duration
                * fraction
            )

            # Seek directly to this moment.
            cap.set(
                cv2.CAP_PROP_POS_MSEC,
                timestamp * 1000.0,
            )

            ret, frame = cap.read()

            if not ret:
                continue

            valid_samples += 1

            # -------------------------------------------------------
            # Detect information density.
            # -------------------------------------------------------

            overall, sides = (
                _content_metrics(
                    cv2,
                    frame,
                )
            )

            overall_scores.append(
                overall
            )

            side_scores.append(
                sides
            )

            # -------------------------------------------------------
            # Detect speaker.
            # -------------------------------------------------------

            faces = _detect_faces(
                cv2,
                detector,
                frame,
            )

            if faces:

                face_samples += 1

                # Largest face is normally the main speaker.
                x, y, w, h = max(
                    faces,
                    key=lambda f:
                    f[2] * f[3],
                )

                face_centers.append(
                    (
                        x + w // 2,
                        y + h // 2,
                    )
                )

                face_areas.append(
                    float(w * h)
                    / float(
                        src_w
                        * src_h
                    )
                )

    finally:

        cap.release()
        del cap

    # ---------------------------------------------------------------
    # Speaker position
    # ---------------------------------------------------------------

    if face_centers:

        center_x = int(
            sum(
                x
                for x, _
                in face_centers
            )
            / len(
                face_centers
            )
        )

        center_y = int(
            sum(
                y
                for _, y
                in face_centers
            )
            / len(
                face_centers
            )
        )

    else:

        center_x = (
            src_w // 2
        )

        center_y = (
            src_h // 2
        )

    # ---------------------------------------------------------------
    # Average metrics
    # ---------------------------------------------------------------

    if face_areas:

        avg_face_area = (
            sum(face_areas)
            / len(face_areas)
        )

    else:

        avg_face_area = 0.0

    if overall_scores:

        avg_content = (
            sum(overall_scores)
            / len(overall_scores)
        )

    else:

        avg_content = 0.0

    if side_scores:

        avg_side_content = (
            sum(side_scores)
            / len(side_scores)
        )

    else:

        avg_side_content = 0.0

    if valid_samples > 0:

        face_presence = (
            face_samples
            / valid_samples
        )

    else:

        face_presence = 0.0

    # ---------------------------------------------------------------
    # Decide framing strategy.
    #
    # We intentionally prefer CONTENT mode when uncertain.
    #
    # That protects:
    #   news
    #   screenshots
    #   text
    #   charts
    #   sites
    #   slides
    #
    # A face alone is NOT enough to force crop mode.
    # ---------------------------------------------------------------

    person_dominant = (
        face_presence >= 0.50

        and avg_face_area
        >= 0.020

        and avg_content
        < 0.38

        and avg_side_content
        < 0.34
    )

    requested_mode = (
        _normalize_reframe_mode(
            reframe_mode
        )
    )

    if requested_mode == "person":

        # Forced podcast/interview mode.
        # Face analysis still decides WHERE to crop, but content-density
        # heuristics are not allowed to switch back to the 16:9 canvas.
        mode = "person"

    elif requested_mode == "content":

        mode = "content"

    else:

        if person_dominant:
            mode = "person"
        else:
            mode = "content"

    return {
        "mode": mode,

        "requested_mode": requested_mode,

        "center": (
            center_x,
            center_y,
        ),

        "src_w": src_w,

        "src_h": src_h,

        "face_area": (
            avg_face_area
        ),

        "face_presence": (
            face_presence
        ),

        "content_score": (
            avg_content
        ),

        "side_content_score": (
            avg_side_content
        ),
    }


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

def _canvas_size(
    aspect_ratio: str,
) -> Tuple[int, int]:
    """Return exact 720p/1080p dimensions for 9:16 or 16:9 output."""

    requested_ratio = (
        os.getenv(
            "CUTLAB_ASPECT_RATIO",
            str(aspect_ratio or "9:16"),
        )
        .strip()
    )

    if requested_ratio not in {"9:16", "16:9"}:
        requested_ratio = "9:16"

    quality = (
        os.getenv(
            "CUTLAB_OUTPUT_RESOLUTION",
            "720",
        )
        .strip()
    )

    if quality not in {"720", "1080"}:
        quality = "720"

    if requested_ratio == "16:9":
        return (
            1920,
            1080,
        ) if quality == "1080" else (
            1280,
            720,
        )

    return (
        1080,
        1920,
    ) if quality == "1080" else (
        720,
        1280,
    )


# ---------------------------------------------------------------------------
# Person mode FFmpeg filter
# ---------------------------------------------------------------------------

def _person_filter(
    analysis: Dict,
    canvas_w: int,
    canvas_h: int,
) -> str:
    """Build fixed vertical crop centered around the speaker."""

    src_w = int(
        analysis["src_w"]
    )

    src_h = int(
        analysis["src_h"]
    )

    center_x, center_y = (
        analysis["center"]
    )

    target_ratio = (
        canvas_w
        / canvas_h
    )

    source_ratio = (
        src_w
        / src_h
    )

    if target_ratio < source_ratio:

        crop_h = src_h

        crop_w = int(
            round(
                crop_h
                * target_ratio
            )
        )

    else:

        crop_w = src_w

        crop_h = int(
            round(
                crop_w
                / target_ratio
            )
        )

    crop_w = max(
        2,
        crop_w
        - crop_w % 2,
    )

    crop_h = max(
        2,
        crop_h
        - crop_h % 2,
    )

    x0 = int(
        center_x
        - crop_w / 2
    )

    y0 = int(
        center_y
        - crop_h / 2
    )

    x0 = max(
        0,
        min(
            src_w
            - crop_w,
            x0,
        ),
    )

    y0 = max(
        0,
        min(
            src_h
            - crop_h,
            y0,
        ),
    )

    return (
        f"[0:v]"
        f"crop="
        f"{crop_w}:"
        f"{crop_h}:"
        f"{x0}:"
        f"{y0},"

        f"scale="
        f"{canvas_w}:"
        f"{canvas_h}:"
        f"flags=lanczos,"

        f"setsar=1,"
        f"format=yuv420p"

        f"[v]"
    )


# ---------------------------------------------------------------------------
# Landscape 16:9 filter
# ---------------------------------------------------------------------------

def _landscape_filter(
    canvas_w: int,
    canvas_h: int,
) -> str:
    """Preserve the source frame in true 16:9 output."""

    return (
        "[0:v]"
        f"scale={canvas_w}:{canvas_h}:"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1,format=yuv420p[v]"
    )


# ---------------------------------------------------------------------------
# Content mode FFmpeg filter
# ---------------------------------------------------------------------------

def _content_filter(
    canvas_w: int,
    canvas_h: int,
) -> str:
    """Preserve the full image over a blurred vertical background.

    Important performance trick:

    The background is reduced to quarter resolution BEFORE blur.

    Instead of blurring 720x1280 pixels, FFmpeg blurs roughly 180x320 and
    enlarges the result afterward.

    Visually the background remains soft, but CPU work is much smaller.
    """

    small_w = (
        canvas_w // 4
    )

    small_h = (
        canvas_h // 4
    )

    small_w = max(
        2,
        small_w
        - small_w % 2,
    )

    small_h = max(
        2,
        small_h
        - small_h % 2,
    )

    return (
        # Make two copies of the frame.
        "[0:v]"
        "split=2"
        "[bgsrc][fgsrc];"

        # -----------------------------------------------------------
        # Background
        # -----------------------------------------------------------

        "[bgsrc]"

        f"scale="
        f"{small_w}:"
        f"{small_h}:"
        "force_original_aspect_ratio=increase,"

        f"crop="
        f"{small_w}:"
        f"{small_h},"

        # Blur while image is tiny.
        "boxblur=12:2,"

        # Expand blurred background to full canvas.
        f"scale="
        f"{canvas_w}:"
        f"{canvas_h}:"
        "flags=bilinear,"

        # Slightly darken background.
        "eq="
        "brightness=-0.18:"
        "saturation=0.90,"

        "setsar=1"

        "[bg];"

        # -----------------------------------------------------------
        # Foreground
        # -----------------------------------------------------------

        "[fgsrc]"

        f"scale="
        f"{canvas_w}:"
        f"{canvas_h}:"
        "force_original_aspect_ratio=decrease:"
        "flags=lanczos,"

        "setsar=1"

        "[fg];"

        # -----------------------------------------------------------
        # Put full video over blurred background.
        # -----------------------------------------------------------

        "[bg][fg]"

        "overlay="
        "(W-w)/2:"
        "(H-h)/2:"
        "shortest=1,"

        "format=yuv420p"

        "[v]"
    )



# ---------------------------------------------------------------------------
# Optional visual filters
# ---------------------------------------------------------------------------

def _env_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(
            os.getenv(
                name,
                str(default),
            )
        )
    except (TypeError, ValueError):
        value = default

    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def _visual_filter_chain() -> List[str]:
    """Return FFmpeg filters selected in the CutLab frontend."""

    filters: List[str] = []

    brightness = _env_float(
        "CUTLAB_FILTER_BRIGHTNESS",
        0.0,
        -100.0,
        100.0,
    )
    contrast = _env_float(
        "CUTLAB_FILTER_CONTRAST",
        0.0,
        -100.0,
        100.0,
    )
    saturation = _env_float(
        "CUTLAB_FILTER_SATURATION",
        0.0,
        -100.0,
        100.0,
    )

    if any(
        abs(value) > 0.01
        for value in (
            brightness,
            contrast,
            saturation,
        )
    ):
        eq_brightness = brightness / 100.0
        eq_contrast = max(
            0.05,
            1.0 + contrast / 100.0,
        )
        eq_saturation = max(
            0.0,
            1.0 + saturation / 100.0,
        )

        filters.append(
            "eq="
            f"brightness={eq_brightness:.3f}:"
            f"contrast={eq_contrast:.3f}:"
            f"saturation={eq_saturation:.3f}"
        )

    sharpen = _env_float(
        "CUTLAB_FILTER_SHARPEN",
        0.0,
        0.0,
        100.0,
    )

    if sharpen > 0.01:
        amount = (
            0.05
            + 1.95
            * (sharpen / 100.0)
        )
        filters.append(
            f"unsharp=5:5:{amount:.3f}:5:5:0"
        )

    cinematic = _env_float(
        "CUTLAB_FILTER_CINEMATIC",
        0.0,
        0.0,
        100.0,
    )

    if cinematic > 0.01:
        strength = cinematic / 100.0
        filters.append(
            "eq="
            f"contrast={1.0 + 0.12 * strength:.3f}:"
            f"saturation={1.0 - 0.10 * strength:.3f}"
        )
        filters.append(
            "colorbalance="
            f"rs={0.025 * strength:.4f}:"
            f"bs={0.040 * strength:.4f}"
        )

    warm = _env_float(
        "CUTLAB_FILTER_WARM",
        0.0,
        0.0,
        100.0,
    )

    if warm > 0.01:
        strength = warm / 100.0
        filters.append(
            "colorbalance="
            f"rs={0.10 * strength:.4f}:"
            f"gs={0.025 * strength:.4f}:"
            f"bs={-0.08 * strength:.4f}"
        )

    cool = _env_float(
        "CUTLAB_FILTER_COOL",
        0.0,
        0.0,
        100.0,
    )

    if cool > 0.01:
        strength = cool / 100.0
        filters.append(
            "colorbalance="
            f"rs={-0.07 * strength:.4f}:"
            f"gs={0.015 * strength:.4f}:"
            f"bs={0.11 * strength:.4f}"
        )

    grayscale = _env_float(
        "CUTLAB_FILTER_GRAYSCALE",
        0.0,
        0.0,
        100.0,
    )

    if grayscale > 0.01:
        remaining_saturation = max(
            0.0,
            1.0 - grayscale / 100.0,
        )
        filters.append(
            f"hue=s={remaining_saturation:.3f}"
        )

    vignette = _env_float(
        "CUTLAB_FILTER_VIGNETTE",
        0.0,
        0.0,
        100.0,
    )

    if vignette > 0.01:
        strength = vignette / 100.0
        denominator = (
            6.0
            - 3.6 * strength
        )
        filters.append(
            f"vignette=PI/{denominator:.3f}:eval=frame"
        )

    return filters

def _append_visual_filters(
    filter_complex: str,
    input_label: str = "[v]",
) -> Tuple[str, str]:
    filters = _visual_filter_chain()

    if not filters:
        return filter_complex, input_label

    filter_complex += (
        f";{input_label}"
        + ",".join(filters)
        + "[vfx]"
    )

    print(
        "[filters/local] "
        + ", ".join(filters),
        flush=True,
    )

    return filter_complex, "[vfx]"


# ---------------------------------------------------------------------------
# Synced captions
# ---------------------------------------------------------------------------

CAPTION_STYLES = {
    "reels_bold": "Reels Bold",
    "tiktok": "TikTok White",
    "tiktok_yellow": "TikTok Yellow",
    "tiktok_neon": "TikTok Neon",
    "tiktok_animated": "TikTok Animated",
    "karaoke": "Karaoke Multicolor",
    "multi_pop": "Multi Color Pop",
    "minimal": "Minimal",
    "box": "Box",
    "yellow_pop": "Yellow Pop",
    "headline": "Headline",
}

ANIMATED_CAPTION_STYLES = {
    "tiktok_animated",
    "karaoke",
    "multi_pop",
}

CAPTION_FONTS = (
    "Arial",
    "Arial Black",
    "Segoe UI",
    "Trebuchet MS",
    "Verdana",
    "Impact",
    "Georgia",
)

CAPTION_POSITIONS = (
    "bottom",
    "center",
    "top",
)


def _env_bool(
    name: str,
    default: bool = False,
) -> bool:
    """Read a flexible true/false environment variable."""

    value = os.getenv(
        name,
        "1" if default else "0",
    )

    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "sim",
    }


def _caption_config() -> Dict:
    """Read caption settings passed by the CutLab AI frontend."""

    style = (
        os.getenv(
            "CUTLAB_CAPTION_STYLE",
            "reels_bold",
        )
        .strip()
        .lower()
    )

    if style not in CAPTION_STYLES:
        style = "reels_bold"

    font = (
        os.getenv(
            "CUTLAB_CAPTION_FONT",
            "Arial Black",
        )
        .strip()
    )

    if font not in CAPTION_FONTS:
        font = "Arial Black"

    position = (
        os.getenv(
            "CUTLAB_CAPTION_POSITION",
            "bottom",
        )
        .strip()
        .lower()
    )

    if position not in CAPTION_POSITIONS:
        position = "bottom"

    try:
        font_size = int(
            os.getenv(
                "CUTLAB_CAPTION_SIZE",
                "54",
            )
        )
    except ValueError:
        font_size = 54

    font_size = max(
        28,
        min(
            86,
            font_size,
        ),
    )

    try:
        words_per_block = int(
            os.getenv(
                "CUTLAB_CAPTION_WORDS",
                "5",
            )
        )
    except ValueError:
        words_per_block = 5

    words_per_block = max(
        2,
        min(
            10,
            words_per_block,
        ),
    )

    return {
        "enabled": _env_bool(
            "CUTLAB_CAPTIONS_ENABLED",
            default=False,
        ),
        "style": style,
        "font": font,
        "position": position,
        "font_size": font_size,
        "words_per_block": words_per_block,
    }


@lru_cache(maxsize=1)
def _has_ass_filter() -> bool:
    """Return True when FFmpeg has the libass-backed ASS filter."""

    try:

        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-filters",
            ],
            check=True,
            capture_output=True,
            text=True,
            errors="ignore",
        )

        output = (
            (result.stdout or "")
            + "\n"
            + (result.stderr or "")
        )

        for line in output.splitlines():

            columns = line.split()

            if len(columns) < 3:
                continue

            if (
                columns[1].lower() == "ass"
                and columns[2].upper() == "V->V"
            ):
                return True

        return bool(
            re.search(
                r"(?im)\bass\s+V->V\b",
                output,
            )
        )

    except Exception:
        return False


def _parse_srt_timestamp(
    value: str,
) -> float:
    """Convert 00:01:02,345 to seconds."""

    value = (
        str(value)
        .strip()
        .replace(",", ".")
    )

    parts = value.split(":")

    if len(parts) != 3:
        raise ValueError(
            f"invalid SRT timestamp: {value}"
        )

    hours = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2])

    return (
        hours * 3600.0
        + minutes * 60.0
        + seconds
    )


def _parse_srt(
    srt_path: Path,
) -> List[Dict]:
    """Parse the source-level SRT generated by the local transcriber."""

    try:
        content = srt_path.read_text(
            encoding="utf-8-sig"
        )
    except UnicodeDecodeError:
        content = srt_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    blocks = re.split(
        r"\r?\n\s*\r?\n",
        content.strip(),
    )

    entries: List[Dict] = []

    for block in blocks:

        lines = [
            line.strip()
            for line in block.splitlines()
        ]

        time_index = None

        for index, line in enumerate(
            lines
        ):

            if "-->" in line:
                time_index = index
                break

        if time_index is None:
            continue

        time_line = lines[
            time_index
        ]

        left, right = (
            time_line.split(
                "-->",
                1,
            )
        )

        # SRT may optionally contain position metadata after the end timestamp.
        right = (
            right.strip()
            .split()[0]
        )

        try:
            start = _parse_srt_timestamp(
                left
            )
            end = _parse_srt_timestamp(
                right
            )
        except Exception:
            continue

        if end <= start:
            continue

        text = " ".join(
            line
            for line in lines[
                time_index + 1:
            ]
            if line
        )

        text = re.sub(
            r"<[^>]+>",
            "",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

        if not text:
            continue

        entries.append(
            {
                "start": start,
                "end": end,
                "text": text,
            }
        )

    return entries


def _split_caption_text(
    text: str,
    max_words: int,
) -> List[str]:
    """Split one SRT sentence into short social-video caption blocks."""

    words = (
        str(text)
        .strip()
        .split()
    )

    if not words:
        return []

    return [
        " ".join(
            words[index:
                  index + max_words]
        )
        for index in range(
            0,
            len(words),
            max_words,
        )
    ]


def _caption_events_for_clip(
    entries: List[Dict],
    clip_start: float,
    clip_end: float,
    words_per_block: int,
) -> List[Dict]:
    """Shift source SRT timestamps into the selected Short timeline."""

    events: List[Dict] = []

    for entry in entries:

        source_start = float(
            entry["start"]
        )

        source_end = float(
            entry["end"]
        )

        if (
            source_end <= clip_start
            or source_start >= clip_end
        ):
            continue

        local_start = max(
            source_start,
            clip_start,
        ) - clip_start

        local_end = min(
            source_end,
            clip_end,
        ) - clip_start

        if local_end <= local_start:
            continue

        chunks = _split_caption_text(
            str(
                entry.get(
                    "text",
                    "",
                )
            ),
            words_per_block,
        )

        if not chunks:
            continue

        all_words = sum(
            max(
                1,
                len(chunk.split()),
            )
            for chunk in chunks
        )

        duration = (
            local_end
            - local_start
        )

        cursor = local_start

        for chunk_index, chunk in enumerate(
            chunks
        ):

            word_count = max(
                1,
                len(
                    chunk.split()
                ),
            )

            if (
                chunk_index
                == len(chunks) - 1
            ):
                chunk_end = (
                    local_end
                )
            else:
                chunk_end = (
                    cursor
                    + duration
                    * (
                        word_count
                        / all_words
                    )
                )

            chunk_end = max(
                cursor + 0.08,
                chunk_end,
            )

            chunk_end = min(
                chunk_end,
                local_end,
            )

            if chunk_end > cursor:

                events.append(
                    {
                        "start": cursor,
                        "end": chunk_end,
                        "text": chunk,
                    }
                )

            cursor = chunk_end

    return events


def _ass_timestamp(
    seconds: float,
) -> str:
    """Convert seconds to ASS h:mm:ss.cc."""

    centiseconds = max(
        0,
        int(
            round(
                float(seconds)
                * 100.0
            )
        ),
    )

    hours = (
        centiseconds
        // 360000
    )

    remainder = (
        centiseconds
        % 360000
    )

    minutes = (
        remainder
        // 6000
    )

    remainder = (
        remainder
        % 6000
    )

    secs = (
        remainder
        // 100
    )

    cents = (
        remainder
        % 100
    )

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{secs:02d}."
        f"{cents:02d}"
    )


def _ass_text(
    text: str,
    uppercase: bool = False,
) -> str:
    """Escape user speech for an ASS Dialogue line."""

    value = str(
        text
    ).strip()

    if uppercase:
        value = value.upper()

    # Remove override-tag delimiters instead of allowing spoken text to
    # accidentally become ASS formatting commands.
    value = value.replace(
        "{",
        "(",
    ).replace(
        "}",
        ")",
    )

    value = value.replace(
        "\\",
        r"\\",
    )

    value = value.replace(
        "\n",
        r"\N",
    )

    return value


def _ass_style_values(
    config: Dict,
) -> Dict:
    """Translate a frontend caption preset into ASS style values."""

    style = str(
        config["style"]
    )

    font_size = int(
        config["font_size"]
    )

    values = {
        "primary": "&H00FFFFFF",
        "secondary": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "back_color": "&H64000000",
        "bold": -1,
        "border_style": 1,
        "outline": 5,
        "shadow": 0,
        "font_scale": 1.0,
        "uppercase": False,
    }

    if style == "tiktok":

        values.update(
            {
                "outline": 4,
                "shadow": 2,
                "font_scale": 0.96,
            }
        )

    elif style == "tiktok_yellow":

        values.update(
            {
                "primary": "&H0000FFFF",
                "outline": 5,
                "shadow": 2,
                "font_scale": 0.98,
            }
        )

    elif style == "tiktok_neon":

        values.update(
            {
                "primary": "&H00FFB347",
                "outline_color": "&H006313A8",
                "outline": 5,
                "shadow": 2,
                "font_scale": 1.00,
            }
        )

    elif style in ANIMATED_CAPTION_STYLES:

        values.update(
            {
                "outline": 5,
                "shadow": 2,
                "font_scale": 1.02,
            }
        )

    elif style == "minimal":

        values.update(
            {
                "bold": 0,
                "outline": 2,
                "shadow": 0,
                "font_scale": 0.82,
            }
        )

    elif style == "box":

        values.update(
            {
                "border_style": 3,
                "outline": 1,
                "shadow": 0,
                "back_color": "&H70000000",
                "font_scale": 0.90,
            }
        )

    elif style == "yellow_pop":

        # ASS colours are AABBGGRR. This is a bright warm yellow.
        values.update(
            {
                "primary": "&H004DDBFF",
                "outline": 5,
                "font_scale": 1.02,
            }
        )

    elif style == "headline":

        values.update(
            {
                "outline": 6,
                "font_scale": 1.08,
                "uppercase": True,
            }
        )

    values[
        "font_size"
    ] = max(
        24,
        int(
            round(
                font_size
                * float(
                    values["font_scale"]
                )
            )
        ),
    )

    return values


def _ass_alignment(
    position: str,
) -> Tuple[int, int]:
    """Return ASS alignment and safe vertical margin."""

    if position == "top":
        return 8, 125

    if position == "center":
        return 5, 40

    # Keep captions above the usual social-network controls.
    return 2, 175


def _write_clip_ass(
    source_path: str,
    start_time: float,
    end_time: float,
    canvas_w: int,
    canvas_h: int,
    out_path: str,
    config: Dict,
) -> Optional[Path]:
    """Create a temporary ASS file containing only this highlight's captions."""

    source_srt = Path(
        source_path
    ).with_suffix(
        ".srt"
    )

    if not source_srt.exists():

        print(
            "[captions/local] warning: source SRT not found: "
            f"{source_srt}",
            flush=True,
        )

        return None

    entries = _parse_srt(
        source_srt
    )

    if not entries:

        print(
            "[captions/local] warning: source SRT contains no usable captions",
            flush=True,
        )

        return None

    events = _caption_events_for_clip(
        entries,
        start_time,
        end_time,
        int(
            config["words_per_block"]
        ),
    )

    if not events:

        print(
            "[captions/local] warning: no subtitle events overlap this highlight",
            flush=True,
        )

        return None

    style_values = _ass_style_values(
        config
    )

    alignment, margin_v = (
        _ass_alignment(
            str(
                config["position"]
            )
        )
    )

    font = str(
        config["font"]
    )

    ass_path = (
        Path(
            out_path
        ).parent
        / (
            ".cutlab_caption_"
            f"{uuid.uuid4().hex}.ass"
        )
    )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {canvas_w}
PlayResY: {canvas_h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{style_values['font_size']},{style_values['primary']},{style_values['secondary']},{style_values['outline_color']},{style_values['back_color']},{style_values['bold']},0,0,0,100,100,0,0,{style_values['border_style']},{style_values['outline']},{style_values['shadow']},{alignment},48,48,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogue_lines: List[
        str
    ] = []

    animated_style = (
        str(config.get("style", ""))
        in ANIMATED_CAPTION_STYLES
    )

    accent_palette = [
        "&H0000FFFF",  # yellow
        "&H00FF66CC",  # pink/purple
        "&H00FFFF55",  # lime/cyan
        "&H00FFAA33",  # cyan-blue
    ]

    for event in events:

        if not animated_style:

            dialogue_lines.append(
                "Dialogue: 0,"
                f"{_ass_timestamp(event['start'])},"
                f"{_ass_timestamp(event['end'])},"
                "Default,,0,0,0,,"
                f"{_ass_text(event['text'], uppercase=bool(style_values['uppercase']))}"
            )
            continue

        words = str(
            event.get(
                "text",
                "",
            )
        ).split()

        if not words:
            continue

        event_start = float(
            event["start"]
        )
        event_end = float(
            event["end"]
        )
        total = max(
            0.12,
            event_end - event_start,
        )
        per_word = total / len(words)

        for word_index in range(
            len(words)
        ):
            word_start = (
                event_start
                + per_word * word_index
            )
            word_end = (
                event_end
                if word_index == len(words) - 1
                else event_start
                + per_word * (word_index + 1)
            )

            rendered_words = []

            for index, word in enumerate(
                words
            ):
                escaped = _ass_text(
                    word,
                    uppercase=bool(
                        style_values["uppercase"]
                    ),
                )

                if index == word_index:
                    accent = accent_palette[
                        index
                        % len(accent_palette)
                    ]

                    if str(config.get("style")) == "tiktok_animated":
                        accent = "&H0000FFFF"

                    rendered_words.append(
                        "{\\c"
                        f"{accent}"
                        "\\fscx116\\fscy116}"
                        f"{escaped}"
                        "{\\r}"
                    )
                else:
                    rendered_words.append(
                        escaped
                    )

            animated_text = " ".join(
                rendered_words
            )

            dialogue_lines.append(
                "Dialogue: 0,"
                f"{_ass_timestamp(word_start)},"
                f"{_ass_timestamp(word_end)},"
                "Default,,0,0,0,,"
                f"{animated_text}"
            )

    ass_path.write_text(
        header
        + "\n".join(
            dialogue_lines
        )
        + "\n",
        encoding="utf-8-sig",
    )

    return ass_path


def _ffmpeg_filter_path(
    path: Path,
) -> str:
    """Escape a Windows path for FFmpeg filter syntax."""

    value = str(
        path.resolve()
    ).replace(
        "\\",
        "/",
    )

    # Filter syntax treats a drive colon as a separator.
    value = value.replace(
        ":",
        r"\:",
    )

    value = value.replace(
        "'",
        r"\'",
    )

    return value



# ---------------------------------------------------------------------------
# Render one highlight
# ---------------------------------------------------------------------------

def _render_highlight(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    reframe_mode: Optional[str] = None,
) -> str:
    """Analyze + render directly from the original source."""

    duration = max(
        0.1,
        end_time - start_time,
    )

    # ---------------------------------------------------------------
    # Lightweight OpenCV analysis.
    # ---------------------------------------------------------------

    analysis = _analyze_range(
        source_path,
        start_time,
        end_time,
        reframe_mode=reframe_mode,
    )

    mode = analysis[
        "mode"
    ]

    print(
        "[reframe/local] "
        f"requested={analysis['requested_mode']} "
        f"mode={mode} "
        f"content="
        f"{analysis['content_score']:.2f} "
        f"sides="
        f"{analysis['side_content_score']:.2f} "
        f"face="
        f"{analysis['face_area']:.3f} "
        f"face_presence="
        f"{analysis['face_presence']:.2f}",
        flush=True,
    )

    aspect_ratio = (
        os.getenv(
            "CUTLAB_ASPECT_RATIO",
            str(aspect_ratio or "9:16"),
        )
        .strip()
    )

    if aspect_ratio not in {"9:16", "16:9"}:
        aspect_ratio = "9:16"

    canvas_w, canvas_h = (
        _canvas_size(
            aspect_ratio
        )
    )

    # ---------------------------------------------------------------
    # Choose FFmpeg filter.
    # ---------------------------------------------------------------

    if aspect_ratio == "16:9":

        filter_complex = (
            _landscape_filter(
                canvas_w,
                canvas_h,
            )
        )

        mode = "landscape"

    elif mode == "person":

        filter_complex = (
            _person_filter(
                analysis,
                canvas_w,
                canvas_h,
            )
        )

    else:

        filter_complex = (
            _content_filter(
                canvas_w,
                canvas_h,
            )
        )

    # ---------------------------------------------------------------
    # Optional synchronized captions.
    #
    # The local transcriber already writes source_VIDEO.srt.
    # We select only entries overlapping this highlight, shift them
    # to 00:00, create a temporary ASS file, and burn it onto the
    # final vertical video.
    # ---------------------------------------------------------------

    filter_complex, video_map = (
        _append_visual_filters(
            filter_complex,
            "[v]",
        )
    )

    caption_config = (
        _caption_config()
    )

    caption_path: Optional[
        Path
    ] = None

    if caption_config[
        "enabled"
    ]:

        if not _has_ass_filter():

            print(
                "[captions/local] warning: FFmpeg ASS filter is unavailable; "
                "rendering without captions",
                flush=True,
            )

        else:

            caption_path = (
                _write_clip_ass(
                    source_path,
                    start_time,
                    end_time,
                    canvas_w,
                    canvas_h,
                    out_path,
                    caption_config,
                )
            )

            if caption_path is not None:

                filter_path = (
                    _ffmpeg_filter_path(
                        caption_path
                    )
                )

                # setpts guarantees the subtitle timeline starts at zero after
                # the fast input seek (-ss before -i).
                filter_complex += (
                    f";{video_map}"
                    "setpts=PTS-STARTPTS,"
                    f"ass=filename='{filter_path}'"
                    "[vout]"
                )

                video_map = (
                    "[vout]"
                )

                print(
                    "[captions/local] "
                    "enabled "
                    f"style={caption_config['style']} "
                    f"font={caption_config['font']} "
                    f"position={caption_config['position']} "
                    f"size={caption_config['font_size']} "
                    f"words={caption_config['words_per_block']}",
                    flush=True,
                )

    encoder_args = (
        _video_encoder_args()
    )

    if _has_nvenc():

        encoder_name = (
            "NVIDIA NVENC"
        )

    else:

        encoder_name = (
            "CPU libx264"
        )

    print(
        "[clip/local] "
        f"rendering "
        f"{canvas_w}x{canvas_h} "
        f"with {encoder_name}...",
        flush=True,
    )

    # ---------------------------------------------------------------
    # FFmpeg
    #
    # IMPORTANT:
    #
    # -ss is placed BEFORE -i.
    #
    # This is much faster when working with a source video that can be
    # several hours long.
    #
    # There is no intermediate .cut.mp4 anymore.
    # ---------------------------------------------------------------

    cmd = [
        "ffmpeg",

        "-y",

        "-hide_banner",

        "-loglevel",
        "error",

        "-ss",
        f"{start_time:.3f}",

        "-i",
        source_path,

        "-t",
        f"{duration:.3f}",

        "-filter_complex",
        filter_complex,

        "-map",
        video_map,

        "-map",
        "0:a:0?",
    ]

    # NVIDIA NVENC or CPU fallback.
    cmd.extend(
        encoder_args
    )

    cmd.extend(
        [
            "-c:a",
            "aac",

            "-b:a",
            "160k",

            "-shortest",

            "-movflags",
            "+faststart",

            out_path,
        ]
    )

    try:

        subprocess.run(
            cmd,
            check=True,
        )

    finally:

        if (
            caption_path is not None
            and caption_path.exists()
        ):

            try:
                caption_path.unlink()
            except OSError:
                pass

    if not os.path.exists(
        out_path
    ):

        raise RuntimeError(
            "FFmpeg finished but "
            "output was not created: "
            f"{out_path}"
        )

    return out_path



# ---------------------------------------------------------------------------
# Audio-aware final boundary snap
# CUTLAB_AUDIO_BOUNDARY_V1
# ---------------------------------------------------------------------------

def _audio_boundary_enabled() -> bool:
    return (
        os.getenv(
            "CUTLAB_AUDIO_BOUNDARY_SNAP",
            "1",
        ).strip().lower()
        not in {
            "0",
            "false",
            "no",
            "off",
        }
    )


def _detect_audio_silences_near(
    source_path: str,
    center_time: float,
    radius: float = 4.0,
) -> List[Tuple[float, float]]:
    """Analyze only a tiny audio window around one boundary."""

    window_start = max(
        0.0,
        float(center_time) - float(radius),
    )

    window_duration = max(
        2.0,
        float(radius) * 2.0,
    )

    noise = os.getenv(
        "CUTLAB_AUDIO_SILENCE_NOISE",
        "-35dB",
    )

    min_silence = os.getenv(
        "CUTLAB_AUDIO_SILENCE_MIN",
        "0.30",
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-ss",
        f"{window_start:.3f}",
        "-i",
        source_path,
        "-t",
        f"{window_duration:.3f}",
        "-vn",
        "-af",
        (
            "silencedetect="
            f"noise={noise}:"
            f"d={min_silence}"
        ),
        "-f",
        "null",
        "-",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
        )
        log = proc.stderr or ""
    except Exception as exc:
        print(
            "[boundary/audio] warning: "
            f"silence detection unavailable: {exc}",
            flush=True,
        )
        return []

    intervals: List[Tuple[float, float]] = []
    current_start: Optional[float] = None

    for line in log.splitlines():
        start_match = re.search(
            r"silence_start:\s*(-?\d+(?:\.\d+)?)",
            line,
        )

        if start_match:
            try:
                current_start = (
                    window_start
                    + float(start_match.group(1))
                )
            except ValueError:
                current_start = None

        end_match = re.search(
            r"silence_end:\s*(-?\d+(?:\.\d+)?)",
            line,
        )

        if end_match:
            try:
                silence_end = (
                    window_start
                    + float(end_match.group(1))
                )
            except ValueError:
                continue

            if current_start is None:
                continue

            if silence_end > current_start:
                intervals.append(
                    (
                        max(0.0, current_start),
                        max(0.0, silence_end),
                    )
                )

            current_start = None

    return intervals


def _nearest_audio_boundary(
    source_path: str,
    timestamp: float,
    kind: str,
) -> float:
    if not _audio_boundary_enabled():
        return timestamp

    max_snap = float(
        os.getenv(
            "CUTLAB_AUDIO_BOUNDARY_MAX_SNAP",
            "2.0",
        )
    )

    intervals = _detect_audio_silences_near(
        source_path,
        timestamp,
    )

    candidates: List[float] = []

    for silence_start, silence_end in intervals:
        silence_duration = silence_end - silence_start

        if kind == "start":
            point = silence_end + 0.03
        else:
            point = (
                silence_start
                + min(
                    0.35,
                    max(
                        0.08,
                        silence_duration * 0.5,
                    ),
                )
            )

        if abs(point - timestamp) <= max_snap:
            candidates.append(point)

    if not candidates:
        return timestamp

    return min(
        candidates,
        key=lambda point: abs(point - timestamp),
    )


def _audio_snap_highlight_boundaries(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
) -> Tuple[float, float]:
    """Use real audio only as a small final snap after semantic review."""

    if not _audio_boundary_enabled():
        return (start_time, end_time)

    original_start = float(start_time)
    original_end = float(end_time)

    snapped_start = _nearest_audio_boundary(
        source_path,
        original_start,
        "start",
    )

    snapped_end = _nearest_audio_boundary(
        source_path,
        original_end,
        "end",
    )

    minimum = float(
        os.getenv(
            "CUTLAB_MIN_HIGHLIGHT_SECONDS",
            "300" if str(aspect_ratio).strip() == "16:9" else "30",
        )
    )

    soft_max = float(
        os.getenv(
            "CUTLAB_MAX_HIGHLIGHT_SECONDS",
            "1800" if str(aspect_ratio).strip() == "16:9" else "150",
        )
    )

    extension = float(
        os.getenv(
            "CUTLAB_BOUNDARY_EXTENSION_SECONDS",
            "120" if str(aspect_ratio).strip() == "16:9" else "30",
        )
    )

    hard_max = soft_max + extension

    snapped_duration = snapped_end - snapped_start

    if (
        snapped_end <= snapped_start
        or snapped_duration < minimum
        or snapped_duration > hard_max
    ):
        end_only_duration = snapped_end - original_start
        start_only_duration = original_end - snapped_start

        if minimum <= end_only_duration <= hard_max:
            snapped_start = original_start
        elif minimum <= start_only_duration <= hard_max:
            snapped_end = original_end
        else:
            snapped_start = original_start
            snapped_end = original_end

    if (
        abs(snapped_start - original_start) >= 0.05
        or abs(snapped_end - original_end) >= 0.05
    ):
        print(
            "[boundary/audio] "
            f"{original_start:.2f}-{original_end:.2f}s "
            "-> "
            f"{snapped_start:.2f}-{snapped_end:.2f}s",
            flush=True,
        )

    return (snapped_start, snapped_end)


# ---------------------------------------------------------------------------
# Public single clip API
# ---------------------------------------------------------------------------

def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    reframe_mode: Optional[str] = None,
) -> str:
    """Analyze and render one selected highlight."""

    return _render_highlight(
        source_path,
        start_time,
        end_time,
        aspect_ratio,
        out_path,
        reframe_mode=reframe_mode,
    )


# ---------------------------------------------------------------------------
# Multiple highlights
# ---------------------------------------------------------------------------

def crop_highlights_local(
    source_path: str,
    highlights: List[Dict],
    aspect_ratio: str = "9:16",
    out_dir: Optional[str] = None,
    reframe_mode: Optional[str] = None,
) -> List[Dict]:
    """Render all selected highlights.

    When reframe_mode is None, CUTLAB_REFRAME_MODE from the frontend is used.
    """

    configured_output = (
        os.getenv(
            "CUTLAB_OUTPUT_DIR",
            "",
        )
        .strip()
    )

    if configured_output:
        out_dir = configured_output
    else:
        out_dir = (
            out_dir
            or LOCAL_OUTPUT_DIR
        )

    configured_ratio = (
        os.getenv(
            "CUTLAB_ASPECT_RATIO",
            "",
        )
        .strip()
    )

    if configured_ratio in {"9:16", "16:9"}:
        aspect_ratio = configured_ratio

    os.makedirs(
        out_dir,
        exist_ok=True,
    )

    results: List[Dict] = []

    global_reframe_mode = (
        _normalize_reframe_mode(
            reframe_mode
        )
    )

    print(
        "[clip/local] "
        f"reframe mode: {global_reframe_mode}",
        flush=True,
    )

    if _has_nvenc():

        print(
            "[clip/local] "
            "video encoder: "
            "NVIDIA NVENC",
            flush=True,
        )

    else:

        print(
            "[clip/local] "
            "video encoder: "
            "CPU libx264",
            flush=True,
        )

    # ---------------------------------------------------------------
    # Render each selected highlight.
    # ---------------------------------------------------------------

    for i, h in enumerate(
        highlights,
        1,
    ):

        title = _safe_filename(
            h.get(
                "title",
                f"Short {i}",
            )
        )

        # Example:
        #
        # 01 - O Fim do Feed Infinito.mp4
        #
        out_path = os.path.join(
            out_dir,
            f"{i:02d} - {title}.mp4",
        )

        print(
            f"[clip/local] "
            f"{i}/"
            f"{len(highlights)}: "
            f"{h.get('title', '(untitled)')}",
            flush=True,
        )

        try:

            highlight_reframe_mode = (
                _normalize_reframe_mode(
                    h.get(
                        "reframe_mode"
                    )
                    or global_reframe_mode
                )
            )

            # Final micro-adjustment using REAL source audio pauses.
            # Keep updated times in h so metadata matches the actual MP4.
            h = dict(h)

            snapped_start, snapped_end = (
                _audio_snap_highlight_boundaries(
                    source_path,
                    float(
                        h["start_time"]
                    ),
                    float(
                        h["end_time"]
                    ),
                    aspect_ratio,
                )
            )

            h["start_time"] = snapped_start
            h["end_time"] = snapped_end

            crop_clip_local(
                source_path,
                float(
                    h["start_time"]
                ),
                float(
                    h["end_time"]
                ),
                aspect_ratio,
                out_path,
                reframe_mode=highlight_reframe_mode,
            )

            results.append(
                {
                    **h,
                    "clip_url": out_path,
                }
            )

            print(
                f"[clip/local] "
                f"{i} completed: "
                f"{out_path}",
                flush=True,
            )

        except Exception as e:

            print(
                f"[clip/local] "
                f"{i} failed: "
                f"{e}",
                flush=True,
            )

            results.append(
                {
                    **h,
                    "clip_url": None,
                    "error": str(e),
                }
            )

    return results

