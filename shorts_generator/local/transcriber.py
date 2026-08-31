"""Local transcription via faster-whisper.

Reads a local media file and returns the same shape the highlight generator
expects: {duration, segments[start, end, text]}.

On Windows, this module also registers CUDA 12 / cuBLAS / cuDNN DLL
directories so CTranslate2 / faster-whisper can use NVIDIA GPUs reliably.
"""

import ctypes
import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional


# Keep os.add_dll_directory handles alive for the entire Python process.
_DLL_DIR_HANDLES = []

# Keep track of registered DLL paths.
_DLL_DIR_PATHS = []

# Keep loaded CUDA DLL objects alive.
_LOADED_CUDA_DLLS = []


def _register_windows_dll_directory(directory: Path) -> None:
    """Register one native DLL directory on Windows."""

    if os.name != "nt":
        return

    try:
        directory = directory.resolve()
    except OSError:
        return

    if not directory.exists() or not directory.is_dir():
        return

    directory_str = str(directory)

    if directory_str not in _DLL_DIR_PATHS:
        _DLL_DIR_PATHS.append(directory_str)

    # Also expose the directory through PATH.
    current_path = os.environ.get("PATH", "")

    current_entries = [
        p.strip().lower()
        for p in current_path.split(os.pathsep)
        if p.strip()
    ]

    if directory_str.lower() not in current_entries:
        os.environ["PATH"] = (
            directory_str
            + os.pathsep
            + current_path
        )

    # Python 3.8+ uses an explicit DLL search mechanism on Windows.
    if hasattr(os, "add_dll_directory"):
        try:
            handle = os.add_dll_directory(
                directory_str
            )

            _DLL_DIR_HANDLES.append(
                handle
            )

        except OSError:
            pass


def _configure_windows_cuda_dlls() -> None:
    """Register CUDA 12 and NVIDIA Python package DLL folders on Windows."""

    if os.name != "nt":
        return

    candidates = []

    # ---------------------------------------------------------------
    # Standard CUDA_PATH environment variable.
    # ---------------------------------------------------------------

    cuda_path = os.environ.get(
        "CUDA_PATH"
    )

    if cuda_path:
        candidates.append(
            Path(cuda_path) / "bin"
        )

    # ---------------------------------------------------------------
    # Version-specific variables such as CUDA_PATH_V12_8.
    # ---------------------------------------------------------------

    for key, value in os.environ.items():

        if (
            key.upper().startswith(
                "CUDA_PATH_V"
            )
            and value
        ):
            candidates.append(
                Path(value) / "bin"
            )

    # ---------------------------------------------------------------
    # Fallback:
    # discover installed CUDA 12.x toolkits automatically.
    # ---------------------------------------------------------------

    program_files = Path(
        os.environ.get(
            "ProgramFiles",
            r"C:\Program Files",
        )
    )

    cuda_root = (
        program_files
        / "NVIDIA GPU Computing Toolkit"
        / "CUDA"
    )

    if cuda_root.exists():

        for version_dir in sorted(
            cuda_root.glob("v12.*"),
            reverse=True,
        ):

            candidates.append(
                version_dir / "bin"
            )

    # ---------------------------------------------------------------
    # NVIDIA packages installed inside the current virtual env.
    #
    # This includes the cuDNN package you installed via pip.
    # ---------------------------------------------------------------

    nvidia_root = (
        Path(sys.prefix)
        / "Lib"
        / "site-packages"
        / "nvidia"
    )

    candidates.extend(
        [
            nvidia_root
            / "cudnn"
            / "bin",

            nvidia_root
            / "cublas"
            / "bin",

            nvidia_root
            / "cuda_runtime"
            / "bin",
        ]
    )

    # ---------------------------------------------------------------
    # Register every existing directory once.
    # ---------------------------------------------------------------

    seen = set()

    for directory in candidates:

        key = str(
            directory
        ).lower()

        if key in seen:
            continue

        seen.add(
            key
        )

        _register_windows_dll_directory(
            directory
        )


def _load_windows_dll(filename: str):
    """Load a DLL from one of the registered directories."""

    last_error = None

    # First try explicit paths.
    for directory in _DLL_DIR_PATHS:

        dll_path = (
            Path(directory)
            / filename
        )

        if not dll_path.exists():
            continue

        try:
            return ctypes.WinDLL(
                str(dll_path)
            )

        except OSError as exc:
            last_error = exc

    # Then let Windows resolve it.
    try:
        return ctypes.WinDLL(
            filename
        )

    except OSError as exc:

        if last_error is not None:
            raise last_error

        raise exc


def _ensure_cuda_runtime() -> None:
    """Validate native CUDA libraries required by faster-whisper."""

    if os.name != "nt":
        return

    required = (
        (
            "cublas64_12.dll",
            "cuBLAS CUDA 12",
        ),
        (
            "cudnn64_9.dll",
            "cuDNN 9",
        ),
    )

    for filename, label in required:

        try:

            dll = _load_windows_dll(
                filename
            )

            # Keep the loaded DLL alive.
            _LOADED_CUDA_DLLS.append(
                dll
            )

        except OSError as exc:

            searched = (
                "\n    ".join(
                    _DLL_DIR_PATHS
                )
                or "(nenhum diretÃ³rio registrado)"
            )

            raise RuntimeError(
                f"{label} nÃ£o pÃ´de ser carregado ({filename}).\n"
                f"DiretÃ³rios pesquisados:\n"
                f"    {searched}\n"
                "Verifique se CUDA 12 e cuDNN 9 estÃ£o instalados."
            ) from exc


# ---------------------------------------------------------------------------
# IMPORTANT:
#
# Configure native CUDA DLL paths BEFORE importing faster-whisper /
# CTranslate2.
# ---------------------------------------------------------------------------

_configure_windows_cuda_dlls()


from ..config import (
    LOCAL_OUTPUT_DIR,
    LOCAL_WHISPER_DEVICE,
    LOCAL_WHISPER_MODEL,
)


# ---------------------------------------------------------------------------
# Transcript cache
# ---------------------------------------------------------------------------

def _transcript_cache_path(
    media_path: str,
) -> Path:
    """Return the .srt cache path for a media file."""

    cache_dir = Path(
        LOCAL_OUTPUT_DIR
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return (
        cache_dir
        / (
            Path(media_path).stem
            + ".srt"
        )
    )


def _format_srt_timestamp(
    seconds: float,
) -> str:

    total_ms = max(
        0,
        int(
            round(
                seconds * 1000
            )
        ),
    )

    ms = (
        total_ms % 1000
    )

    total_s = (
        total_ms // 1000
    )

    s = (
        total_s % 60
    )

    total_m = (
        total_s // 60
    )

    m = (
        total_m % 60
    )

    h = (
        total_m // 60
    )

    return (
        f"{h:02d}:"
        f"{m:02d}:"
        f"{s:02d},"
        f"{ms:03d}"
    )


def _parse_srt_timestamp(
    value: str,
) -> float:

    match = re.fullmatch(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})",
        value.strip(),
    )

    if not match:
        raise ValueError(
            f"Invalid SRT timestamp: "
            f"{value!r}"
        )

    hours, minutes, seconds, millis = map(
        int,
        match.groups(),
    )

    return (
        hours * 3600
        + minutes * 60
        + seconds
        + (
            millis / 1000.0
        )
    )


def _write_srt_cache(
    media_path: str,
    transcript: Dict,
) -> Path:

    cache_path = (
        _transcript_cache_path(
            media_path
        )
    )

    lines = []

    for idx, segment in enumerate(
        transcript.get(
            "segments",
            [],
        ),
        start=1,
    ):

        start = (
            _format_srt_timestamp(
                float(
                    segment["start"]
                )
            )
        )

        end = (
            _format_srt_timestamp(
                float(
                    segment["end"]
                )
            )
        )

        text = (
            str(
                segment.get(
                    "text",
                    "",
                )
            )
            .strip()
            .replace(
                "\r",
                "",
            )
            .replace(
                "\n",
                " ",
            )
        )

        lines.append(
            str(idx)
        )

        lines.append(
            f"{start} --> {end}"
        )

        lines.append(
            text
        )

        lines.append(
            ""
        )

    cache_path.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )

    return cache_path


def _load_srt_cache(
    cache_path: Path,
) -> Dict:

    content = (
        cache_path.read_text(
            encoding="utf-8-sig"
        ).strip()
    )

    if not content:
        return {
            "duration": 0.0,
            "segments": [],
        }

    segments = []

    for block in re.split(
        r"\n\s*\n",
        content,
    ):

        lines = [
            line.strip(
                "\ufeff"
            )
            for line in block.splitlines()
            if line.strip()
        ]

        if not lines:
            continue

        if (
            "-->" not in lines[0]
            and len(lines) > 1
            and "-->" in lines[1]
        ):
            lines = lines[1:]

        if (
            not lines
            or "-->" not in lines[0]
        ):
            continue

        start_raw, end_raw = [
            part.strip()
            for part in lines[0].split(
                "-->",
                1,
            )
        ]

        text = (
            "\n".join(
                lines[1:]
            ).strip()
        )

        segments.append(
            {
                "start": (
                    _parse_srt_timestamp(
                        start_raw
                    )
                ),
                "end": (
                    _parse_srt_timestamp(
                        end_raw
                    )
                ),
                "text": text,
            }
        )

    duration = (
        segments[-1]["end"]
        if segments
        else 0.0
    )

    return {
        "duration": duration,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------

def _resolve_device() -> str:
    """Resolve CPU/CUDA and validate CUDA before loading the model."""

    configured = str(
        LOCAL_WHISPER_DEVICE
        or "auto"
    ).strip().lower()

    # ---------------------------------------------------------------
    # Explicit configuration.
    # ---------------------------------------------------------------

    if configured != "auto":

        if configured == "cuda":

            _ensure_cuda_runtime()

        return configured

    # ---------------------------------------------------------------
    # Auto mode.
    #
    # Prefer CTranslate2 detection because faster-whisper itself uses it.
    # ---------------------------------------------------------------

    try:

        import ctranslate2  # type: ignore

        if (
            ctranslate2.get_cuda_device_count()
            > 0
        ):

            try:

                _ensure_cuda_runtime()

                return "cuda"

            except RuntimeError as exc:

                print(
                    "[transcribe/local] "
                    "CUDA detectada, mas runtime indisponÃ­vel; "
                    "usando CPU. "
                    f"Motivo: {exc}",
                    flush=True,
                )

    except (
        ImportError,
        OSError,
        RuntimeError,
    ):
        pass

    return "cpu"


# ---------------------------------------------------------------------------
# Main transcription
# ---------------------------------------------------------------------------

def transcribe_local(
    media_path: str,
    language: Optional[str] = None,
) -> Dict:
    """Run faster-whisper on a local file path and cache result as .srt."""

    cache_path = (
        _transcript_cache_path(
            media_path
        )
    )

    # ---------------------------------------------------------------
    # Reuse cache.
    # ---------------------------------------------------------------

    if cache_path.exists():

        source_mtime = (
            os.path.getmtime(
                media_path
            )
        )

        cache_mtime = (
            cache_path.stat().st_mtime
        )

        if (
            cache_mtime
            >= source_mtime
        ):

            print(
                "[transcribe/local] "
                f"reusing cached transcript: "
                f"{cache_path}",
                flush=True,
            )

            cached = (
                _load_srt_cache(
                    cache_path
                )
            )

            # Treat empty/partial cache as invalid.
            if (
                not cached["segments"]
                or cached["duration"]
                <= 0.0
            ):

                print(
                    "[transcribe/local] "
                    "cache is empty/invalid, deleting: "
                    f"{cache_path}",
                    flush=True,
                )

                cache_path.unlink(
                    missing_ok=True
                )

            else:

                print(
                    "[transcribe/local] "
                    f"{len(cached['segments'])} "
                    "cached segments, "
                    f"{cached['duration']:.0f}s "
                    "of audio",
                    flush=True,
                )

                return cached

    # ---------------------------------------------------------------
    # Import faster-whisper after CUDA DLL configuration.
    # ---------------------------------------------------------------

    try:

        from faster_whisper import (
            WhisperModel,
        )  # type: ignore

    except ImportError as e:

        raise RuntimeError(
            "faster-whisper is required for --mode local. "
            "Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    # ---------------------------------------------------------------
    # Device.
    # ---------------------------------------------------------------

    device = (
        _resolve_device()
    )

    compute_type = (
        "float16"
        if device == "cuda"
        else "int8"
    )

    print(
        "[transcribe/local] "
        f"faster-whisper "
        f"model={LOCAL_WHISPER_MODEL} "
        f"device={device}",
        flush=True,
    )

    from ..config import (
        LOCAL_WHISPER_VAD_FILTER,
        LOCAL_WHISPER_VAD_PARAMETERS,
    )

    # ---------------------------------------------------------------
    # Load Whisper model.
    # ---------------------------------------------------------------

    try:

        model = WhisperModel(
            LOCAL_WHISPER_MODEL,
            device=device,
            compute_type=compute_type,
        )

    except Exception as exc:

        if device == "cuda":

            raise RuntimeError(
                "Falha ao inicializar o faster-whisper com CUDA. "
                "As DLLs foram registradas, mas o runtime NVIDIA "
                "ainda nÃ£o pÃ´de ser inicializado. "
                f"Erro original: {exc}"
            ) from exc

        raise

    # ---------------------------------------------------------------
    # Whisper options.
    # ---------------------------------------------------------------

    transcribe_kwargs = {
        "audio": media_path,
        "language": language,
        "beam_size": 5,
        "condition_on_previous_text": False,
    }

    if LOCAL_WHISPER_VAD_FILTER:

        transcribe_kwargs[
            "vad_filter"
        ] = True

        transcribe_kwargs[
            "vad_parameters"
        ] = (
            LOCAL_WHISPER_VAD_PARAMETERS
        )

    else:

        transcribe_kwargs[
            "vad_filter"
        ] = False

    # ---------------------------------------------------------------
    # Transcription.
    # ---------------------------------------------------------------

    segments_iter, info = (
        model.transcribe(
            **transcribe_kwargs
        )
    )

    segments = []

    for s in segments_iter:

        segments.append(
            {
                "start": float(
                    s.start
                ),
                "end": float(
                    s.end
                ),
                "text": (
                    s.text
                    or ""
                ).strip(),
            }
        )

    duration = (
        float(
            getattr(
                info,
                "duration",
                0.0,
            )
        )
        or (
            segments[-1]["end"]
            if segments
            else 0.0
        )
    )

    print(
        "[transcribe/local] "
        f"{len(segments)} segments, "
        f"{duration:.0f}s of audio",
        flush=True,
    )

    transcript = {
        "duration": duration,
        "segments": segments,
    }

    cache_path = (
        _write_srt_cache(
            media_path,
            transcript,
        )
    )

    print(
        "[transcribe/local] "
        f"wrote cache: "
        f"{cache_path}",
        flush=True,
    )

    return transcript

