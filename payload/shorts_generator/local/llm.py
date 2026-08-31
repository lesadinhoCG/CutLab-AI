"""CutLab AI local LLM backends.

NVIDIA behavior:
- OpenAI-compatible NVIDIA NIM endpoint.
- 240 second request timeout by default.
- The model chosen in the CutLab frontend is always tried first.
- Temporary failures automatically fall back to other NVIDIA models.
- The outer retry/backoff logic in highlights.py remains unchanged.

Environment variables:
    NVIDIA_API_KEY
    NVIDIA_BASE_URL
    NVIDIA_MODEL
    NVIDIA_TIMEOUT_SECONDS
    NVIDIA_FALLBACK_MODELS

Example NVIDIA_FALLBACK_MODELS:
    nvidia/llama-3.3-nemotron-super-49b-v1.5,deepseek-ai/deepseek-v4-flash-0731
"""

from __future__ import annotations

import os
from typing import List

from .. import config


DEFAULT_NVIDIA_BASE_URL = (
    "https://integrate.api.nvidia.com/v1"
)

DEFAULT_NVIDIA_TIMEOUT_SECONDS = 240.0

DEFAULT_NVIDIA_FALLBACK_MODELS = (
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "deepseek-ai/deepseek-v4-flash-0731",
)


def _env(
    name: str,
    default: str = "",
) -> str:
    """Return a stripped environment/config value."""

    environment_value = os.getenv(
        name
    )

    if (
        environment_value is not None
        and str(
            environment_value
        ).strip()
    ):
        return str(
            environment_value
        ).strip()

    config_value = getattr(
        config,
        name,
        default,
    )

    return str(
        config_value
        or default
    ).strip()


def _require_value(
    name: str,
    helper_name: str,
) -> str:
    """Use a config helper when available, otherwise read the setting."""

    helper = getattr(
        config,
        helper_name,
        None,
    )

    if callable(helper):

        value = helper()

        if value:
            return str(
                value
            ).strip()

    value = _env(
        name
    )

    if not value:

        raise RuntimeError(
            f"{name} is required."
        )

    return value


def _nvidia_timeout() -> float:
    """Return the NVIDIA per-request timeout."""

    raw = _env(
        "NVIDIA_TIMEOUT_SECONDS",
        str(
            DEFAULT_NVIDIA_TIMEOUT_SECONDS
        ),
    )

    try:

        value = float(
            raw
        )

    except (
        TypeError,
        ValueError,
    ):

        value = (
            DEFAULT_NVIDIA_TIMEOUT_SECONDS
        )

    return max(
        30.0,
        min(
            600.0,
            value,
        ),
    )


def _nvidia_models() -> List[str]:
    """Return selected model first, followed by unique fallback models."""

    selected = _env(
        "NVIDIA_MODEL",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    )

    configured_fallbacks = _env(
        "NVIDIA_FALLBACK_MODELS"
    )

    if configured_fallbacks:

        fallback_models = [
            item.strip()
            for item in (
                configured_fallbacks
                .replace(
                    ";",
                    ",",
                )
                .split(",")
            )
            if item.strip()
        ]

    else:

        fallback_models = list(
            DEFAULT_NVIDIA_FALLBACK_MODELS
        )

    result: List[str] = []

    for model in [
        selected,
        *fallback_models,
    ]:

        if (
            model
            and model not in result
        ):

            result.append(
                model
            )

    return result


def _http_status_from_error(
    error: Exception,
):
    """Best-effort extraction of an HTTP status code from OpenAI errors."""

    status = getattr(
        error,
        "status_code",
        None,
    )

    if status is not None:

        try:
            return int(
                status
            )
        except Exception:
            pass

    response = getattr(
        error,
        "response",
        None,
    )

    status = getattr(
        response,
        "status_code",
        None,
    )

    if status is not None:

        try:
            return int(
                status
            )
        except Exception:
            pass

    return None


def _is_nvidia_fallback_error(
    error: Exception,
) -> bool:
    """Return True when another NVIDIA model is worth trying."""

    status = _http_status_from_error(
        error
    )

    # Authentication/permission problems will affect every model.
    if status in {
        401,
        403,
    }:
        return False

    # Model-specific 404/400, rate limiting and server trouble can often be
    # avoided by moving to another model.
    if (
        status is not None
        and (
            status == 400
            or status == 404
            or status == 408
            or status == 409
            or status == 429
            or status >= 500
        )
    ):
        return True

    text = (
        f"{type(error).__name__}: {error}"
        .lower()
    )

    markers = (
        "timeout",
        "timed out",
        "api timeout",
        "connection",
        "connecterror",
        "rate limit",
        "rate_limit",
        "too many requests",
        "429",
        "500",
        "502",
        "503",
        "504",
        "service unavailable",
        "temporarily unavailable",
        "high demand",
        "overloaded",
        "model not found",
        "invalid model",
    )

    return any(
        marker in text
        for marker in markers
    )


def _extract_chat_content(
    response,
) -> str:
    """Extract text from an OpenAI-compatible chat response."""

    try:

        content = (
            response
            .choices[0]
            .message
            .content
        )

    except Exception as exc:

        raise RuntimeError(
            "NVIDIA returned an invalid "
            "chat completion response."
        ) from exc

    return str(
        content
        or ""
    ).strip()


def call_openai_llm(
    prompt: str,
) -> str:
    """OpenAI Chat Completions backend."""

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai is required for local LLM calls. "
            "Install it with: pip install openai"
        ) from exc

    api_key = _require_value(
        "OPENAI_API_KEY",
        "require_openai_key",
    )

    model = _env(
        "OPENAI_MODEL",
        "gpt-4o-mini",
    )

    client = OpenAI(
        api_key=api_key,
        timeout=240.0,
        max_retries=0,
    )

    response = (
        client
        .chat
        .completions
        .create(
            model=model,
            temperature=0.2,
            max_tokens=8192,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Follow the requested output format exactly. "
                        "When JSON is requested, return only valid JSON "
                        "without markdown or additional commentary."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )
    )

    return _extract_chat_content(
        response
    )


def call_gemini_llm(
    prompt: str,
) -> str:
    """Gemini backend."""

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is required for LLM_PROVIDER=gemini. "
            "Install it with: pip install google-genai"
        ) from exc

    api_key = _require_value(
        "GEMINI_API_KEY",
        "require_gemini_key",
    )

    model = _env(
        "GEMINI_MODEL",
        "gemini-3.6-flash",
    )

    client = genai.Client(
        api_key=api_key
    )

    response = (
        client
        .models
        .generate_content(
            model=model,
            contents=prompt,
            config={
                "temperature": 0.2,
                "response_mime_type": (
                    "application/json"
                ),
                "max_output_tokens": 8192,
            },
        )
    )

    return str(
        response.text
        or ""
    ).strip()


def _call_one_nvidia_model(
    client,
    model: str,
    prompt: str,
) -> str:
    """Call one NVIDIA model."""

    common = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise assistant. "
                    "Follow the requested output format exactly. "
                    "When JSON is requested, return only valid JSON "
                    "without markdown or additional commentary."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 8192,
        "stream": False,
    }

    # Nemotron models can spend the whole response budget on hidden/visible
    # reasoning unless thinking is disabled. NVIDIA's OpenAI-compatible API
    # accepts chat_template_kwargs for these models.
    try:

        response = (
            client
            .chat
            .completions
            .create(
                **common,
                extra_body={
                    "chat_template_kwargs": {
                        "enable_thinking": False,
                    }
                },
            )
        )

    except Exception as exc:

        # Some NVIDIA-hosted models may reject model-specific template args.
        # Retry the SAME model once without that optional field only when the
        # error looks like a 400 caused by unsupported request parameters.
        status = _http_status_from_error(
            exc
        )

        text = str(
            exc
        ).lower()

        unsupported_template = (
            status == 400
            and (
                "chat_template" in text
                or "enable_thinking" in text
                or "extra_body" in text
                or "unknown field" in text
                or "unexpected" in text
            )
        )

        if not unsupported_template:
            raise

        print(
            "[llm/nvidia] model does not accept "
            "thinking controls; retrying request "
            "without chat_template_kwargs",
            flush=True,
        )

        response = (
            client
            .chat
            .completions
            .create(
                **common
            )
        )

    return _extract_chat_content(
        response
    )


def call_nvidia_llm(
    prompt: str,
) -> str:
    """NVIDIA NIM backend with timeout + automatic model fallback."""

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai is required for NVIDIA's OpenAI-compatible API. "
            "Install it with: pip install openai"
        ) from exc

    api_key = _require_value(
        "NVIDIA_API_KEY",
        "require_nvidia_key",
    )

    base_url = _env(
        "NVIDIA_BASE_URL",
        DEFAULT_NVIDIA_BASE_URL,
    )

    timeout = (
        _nvidia_timeout()
    )

    models = (
        _nvidia_models()
    )

    if not models:

        raise RuntimeError(
            "No NVIDIA models are configured."
        )

    print(
        "[llm/nvidia] "
        f"connecting to {base_url}",
        flush=True,
    )

    print(
        "[llm/nvidia] "
        f"timeout={timeout:.0f}s "
        f"models={len(models)}",
        flush=True,
    )

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        # highlights.py already owns the retry/backoff policy.
        # Avoid hidden SDK retries before moving to a fallback model.
        max_retries=0,
    )

    errors = []

    for index, model in enumerate(
        models,
        1,
    ):

        print(
            "[llm/nvidia] "
            f"model={model} "
            f"({index}/{len(models)})",
            flush=True,
        )

        print(
            "[llm/nvidia] "
            "sending request...",
            flush=True,
        )

        try:

            text = (
                _call_one_nvidia_model(
                    client,
                    model,
                    prompt,
                )
            )

            if not text:

                raise RuntimeError(
                    "NVIDIA returned an empty response."
                )

            print(
                "[llm/nvidia] "
                f"response received from {model}",
                flush=True,
            )

            if index > 1:

                print(
                    "[llm/nvidia] "
                    f"fallback succeeded: {model}",
                    flush=True,
                )

            return text

        except Exception as exc:

            error_text = (
                f"{type(exc).__name__}: {exc}"
            )

            errors.append(
                f"{model}: {error_text}"
            )

            print(
                "[llm/nvidia] "
                f"request failed on {model}: "
                f"{error_text}",
                flush=True,
            )

            if not (
                _is_nvidia_fallback_error(
                    exc
                )
            ):

                raise

            if index < len(models):

                next_model = (
                    models[index]
                )

                print(
                    "[llm/nvidia] "
                    "temporary/model-specific failure; "
                    f"switching fallback -> {next_model}",
                    flush=True,
                )

    raise RuntimeError(
        "All NVIDIA fallback models failed. "
        + " | ".join(
            errors
        )
    )


def call_local_llm(
    prompt: str,
) -> str:
    """Dispatch to the configured local LLM provider."""

    provider = _env(
        "LLM_PROVIDER",
        "openai",
    ).lower()

    print(
        "[llm/local] "
        f"provider={provider}",
        flush=True,
    )

    if provider == "openai":
        return call_openai_llm(
            prompt
        )

    if provider == "gemini":
        return call_gemini_llm(
            prompt
        )

    if provider == "nvidia":
        return call_nvidia_llm(
            prompt
        )

    raise RuntimeError(
        f"Unknown LLM_PROVIDER={provider!r}. "
        "Use 'openai', 'gemini', or 'nvidia'."
    )


# Compatibility aliases for local customizations/import styles.
call_llm = call_local_llm


