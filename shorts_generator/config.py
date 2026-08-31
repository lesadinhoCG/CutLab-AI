import json
import os

from dotenv import load_dotenv


load_dotenv()


# ============================================================
# MuAPI
# ============================================================

MUAPI_API_KEY = os.getenv(
    "MUAPI_API_KEY",
    "",
).strip()

MUAPI_BASE_URL = os.getenv(
    "MUAPI_BASE_URL",
    "https://api.muapi.ai/api/v1",
).rstrip("/")

POLL_INTERVAL_SECONDS = float(
    os.getenv(
        "MUAPI_POLL_INTERVAL",
        "5",
    )
)

POLL_TIMEOUT_SECONDS = float(
    os.getenv(
        "MUAPI_POLL_TIMEOUT",
        "600",
    )
)


# ============================================================
# LLM provider
# ============================================================

LLM_PROVIDER = os.getenv(
    "LLM_PROVIDER",
    "openai",
).strip().lower()


# ============================================================
# OpenAI
# ============================================================

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    "",
).strip()

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-4o-mini",
).strip()


# ============================================================
# Gemini
# ============================================================

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    "",
).strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash",
).strip()


# ============================================================
# NVIDIA NIM / NVIDIA Build
# ============================================================

NVIDIA_API_KEY = os.getenv(
    "NVIDIA_API_KEY",
    "",
).strip()

NVIDIA_BASE_URL = (
    os.getenv(
        "NVIDIA_BASE_URL",
        "https://integrate.api.nvidia.com/v1",
    )
    .strip()
    .rstrip("/")
)

NVIDIA_MODEL = os.getenv(
    "NVIDIA_MODEL",
    "meta/llama-3.3-70b-instruct",
).strip()


# ============================================================
# Local Whisper
# ============================================================

LOCAL_WHISPER_MODEL = os.getenv(
    "LOCAL_WHISPER_MODEL",
    "base",
).strip()

LOCAL_WHISPER_DEVICE = os.getenv(
    "LOCAL_WHISPER_DEVICE",
    "auto",
).strip().lower()

LOCAL_OUTPUT_DIR = os.getenv(
    "LOCAL_OUTPUT_DIR",
    "output",
).strip()


# ============================================================
# VAD
# ============================================================

LOCAL_WHISPER_VAD_FILTER = (
    os.getenv(
        "LOCAL_WHISPER_VAD_FILTER",
        "false",
    )
    .strip()
    .lower()
    == "true"
)

_vad_params_env = os.getenv(
    "LOCAL_WHISPER_VAD_PARAMETERS",
    "",
)

if _vad_params_env:

    LOCAL_WHISPER_VAD_PARAMETERS = json.loads(
        _vad_params_env
    )

else:

    LOCAL_WHISPER_VAD_PARAMETERS = {
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
        "max_speech_duration_s": float("inf"),
        "min_silence_duration_ms": 2000,
        "speech_pad_ms": 400,
    }


# ============================================================
# Required key helpers
# ============================================================

def require_api_key() -> str:

    if not MUAPI_API_KEY:

        raise RuntimeError(
            "MUAPI_API_KEY is not set."
        )

    return MUAPI_API_KEY


def require_openai_key() -> str:

    if not OPENAI_API_KEY:

        raise RuntimeError(
            "OPENAI_API_KEY is not set. "
            "Add it to your .env file."
        )

    return OPENAI_API_KEY


def require_gemini_key() -> str:

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file."
        )

    return GEMINI_API_KEY


def require_nvidia_key() -> str:

    if not NVIDIA_API_KEY:

        raise RuntimeError(
            "NVIDIA_API_KEY is not set. "
            "Add it to your .env file."
        )

    return NVIDIA_API_KEY

