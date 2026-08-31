"""Find the most viral-worthy highlights in a transcript.

Long-video strategy:

1. Detect content type / density.
2. Videos >= 30 minutes are split into 30-minute chunks.
3. Chunks overlap by 60 seconds so moments near boundaries are not lost.
4. Each chunk produces up to 4 strong candidates.
5. Successful chunk results are cached locally.
6. Candidates are converted back to absolute video timestamps.
7. Overlapping / duplicate candidates are removed.
8. A final LLM pass globally ranks all candidates.
9. The requested Top N is returned for rendering.

Transient LLM errors such as 429/500/502/503/504 are retried automatically
with exponential backoff instead of aborting the entire long-video analysis.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import muapi
from .config import LOCAL_OUTPUT_DIR


LLMFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Content classification
# ---------------------------------------------------------------------------

CONTENT_TYPE_PROMPT = """Analyze this video transcript sample and classify the content type.

Choose one:
podcast, interview, tutorial, lecture, commentary, debate, vlog, other.

Also estimate content density:
low = mostly filler/chit-chat
medium = mixed
high = dense information/stories

Respond with JSON only:

{"content_type":"podcast","density":"high"}
"""


# ---------------------------------------------------------------------------
# Virality criteria
# ---------------------------------------------------------------------------

VIRALITY_CRITERIA = """
Virality signals to prioritize, ranked by impact:

1. HOOK MOMENTS
Statements that create immediate curiosity.

2. EMOTIONAL PEAKS
Surprise, laughter, anger, vulnerability, excitement or strong reactions.

3. OPINION BOMBS
Strong, polarizing or counter-intuitive statements.

4. REVELATION MOMENTS
Surprising facts, statistics, confessions or discoveries.

5. CONFLICT / TENSION
Disagreement, confrontation, controversy or problems being challenged.

6. QUOTABLE ONE-LINERS
Sentences that work well as standalone quotes.

7. STORY PEAKS
Climaxes, twists and payoffs in anecdotes.

8. PRACTICAL VALUE
Useful tips, insights or information viewers can immediately apply.
"""


HIGHLIGHT_SYSTEM_PROMPT = """You are an elite short-form video editor who has studied thousands of viral TikTok, Instagram Reels and YouTube Shorts.

{virality_criteria}

Content type: {content_type}
Content density: {density}

Your task is to identify the strongest standalone short-form video moments from this transcript.

Rules:

- Every highlight should begin with a strong hook whenever possible.
- Prefer clips that make sense without needing the rest of the video.
- Every highlight MUST be at least {minimum_highlight_seconds} seconds long.
- NEVER return a highlight shorter than {minimum_highlight_seconds} seconds.
- Prefer complete highlights around {minimum_highlight_seconds}-{ideal_highlight_max_seconds} seconds when the source supports it.
- A highlight may extend up to {maximum_highlight_seconds} seconds when additional context is required.
- Prefer complete ideas, stories, arguments or explanations over extremely short clips.
- NEVER sacrifice the natural ending of a sentence or idea just to hit a round duration.
- Prefer a slightly longer complete thought over a shorter clip that ends abruptly.
- Never intentionally cut in the middle of a sentence or thought.
- Avoid highlights that repeat the same idea.
- Score each candidate from 0-100 for short-form viral potential.
- Strong opinions, surprising revelations, valuable explanations and emotionally intense moments deserve priority.
- Do not invent statements that do not exist in the transcript.
- ALWAYS write the highlight title in Brazilian Portuguese.
- {num_clips_instruction}

For every highlight provide:

title
start_time
end_time
score
hook_sentence
virality_reason

Language rules:

- The "title" MUST always be written in Brazilian Portuguese.
- The title must sound natural, catchy and suitable for YouTube Shorts.
- Keep the title concise, preferably between 4 and 10 words.
- Do not translate names of people, brands, products or other proper nouns.
- Even if the source video is in another language, the title must be in Brazilian Portuguese.
- Do not use English titles unless the title consists only of a proper noun or an official product/brand name.
- hook_sentence and virality_reason may follow the transcript language, but title must always be Brazilian Portuguese.

Respond ONLY with valid JSON:

{{"highlights":[{{"title":"string","start_time":0.0,"end_time":60.0,"score":90,"hook_sentence":"string","virality_reason":"string"}}]}}
"""


# ---------------------------------------------------------------------------
# Long-video configuration
# ---------------------------------------------------------------------------

CHUNK_SIZE_SECONDS = 1800
LONG_VIDEO_THRESHOLD = 1800
CHUNK_OVERLAP_SECONDS = 60
PER_CHUNK_CANDIDATES = 4
GLOBAL_RERANK_POOL_MAX = 60

# CUTLAB_ASPECT_DURATION_V1
# CUTLAB_ROBUST_CHUNK_V3
_ASPECT_RATIO = os.getenv("CUTLAB_ASPECT_RATIO", "9:16").strip()
_LANDSCAPE_PROFILE = _ASPECT_RATIO == "16:9"
MIN_HIGHLIGHT_SECONDS = int(float(os.getenv(
    "CUTLAB_MIN_HIGHLIGHT_SECONDS",
    "300" if _LANDSCAPE_PROFILE else "30",
)))
IDEAL_HIGHLIGHT_MAX_SECONDS = 600 if _LANDSCAPE_PROFILE else 90
MAX_HIGHLIGHT_SECONDS = int(float(os.getenv(
    "CUTLAB_MAX_HIGHLIGHT_SECONDS",
    "1800" if _LANDSCAPE_PROFILE else "150",
)))

GPT_CALL_TIMEOUT_SECONDS = 300

MAX_HIGHLIGHT_API_ATTEMPTS = 6
MAX_GLOBAL_RERANK_ATTEMPTS = 6
MAX_CONTENT_TYPE_ATTEMPTS = 4

RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 80

# Increase this number if you later change the chunk-analysis prompt
# and want to invalidate all old chunk caches automatically.
CHUNK_CACHE_VERSION = (7 if _LANDSCAPE_PROFILE else 6)


# ---------------------------------------------------------------------------
# Final global ranking prompt
# ---------------------------------------------------------------------------

GLOBAL_RERANK_PROMPT = """You are the FINAL EDITOR for a long-form video clipping system.

Several independent 30-minute sections of the video have already been analyzed.
Each section produced candidate clips.

Your job is now to compare ALL candidates against each other and choose the
globally strongest {num_clips} clips from the entire video.

Selection priorities:

1. Extremely strong opening hook
2. Standalone value without needing missing context
3. Surprise, controversy, revelation, emotion or strong opinion
4. Clear and understandable story or argument
5. High potential for retention, comments and shares
6. Variety between the final clips
7. Avoid selecting two clips that communicate essentially the same idea

Important rules:

- Do NOT invent new clips.
- Do NOT change timestamps.
- Select ONLY candidate_id values provided below.
- Preserve each candidate title exactly as provided.
- Do not rewrite or translate titles into English.
- Final selected titles must remain in Brazilian Portuguese.
- Think globally. A clip with score 95 in one chunk is not automatically better
  than a clip with score 90 from another chunk.
- Prefer quality over artificial chronological distribution.
- Avoid near-duplicate topics when another strong candidate exists.
- Select up to {num_clips} candidates.
- If fewer than {num_clips} candidates are truly strong, it is acceptable to
  return fewer rather than selecting obvious filler.

Assign each selected candidate a GLOBAL score from 0-100.

Respond ONLY with valid JSON:

{{
  "ranking": [
    {{
      "candidate_id": 1,
      "global_score": 98,
      "reason": "Why this is one of the strongest clips in the full video"
    }}
  ]
}}

Candidates:

{candidates}
"""


# ---------------------------------------------------------------------------
# LLM backend
# ---------------------------------------------------------------------------

def call_muapi_llm(prompt: str) -> str:
    """Default LLM backend: MuAPI."""

    result = muapi.run(
        "gpt-5-mini",
        {"prompt": prompt},
        label="gpt-5-mini",
        timeout=GPT_CALL_TIMEOUT_SECONDS,
    )

    outputs = result.get("outputs")

    if (
        isinstance(outputs, list)
        and outputs
        and isinstance(outputs[0], str)
        and outputs[0].strip()
    ):
        return outputs[0]

    for key in (
        "output",
        "text",
        "response",
        "result",
        "content",
    ):
        value = result.get(key)

        if isinstance(value, str) and value.strip():
            return value

        if isinstance(value, dict):
            inner = value.get("text") or value.get("content")

            if isinstance(inner, str) and inner.strip():
                return inner

        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], str)
        ):
            return value[0]

    raise RuntimeError(
        f"Could not extract LLM text from response: {result}"
    )


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

def _is_transient_llm_error(error: object) -> bool:
    """Return True for temporary API errors worth retrying."""

    text = str(error).lower()

    markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "unavailable",
        "high demand",
        "resource_exhausted",
        "resource exhausted",
        "rate limit",
        "rate_limit",
        "too many requests",
        "temporarily",
        "temporary",
        "timeout",
        "timed out",
        "deadline exceeded",
        "internal error",
        "service unavailable",
    )

    return any(
        marker in text
        for marker in markers
    )


def _retry_wait_seconds(attempt: int) -> int:
    """Return exponential backoff delay."""

    return min(
        RETRY_BASE_SECONDS
        * (2 ** max(0, attempt - 1)),
        RETRY_MAX_SECONDS,
    )


def _sleep_before_retry(
    label: str,
    attempt: int,
    max_attempts: int,
    error: object,
) -> None:
    """Log and wait before retrying."""

    wait_seconds = (
        _retry_wait_seconds(
            attempt
        )
    )

    print(
        f"[highlights] {label} temporarily unavailable "
        f"(attempt {attempt}/{max_attempts}). "
        f"Retrying in {wait_seconds}s... "
        f"Error: {error}",
        flush=True,
    )

    time.sleep(
        wait_seconds
    )


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _parse_json_loose(raw: str) -> Dict:
    """Parse JSON even when the model adds markdown fences."""

    text = raw.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:
        return json.loads(
            text
        )

    except json.JSONDecodeError:

        start = text.find(
            "{"
        )

        end = text.rfind(
            "}"
        )

        if (
            start != -1
            and end != -1
            and end > start
        ):
            return json.loads(
                text[
                    start:
                    end + 1
                ]
            )

        raise


def _coerce_float(
    value: object,
    default: float = 0.0,
) -> float:

    try:
        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


def _coerce_int(
    value: object,
    default: int = 0,
) -> int:

    try:
        return int(
            float(
                value
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


# ---------------------------------------------------------------------------
# Highlight sanitization
# ---------------------------------------------------------------------------

def _sanitize_highlights(
    raw_highlights: object,
    duration: float,
) -> List[Dict]:

    if not isinstance(
        raw_highlights,
        list,
    ):
        return []

    max_end = (
        duration
        if duration > 0
        else float("inf")
    )

    cleaned: List[Dict] = []

    for item in raw_highlights:

        if not isinstance(
            item,
            dict,
        ):
            continue

        start = _coerce_float(
            item.get(
                "start_time"
            ),
            default=-1.0,
        )

        end = _coerce_float(
            item.get(
                "end_time"
            ),
            default=-1.0,
        )

        if (
            start < 0
            or end <= start
        ):
            continue

        if max_end != float("inf"):

            start = min(
                start,
                max_end,
            )

            end = min(
                end,
                max_end,
            )

            if end <= start:
                continue

        clip_duration = (
            end
            - start
        )

        if (
            clip_duration
            < MIN_HIGHLIGHT_SECONDS
        ):

            print(
                "[highlights] rejected short candidate: "
                f"{clip_duration:.1f}s "
                f"(minimum={MIN_HIGHLIGHT_SECONDS}s)",
                flush=True,
            )

            continue

        if (
            clip_duration
            > MAX_HIGHLIGHT_SECONDS
        ):

            print(
                "[highlights] rejected long candidate: "
                f"{clip_duration:.1f}s "
                f"(maximum={MAX_HIGHLIGHT_SECONDS}s)",
                flush=True,
            )

            continue

        cleaned.append(
            {
                "title": str(
                    item.get(
                        "title"
                    )
                    or "Destaque sem tÃ­tulo"
                ).strip(),

                "start_time": start,

                "end_time": end,

                "score": max(
                    0,
                    min(
                        100,
                        _coerce_int(
                            item.get(
                                "score"
                            ),
                            default=0,
                        ),
                    ),
                ),

                "hook_sentence": str(
                    item.get(
                        "hook_sentence"
                    )
                    or ""
                ).strip(),

                "virality_reason": str(
                    item.get(
                        "virality_reason"
                    )
                    or ""
                ).strip(),
            }
        )

    return cleaned


# ---------------------------------------------------------------------------
# Content classification
# ---------------------------------------------------------------------------

def detect_content_type(
    transcript: Dict,
    llm_fn: LLMFn = call_muapi_llm,
) -> Dict[str, str]:

    segments = transcript.get(
        "segments",
        [],
    )

    sample = " ".join(
        str(
            s.get(
                "text",
                "",
            )
        )
        for s in segments[:25]
    )[:3000]

    prompt = (
        f"{CONTENT_TYPE_PROMPT}"
        f"\n\nTranscript sample:\n"
        f"{sample}"
    )

    last_error: Optional[
        Exception
    ] = None

    for attempt in range(
        1,
        MAX_CONTENT_TYPE_ATTEMPTS + 1,
    ):

        try:

            raw = llm_fn(
                prompt
            )

            parsed = (
                _parse_json_loose(
                    raw
                )
            )

            content_type = str(
                parsed.get(
                    "content_type"
                )
                or "other"
            ).strip().lower()

            density = str(
                parsed.get(
                    "density"
                )
                or "medium"
            ).strip().lower()

            if density not in {
                "low",
                "medium",
                "high",
            }:
                density = "medium"

            return {
                "content_type": (
                    content_type
                ),
                "density": density,
            }

        except Exception as exc:

            last_error = exc

            if (
                attempt
                < MAX_CONTENT_TYPE_ATTEMPTS
                and _is_transient_llm_error(
                    exc
                )
            ):

                _sleep_before_retry(
                    "content classification",
                    attempt,
                    MAX_CONTENT_TYPE_ATTEMPTS,
                    exc,
                )

                continue

            if (
                attempt
                < MAX_CONTENT_TYPE_ATTEMPTS
            ):

                time.sleep(
                    2
                )

                continue

    if last_error is not None:

        print(
            "[highlights] content classification failed; "
            "using fallback other/medium. "
            f"Error: {last_error}",
            flush=True,
        )

    return {
        "content_type": "other",
        "density": "medium",
    }


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------

def build_transcript_text(
    transcript: Dict,
) -> str:

    segments = transcript.get(
        "segments",
        [],
    )

    return "\n".join(
        f"[{float(s['start']):.1f}s] "
        f"{str(s.get('text', '')).strip()}"
        for s in segments
    )


def chunk_transcript(
    transcript: Dict,
) -> List[Dict]:
    """Split transcript into overlapping 30-minute windows."""

    segments = transcript.get(
        "segments",
        [],
    )

    duration = _coerce_float(
        transcript.get(
            "duration"
        ),
        default=(
            float(
                segments[-1].get(
                    "end",
                    0,
                )
            )
            if segments
            else 0
        ),
    )

    chunks: List[Dict] = []

    if duration <= 0:
        return chunks

    step = (
        CHUNK_SIZE_SECONDS
        - CHUNK_OVERLAP_SECONDS
    )

    start = 0.0
    chunk_index = 0

    while start < duration:

        end = min(
            start
            + CHUNK_SIZE_SECONDS,
            duration,
        )

        local_segments: List[
            Dict
        ] = []

        for segment in segments:

            original_start = (
                _coerce_float(
                    segment.get(
                        "start"
                    ),
                    0.0,
                )
            )

            original_end = (
                _coerce_float(
                    segment.get(
                        "end"
                    ),
                    original_start,
                )
            )

            if (
                original_end <= start
                or original_start >= end
            ):
                continue

            local_start = max(
                0.0,
                original_start
                - start,
            )

            local_end = min(
                end - start,
                original_end - start,
            )

            if (
                local_end
                <= local_start
            ):
                continue

            local_segment = dict(
                segment
            )

            local_segment[
                "start"
            ] = local_start

            local_segment[
                "end"
            ] = local_end

            local_segments.append(
                local_segment
            )

        if local_segments:

            chunk = dict(
                transcript
            )

            chunk[
                "segments"
            ] = local_segments

            chunk[
                "duration"
            ] = end - start

            chunk[
                "_offset"
            ] = start

            chunk[
                "_chunk_index"
            ] = chunk_index

            chunks.append(
                chunk
            )

        if end >= duration:
            break

        start += step
        chunk_index += 1

    return chunks


# ---------------------------------------------------------------------------
# Per-chunk cache
# ---------------------------------------------------------------------------

def _chunk_cache_dir() -> Path:

    path = (
        Path(
            LOCAL_OUTPUT_DIR
        )
        / ".highlight_cache"
    )

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def _chunk_cache_key(
    transcript_text: str,
    content_info: Dict,
    duration: float,
    num_clips: int,
) -> str:

    payload = {
        "version": (
            CHUNK_CACHE_VERSION
        ),
        "duration": round(
            float(
                duration
            ),
            3,
        ),
        "num_clips": int(
            num_clips
        ),
        "content_type": (
            content_info.get(
                "content_type",
                "other",
            )
        ),
        "density": (
            content_info.get(
                "density",
                "medium",
            )
        ),
        "transcript": (
            transcript_text
        ),
    }

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode(
        "utf-8"
    )

    return hashlib.sha256(
        raw
    ).hexdigest()


def _load_chunk_cache(
    cache_key: str,
    duration: float,
) -> Optional[Dict]:

    path = (
        _chunk_cache_dir()
        / f"{cache_key}.json"
    )

    if not path.exists():
        return None

    try:

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        highlights = (
            _sanitize_highlights(
                data.get(
                    "highlights"
                ),
                duration=duration,
            )
        )

        if not highlights:
            return None

        return {
            "highlights": highlights
        }

    except Exception:
        return None


def _save_chunk_cache(
    cache_key: str,
    result: Dict,
) -> None:

    path = (
        _chunk_cache_dir()
        / f"{cache_key}.json"
    )

    temp_path = (
        path.with_suffix(
            ".json.tmp"
        )
    )

    payload = {
        "version": (
            CHUNK_CACHE_VERSION
        ),
        "highlights": (
            result.get(
                "highlights",
                [],
            )
        ),
    }

    try:

        temp_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temp_path.replace(
            path
        )

    except Exception as exc:

        print(
            "[highlights] warning: "
            "could not write chunk cache: "
            f"{exc}",
            flush=True,
        )

        try:
            temp_path.unlink(
                missing_ok=True
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Highlight request
# ---------------------------------------------------------------------------

def call_highlight_api(
    transcript_text: str,
    content_info: Dict,
    duration: float,
    num_clips: int,
    is_chunk: bool = False,
    llm_fn: LLMFn = call_muapi_llm,
    max_attempts: Optional[int] = None,
) -> Dict:

    if is_chunk:

        requested = max(
            1,
            min(
                num_clips,
                PER_CHUNK_CANDIDATES,
            ),
        )

        num_instruction = (
            f"Generate up to {requested} genuinely strong highlights. "
            "Do not add weak filler merely to reach the number."
        )

    else:

        target = max(
            num_clips * 2,
            5,
        )

        natural_max = max(
            3,
            int(
                duration / max(90, MIN_HIGHLIGHT_SECONDS)
            ),
        )

        requested = min(
            target,
            natural_max,
            20,
        )

        num_instruction = (
            f"Generate up to {requested} strong highlights. "
            f"The final system will keep at most {num_clips}."
        )

    system = (
        HIGHLIGHT_SYSTEM_PROMPT.format(
            virality_criteria=(
                VIRALITY_CRITERIA
            ),
            content_type=(
                content_info.get(
                    "content_type",
                    "other",
                )
            ),
            density=(
                content_info.get(
                    "density",
                    "medium",
                )
            ),
            minimum_highlight_seconds=(MIN_HIGHLIGHT_SECONDS),
            ideal_highlight_max_seconds=(IDEAL_HIGHLIGHT_MAX_SECONDS),
            maximum_highlight_seconds=(MAX_HIGHLIGHT_SECONDS),
            num_clips_instruction=(
                num_instruction
            ),
        )
    )

    base_prompt = (
        f"{system}"
        f"\n\nTranscript:\n"
        f"{transcript_text}"
    )

    prompt = (
        base_prompt
    )

    last_error = (
        "unknown"
    )

    attempts_limit = (
        MAX_HIGHLIGHT_API_ATTEMPTS
        if max_attempts is None
        else max(
            1,
            min(
                int(max_attempts),
                MAX_HIGHLIGHT_API_ATTEMPTS,
            ),
        )
    )

    for attempt in range(
        1,
        attempts_limit
        + 1,
    ):

        try:

            raw = llm_fn(
                prompt
            )

            parsed = (
                _parse_json_loose(
                    raw
                )
            )

            highlights = (
                _sanitize_highlights(
                    parsed.get(
                        "highlights"
                    ),
                    duration=duration,
                )
            )

            if highlights:

                return {
                    "highlights": (
                        highlights
                    )
                }

            last_error = (
                "no valid highlights "
                "in response"
            )

            if (
                attempt
                < attempts_limit
            ):

                print(
                    "[highlights] model returned "
                    "no usable highlights "
                    f"(attempt {attempt}/"
                    f"{attempts_limit}); "
                    "retrying in 2s...",
                    flush=True,
                )

                time.sleep(
                    2
                )

        except Exception as exc:

            last_error = str(
                exc
            )

            if (
                attempt
                >= attempts_limit
            ):
                break

            if (
                _is_transient_llm_error(
                    exc
                )
            ):

                _sleep_before_retry(
                    "LLM",
                    attempt,
                    attempts_limit,
                    exc,
                )

            else:

                print(
                    "[highlights] invalid model output "
                    f"(attempt {attempt}/"
                    f"{attempts_limit}); "
                    "retrying in 2s...",
                    flush=True,
                )

                time.sleep(
                    2
                )

        prompt = (
            base_prompt
            + "\n\nIMPORTANT: "
            "Return ONLY valid JSON "
            "with a top-level "
            "'highlights' array. "
            "No markdown and no commentary."
        )

    raise RuntimeError(
        "Highlight generator failed after "
        f"{attempts_limit} "
        f"attempts: {last_error}"
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedupe_highlights(
    highlights: List[Dict],
) -> List[Dict]:

    highlights = sorted(
        highlights,
        key=lambda x: int(
            x.get(
                "score",
                0,
            )
        ),
        reverse=True,
    )

    kept: List[Dict] = []

    for highlight in highlights:

        h_start = float(
            highlight[
                "start_time"
            ]
        )

        h_end = float(
            highlight[
                "end_time"
            ]
        )

        h_duration = (
            h_end
            - h_start
        )

        if h_duration <= 0:
            continue

        overlapping = False

        for existing in kept:

            e_start = float(
                existing[
                    "start_time"
                ]
            )

            e_end = float(
                existing[
                    "end_time"
                ]
            )

            latest_start = max(
                h_start,
                e_start,
            )

            earliest_end = min(
                h_end,
                e_end,
            )

            overlap = (
                earliest_end
                - latest_start
            )

            if overlap <= 0:
                continue

            existing_duration = (
                e_end
                - e_start
            )

            smaller_duration = min(
                h_duration,
                existing_duration,
            )

            if (
                overlap
                >= 0.5
                * smaller_duration
            ):

                overlapping = True
                break

        if not overlapping:
            kept.append(
                highlight
            )

    return kept


# ---------------------------------------------------------------------------
# Global candidate pool
# ---------------------------------------------------------------------------

def _prepare_global_pool(
    highlights: List[Dict],
) -> List[Dict]:

    ranked = sorted(
        highlights,
        key=lambda h: int(
            h.get(
                "score",
                0,
            )
        ),
        reverse=True,
    )

    return ranked[
        :GLOBAL_RERANK_POOL_MAX
    ]


# ---------------------------------------------------------------------------
# Global ranking
# ---------------------------------------------------------------------------

def rerank_global_highlights(
    highlights: List[Dict],
    num_clips: int,
    llm_fn: LLMFn,
) -> List[Dict]:

    if not highlights:
        return []

    if (
        len(highlights)
        <= num_clips
    ):

        ranked = [
            dict(h)
            for h in sorted(
                highlights,
                key=lambda h: int(
                    h.get(
                        "score",
                        0,
                    )
                ),
                reverse=True,
            )
        ]

        for index, highlight in enumerate(
            ranked,
            1,
        ):

            chunk_score = int(
                highlight.get(
                    "score",
                    0,
                )
            )

            highlight[
                "chunk_score"
            ] = chunk_score

            highlight[
                "global_score"
            ] = chunk_score

            highlight[
                "global_rank"
            ] = index

        return ranked

    pool = (
        _prepare_global_pool(
            highlights
        )
    )

    candidate_payload = []

    id_to_highlight: Dict[
        int,
        Dict,
    ] = {}

    for candidate_id, highlight in enumerate(
        pool,
        1,
    ):

        id_to_highlight[
            candidate_id
        ] = highlight

        candidate_payload.append(
            {
                "candidate_id": (
                    candidate_id
                ),

                "source_chunk": (
                    highlight.get(
                        "_chunk_index"
                    )
                ),

                "start_time": round(
                    float(
                        highlight[
                            "start_time"
                        ]
                    ),
                    1,
                ),

                "end_time": round(
                    float(
                        highlight[
                            "end_time"
                        ]
                    ),
                    1,
                ),

                "chunk_score": int(
                    highlight.get(
                        "score",
                        0,
                    )
                ),

                "title": (
                    highlight.get(
                        "title",
                        "",
                    )
                ),

                "hook_sentence": (
                    highlight.get(
                        "hook_sentence",
                        "",
                    )
                ),

                "virality_reason": (
                    highlight.get(
                        "virality_reason",
                        "",
                    )
                ),
            }
        )

    prompt = (
        GLOBAL_RERANK_PROMPT.format(
            num_clips=min(
                num_clips,
                len(
                    pool
                ),
            ),

            candidates=json.dumps(
                candidate_payload,
                ensure_ascii=False,
                indent=2,
            ),
        )
    )

    last_error = (
        "unknown"
    )

    for attempt in range(
        1,
        MAX_GLOBAL_RERANK_ATTEMPTS
        + 1,
    ):

        try:

            print(
                "[highlights] global ranking "
                f"attempt {attempt}/"
                f"{MAX_GLOBAL_RERANK_ATTEMPTS}",
                flush=True,
            )

            raw = llm_fn(
                prompt
            )

            parsed = (
                _parse_json_loose(
                    raw
                )
            )

            ranking = (
                parsed.get(
                    "ranking"
                )
            )

            if not isinstance(
                ranking,
                list,
            ):
                raise ValueError(
                    "missing ranking array"
                )

            selected: List[
                Dict
            ] = []

            selected_ids = set()

            for item in ranking:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                candidate_id = (
                    _coerce_int(
                        item.get(
                            "candidate_id"
                        ),
                        default=-1,
                    )
                )

                if (
                    candidate_id
                    not in id_to_highlight
                ):
                    continue

                if (
                    candidate_id
                    in selected_ids
                ):
                    continue

                original = (
                    id_to_highlight[
                        candidate_id
                    ]
                )

                chosen = dict(
                    original
                )

                chunk_score = int(
                    chosen.get(
                        "score",
                        0,
                    )
                )

                global_score = max(
                    0,
                    min(
                        100,
                        _coerce_int(
                            item.get(
                                "global_score"
                            ),
                            default=(
                                chunk_score
                            ),
                        ),
                    ),
                )

                chosen[
                    "chunk_score"
                ] = chunk_score

                chosen[
                    "global_score"
                ] = global_score

                chosen[
                    "score"
                ] = global_score

                chosen[
                    "global_reason"
                ] = str(
                    item.get(
                        "reason"
                    )
                    or ""
                ).strip()

                selected.append(
                    chosen
                )

                selected_ids.add(
                    candidate_id
                )

                if (
                    len(selected)
                    >= num_clips
                ):
                    break

            if not selected:

                raise ValueError(
                    "global ranking returned "
                    "no usable candidate IDs"
                )

            selected.sort(
                key=lambda h: int(
                    h.get(
                        "global_score",
                        h.get(
                            "score",
                            0,
                        ),
                    )
                ),
                reverse=True,
            )

            for index, highlight in enumerate(
                selected,
                1,
            ):

                highlight[
                    "global_rank"
                ] = index

            print(
                "[highlights] global ranking "
                f"selected {len(selected)} "
                f"of {len(pool)} candidates",
                flush=True,
            )

            return selected[
                :num_clips
            ]

        except Exception as exc:

            last_error = str(
                exc
            )

            if (
                attempt
                >= MAX_GLOBAL_RERANK_ATTEMPTS
            ):
                break

            if (
                _is_transient_llm_error(
                    exc
                )
            ):

                _sleep_before_retry(
                    "global ranking",
                    attempt,
                    MAX_GLOBAL_RERANK_ATTEMPTS,
                    exc,
                )

            else:

                print(
                    "[highlights] invalid global ranking "
                    f"output (attempt {attempt}/"
                    f"{MAX_GLOBAL_RERANK_ATTEMPTS}); "
                    "retrying in 2s...",
                    flush=True,
                )

                time.sleep(
                    2
                )

    print(
        "[highlights] global ranking failed "
        f"({last_error}); "
        "falling back to candidate scores",
        flush=True,
    )

    fallback = [
        dict(h)
        for h in sorted(
            pool,
            key=lambda h: int(
                h.get(
                    "score",
                    0,
                )
            ),
            reverse=True,
        )[
            :num_clips
        ]
    ]

    for index, highlight in enumerate(
        fallback,
        1,
    ):

        chunk_score = int(
            highlight.get(
                "score",
                0,
            )
        )

        highlight[
            "chunk_score"
        ] = chunk_score

        highlight[
            "global_score"
        ] = chunk_score

        highlight[
            "global_rank"
        ] = index

    return fallback



# ---------------------------------------------------------------------------
# Semantic boundary refinement
# CUTLAB_BOUNDARY_EDITOR_V1
# ---------------------------------------------------------------------------

BOUNDARY_CACHE_VERSION = 1
BOUNDARY_REVIEW_MAX_ATTEMPTS = 2
BOUNDARY_MIN_PAUSE_SECONDS = 0.45

BOUNDARY_REVIEW_ENABLED = (
    os.getenv(
        "CUTLAB_BOUNDARY_REVIEW",
        "1",
    ).strip().lower()
    not in {
        "0",
        "false",
        "no",
        "off",
    }
)

BOUNDARY_LOOKBACK_SECONDS = float(
    os.getenv(
        "CUTLAB_BOUNDARY_LOOKBACK_SECONDS",
        "30" if _LANDSCAPE_PROFILE else "15",
    )
)

BOUNDARY_LOOKAHEAD_SECONDS = float(
    os.getenv(
        "CUTLAB_BOUNDARY_LOOKAHEAD_SECONDS",
        "120" if _LANDSCAPE_PROFILE else "45",
    )
)

BOUNDARY_EXTENSION_SECONDS = float(
    os.getenv(
        "CUTLAB_BOUNDARY_EXTENSION_SECONDS",
        "120" if _LANDSCAPE_PROFILE else "30",
    )
)


BOUNDARY_REVIEW_PROMPT = """You are the BOUNDARY EDITOR of a professional video clipping system.

The topic and viral value of this clip have ALREADY been selected. Do NOT
replace the subject and do NOT search for another highlight.

Your only job is to choose the most natural START and END for this exact clip.

Original candidate:
- title: {title}
- start_time: {original_start:.3f}
- end_time: {original_end:.3f}
- original_duration: {original_duration:.3f}s

A local transcript/pause analyzer suggests:
- start_time: {local_start:.3f}
- end_time: {local_end:.3f}

Allowed ranges:
- start_time: {start_low:.3f} to {start_high:.3f}
- end_time: {end_low:.3f} to {end_high:.3f}
- minimum final duration: {minimum_duration:.1f}s
- preferred maximum duration: {soft_max:.1f}s
- absolute hard maximum duration: {hard_max:.1f}s

Rules:
1. The clip must begin at a natural sentence/thought boundary.
2. The clip must end AFTER the speaker completes the relevant thought.
3. NEVER end in the middle of a sentence, answer, explanation, list or causal chain.
4. NEVER stop immediately after a continuation such as "porque", "mas",
   "entÃ£o", "por exemplo", "ou seja", "and", "but", "because", "so" or similar.
5. If a complete conclusion occurs shortly AFTER the original end_time,
   EXTEND the clip to that conclusion.
6. If a clearly better natural ending occurs shortly BEFORE the original
   end_time and it preserves the full idea, shortening is allowed.
7. A small pause alone is not enough if the next sentence obviously continues
   the same reasoning.
8. Prefer semantic completeness over hitting a round number of seconds.
9. Do not exceed the hard maximum.
10. Use only timestamps that are supported by the transcript context below.

Useful local pause/end candidates:
{pause_candidates}

Transcript context:
{context}

Return ONLY valid JSON:
{{
  "start_time": 0.0,
  "end_time": 0.0,
  "start_complete": true,
  "end_complete": true,
  "confidence": 0,
  "reason": "brief explanation"
}}
"""


def _boundary_provider_signature() -> str:
    provider = os.getenv(
        "CUTLAB_ORIGINAL_LLM_PROVIDER",
        os.getenv(
            "LLM_PROVIDER",
            "",
        ),
    )

    model = (
        os.getenv("NVIDIA_MODEL")
        or os.getenv("GEMINI_MODEL")
        or os.getenv("GROQ_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("LMSTUDIO_MODEL")
        or ""
    )

    return f"{provider}|{model}"


def _boundary_cache_dir() -> Path:
    path = (
        Path(
            LOCAL_OUTPUT_DIR
        )
        / ".highlight_cache"
        / "boundary"
    )

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def _boundary_cache_key(
    highlight: Dict,
    context: str,
) -> str:
    payload = {
        "version": BOUNDARY_CACHE_VERSION,
        "aspect": _ASPECT_RATIO,
        "provider": _boundary_provider_signature(),
        "title": str(
            highlight.get(
                "title",
                "",
            )
        ),
        "start": round(
            _coerce_float(
                highlight.get(
                    "start_time"
                )
            ),
            3,
        ),
        "end": round(
            _coerce_float(
                highlight.get(
                    "end_time"
                )
            ),
            3,
        ),
        "context": context,
        "lookback": BOUNDARY_LOOKBACK_SECONDS,
        "lookahead": BOUNDARY_LOOKAHEAD_SECONDS,
        "extension": BOUNDARY_EXTENSION_SECONDS,
    }

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode(
        "utf-8"
    )

    return hashlib.sha256(
        raw
    ).hexdigest()


def _load_boundary_cache(
    key: str,
) -> Optional[Dict]:
    path = (
        _boundary_cache_dir()
        / f"{key}.json"
    )

    if not path.exists():
        return None

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        if (
            int(
                data.get(
                    "version",
                    -1,
                )
            )
            != BOUNDARY_CACHE_VERSION
        ):
            return None

        result = data.get(
            "result"
        )

        return (
            result
            if isinstance(
                result,
                dict,
            )
            else None
        )

    except Exception:
        return None


def _save_boundary_cache(
    key: str,
    result: Dict,
) -> None:
    path = (
        _boundary_cache_dir()
        / f"{key}.json"
    )

    temp = path.with_suffix(
        ".json.tmp"
    )

    payload = {
        "version": BOUNDARY_CACHE_VERSION,
        "result": result,
    }

    try:
        temp.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temp.replace(
            path
        )

    except Exception as exc:
        print(
            "[boundary] warning: "
            "could not save boundary cache: "
            f"{exc}",
            flush=True,
        )

        try:
            temp.unlink(
                missing_ok=True
            )
        except Exception:
            pass


def _segment_start(
    segment: Dict,
) -> float:
    return _coerce_float(
        segment.get(
            "start"
        ),
        0.0,
    )


def _segment_end(
    segment: Dict,
) -> float:
    start = _segment_start(
        segment
    )

    return max(
        start,
        _coerce_float(
            segment.get(
                "end"
            ),
            start,
        ),
    )


def _segment_text(
    segment: Dict,
) -> str:
    return str(
        segment.get(
            "text",
            "",
        )
    ).strip()


def _sentence_looks_complete(
    text: str,
) -> bool:
    value = str(
        text
        or ""
    ).strip()

    if not value:
        return False

    continuation = re.compile(
        r"(?:"
        r"porque|mas|ent[aÃ£]o|por exemplo|ou seja|"
        r"e|ou|que|quando|se|"
        r"because|but|so|then|for example|that|when|if|and|or|"
        r"porque|pero|entonces|por ejemplo|y|o|que|cuando|si"
        r")"
        r"[\s,:;\-â€“â€”â€¦]*$",
        re.IGNORECASE,
    )

    if continuation.search(
        value
    ):
        return False

    value = value.rstrip(
        "\"'â€â€™)]}"
    )

    return value.endswith(
        (
            ".",
            "!",
            "?",
            "â€¦",
        )
    )


def _pause_after_segment(
    segments: List[Dict],
    index: int,
    video_duration: float,
) -> float:
    current_end = _segment_end(
        segments[
            index
        ]
    )

    if (
        index + 1
        < len(
            segments
        )
    ):
        next_start = _segment_start(
            segments[
                index + 1
            ]
        )

        return max(
            0.0,
            next_start
            - current_end,
        )

    if video_duration > 0:
        return max(
            0.0,
            video_duration
            - current_end,
        )

    return 0.0


def _pause_cut_time(
    segments: List[Dict],
    index: int,
    video_duration: float,
) -> float:
    end = _segment_end(
        segments[
            index
        ]
    )

    pause = _pause_after_segment(
        segments,
        index,
        video_duration,
    )

    if pause <= 0:
        return end

    return min(
        video_duration
        if video_duration > 0
        else end + 0.35,
        end
        + min(
            0.35,
            pause * 0.5,
        ),
    )


def _end_boundary_score(
    segments: List[Dict],
    index: int,
    original_end: float,
    soft_end: float,
    video_duration: float,
) -> float:
    text = _segment_text(
        segments[
            index
        ]
    )

    cut_time = _pause_cut_time(
        segments,
        index,
        video_duration,
    )

    pause = _pause_after_segment(
        segments,
        index,
        video_duration,
    )

    score = 0.0

    if _sentence_looks_complete(
        text
    ):
        score += 45.0
    else:
        score -= 18.0

    if pause >= 1.0:
        score += 28.0
    elif pause >= BOUNDARY_MIN_PAUSE_SECONDS:
        score += 18.0
    elif pause >= 0.20:
        score += 7.0

    distance = abs(
        cut_time
        - original_end
    )

    score -= min(
        30.0,
        distance * 0.65,
    )

    if (
        cut_time
        >= original_end
        and cut_time
        <= original_end + 20.0
    ):
        score += 8.0

    if cut_time > soft_end:
        score -= (
            cut_time
            - soft_end
        ) * 0.30

    return score


def _start_boundary_score(
    segments: List[Dict],
    index: int,
    original_start: float,
) -> float:
    start = _segment_start(
        segments[
            index
        ]
    )

    score = -abs(
        start
        - original_start
    ) * 0.65

    if index == 0:
        score += 15.0
    else:
        previous_end = _segment_end(
            segments[
                index - 1
            ]
        )

        pause = max(
            0.0,
            start - previous_end,
        )

        if pause >= 1.0:
            score += 24.0
        elif pause >= BOUNDARY_MIN_PAUSE_SECONDS:
            score += 16.0
        elif pause >= 0.20:
            score += 6.0

        previous_text = _segment_text(
            segments[
                index - 1
            ]
        )

        if _sentence_looks_complete(
            previous_text
        ):
            score += 8.0

    return score


def _boundary_context_segments(
    transcript: Dict,
    start: float,
    end: float,
) -> List[Dict]:
    segments = transcript.get(
        "segments",
        [],
    )

    if not isinstance(
        segments,
        list,
    ):
        return []

    selected: List[
        Dict
    ] = []

    for segment in segments:
        if not isinstance(
            segment,
            dict,
        ):
            continue

        seg_start = _segment_start(
            segment
        )

        seg_end = _segment_end(
            segment
        )

        if (
            seg_end < start
            or seg_start > end
        ):
            continue

        selected.append(
            segment
        )

    return selected


def _boundary_context_text(
    segments: List[Dict],
    max_chars: int = 14000,
) -> str:
    lines: List[
        str
    ] = []

    for segment in segments:
        lines.append(
            f"[{_segment_start(segment):.2f}s"
            f" - {_segment_end(segment):.2f}s] "
            f"{_segment_text(segment)}"
        )

    text = "\n".join(
        lines
    )

    if len(text) > max_chars:
        text = text[
            :max_chars
        ]

    return text


def _local_boundary_refine(
    highlight: Dict,
    transcript: Dict,
) -> Dict:
    segments = transcript.get(
        "segments",
        [],
    )

    if not isinstance(
        segments,
        list,
    ) or not segments:
        return {
            "start_time": _coerce_float(
                highlight.get(
                    "start_time"
                )
            ),
            "end_time": _coerce_float(
                highlight.get(
                    "end_time"
                )
            ),
            "pause_candidates": [],
        }

    original_start = _coerce_float(
        highlight.get(
            "start_time"
        )
    )

    original_end = _coerce_float(
        highlight.get(
            "end_time"
        )
    )

    video_duration = _coerce_float(
        transcript.get(
            "duration"
        ),
        _segment_end(
            segments[
                -1
            ]
        ),
    )

    start_low = max(
        0.0,
        original_start
        - BOUNDARY_LOOKBACK_SECONDS,
    )

    start_high = min(
        original_end - 1.0,
        original_start + 8.0,
    )

    start_candidates = []

    for index, segment in enumerate(
        segments
    ):
        seg_start = _segment_start(
            segment
        )

        if (
            seg_start < start_low
            or seg_start > start_high
        ):
            continue

        start_candidates.append(
            (
                _start_boundary_score(
                    segments,
                    index,
                    original_start,
                ),
                seg_start,
                index,
            )
        )

    local_start = original_start

    for index, segment in enumerate(
        segments
    ):
        seg_start = _segment_start(
            segment
        )

        seg_end = _segment_end(
            segment
        )

        if (
            seg_start
            <= original_start
            <= seg_end
            and seg_start
            >= start_low
        ):
            start_candidates.append(
                (
                    20.0
                    - abs(
                        seg_start
                        - original_start
                    ) * 0.25,
                    seg_start,
                    index,
                )
            )
            break

    if start_candidates:
        start_candidates.sort(
            key=lambda item: item[
                0
            ],
            reverse=True,
        )

        local_start = float(
            start_candidates[
                0
            ][1]
        )

    hard_max = (
        float(
            MAX_HIGHLIGHT_SECONDS
        )
        + BOUNDARY_EXTENSION_SECONDS
    )

    end_low = max(
        local_start
        + float(
            MIN_HIGHLIGHT_SECONDS
        ),
        original_end - 12.0,
    )

    end_high = min(
        video_duration,
        original_end
        + BOUNDARY_LOOKAHEAD_SECONDS,
        local_start
        + hard_max,
    )

    soft_end = (
        local_start
        + float(
            MAX_HIGHLIGHT_SECONDS
        )
    )

    end_candidates = []

    for index, segment in enumerate(
        segments
    ):
        cut_time = _pause_cut_time(
            segments,
            index,
            video_duration,
        )

        if (
            cut_time < end_low
            or cut_time > end_high
        ):
            continue

        score = _end_boundary_score(
            segments,
            index,
            original_end,
            soft_end,
            video_duration,
        )

        end_candidates.append(
            (
                score,
                cut_time,
                index,
                _pause_after_segment(
                    segments,
                    index,
                    video_duration,
                ),
                _sentence_looks_complete(
                    _segment_text(
                        segment
                    )
                ),
            )
        )

    local_end = original_end

    if end_candidates:
        end_candidates.sort(
            key=lambda item: item[
                0
            ],
            reverse=True,
        )

        local_end = float(
            end_candidates[
                0
            ][1]
        )

    for segment in segments:
        seg_start = _segment_start(
            segment
        )

        seg_end = _segment_end(
            segment
        )

        if (
            seg_start
            < local_end
            < seg_end
            and seg_end
            <= end_high
        ):
            local_end = seg_end
            break

    pause_candidates = []

    for score, cut_time, index, pause, complete in sorted(
        end_candidates,
        key=lambda item: item[
            0
        ],
        reverse=True,
    )[
        :8
    ]:
        pause_candidates.append(
            {
                "time": round(
                    float(
                        cut_time
                    ),
                    3,
                ),
                "score": round(
                    float(
                        score
                    ),
                    1,
                ),
                "pause_after": round(
                    float(
                        pause
                    ),
                    3,
                ),
                "sentence_complete": bool(
                    complete
                ),
                "text": _segment_text(
                    segments[
                        index
                    ]
                )[
                    :220
                ],
            }
        )

    return {
        "start_time": local_start,
        "end_time": local_end,
        "pause_candidates": pause_candidates,
    }


def _coerce_boundary_bool(
    value: object,
) -> bool:
    if isinstance(
        value,
        bool,
    ):
        return value

    return str(
        value
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "sim",
    }


def _snap_llm_boundary_to_transcript(
    start_time: float,
    end_time: float,
    transcript: Dict,
) -> tuple:
    segments = transcript.get(
        "segments",
        [],
    )

    if not isinstance(
        segments,
        list,
    ) or not segments:
        return (
            start_time,
            end_time,
        )

    video_duration = _coerce_float(
        transcript.get(
            "duration"
        ),
        _segment_end(
            segments[
                -1
            ]
        ),
    )

    start_options = []

    for index, segment in enumerate(
        segments
    ):
        point = _segment_start(
            segment
        )

        distance = abs(
            point
            - start_time
        )

        if distance <= 1.75:
            start_options.append(
                (
                    distance
                    - 0.03
                    * _start_boundary_score(
                        segments,
                        index,
                        start_time,
                    ),
                    point,
                )
            )

    if start_options:
        start_options.sort(
            key=lambda item: item[
                0
            ]
        )

        start_time = float(
            start_options[
                0
            ][1]
        )

    end_options = []

    for index, segment in enumerate(
        segments
    ):
        point = _pause_cut_time(
            segments,
            index,
            video_duration,
        )

        distance = abs(
            point
            - end_time
        )

        if distance <= 2.75:
            score = _end_boundary_score(
                segments,
                index,
                end_time,
                end_time + 99999.0,
                video_duration,
            )

            end_options.append(
                (
                    distance
                    - 0.02
                    * score,
                    point,
                )
            )

    if end_options:
        end_options.sort(
            key=lambda item: item[
                0
            ]
        )

        end_time = float(
            end_options[
                0
            ][1]
        )

    return (
        start_time,
        end_time,
    )


def _validate_boundary_result(
    result: Dict,
    original: Dict,
    transcript: Dict,
) -> Optional[Dict]:
    original_start = _coerce_float(
        original.get(
            "start_time"
        )
    )

    original_end = _coerce_float(
        original.get(
            "end_time"
        )
    )

    video_duration = _coerce_float(
        transcript.get(
            "duration"
        ),
        original_end
        + BOUNDARY_LOOKAHEAD_SECONDS,
    )

    start_time = _coerce_float(
        result.get(
            "start_time"
        ),
        -1.0,
    )

    end_time = _coerce_float(
        result.get(
            "end_time"
        ),
        -1.0,
    )

    start_low = max(
        0.0,
        original_start
        - BOUNDARY_LOOKBACK_SECONDS,
    )

    start_high = min(
        original_end - 1.0,
        original_start + 8.0,
    )

    hard_max = (
        float(
            MAX_HIGHLIGHT_SECONDS
        )
        + BOUNDARY_EXTENSION_SECONDS
    )

    end_low_absolute = max(
        0.0,
        original_end - 12.0,
    )

    end_high_absolute = min(
        video_duration,
        original_end
        + BOUNDARY_LOOKAHEAD_SECONDS,
    )

    if (
        start_time < start_low
        or start_time > start_high
    ):
        return None

    if (
        end_time < end_low_absolute
        or end_time > end_high_absolute
        or end_time <= start_time
    ):
        return None

    start_time, end_time = (
        _snap_llm_boundary_to_transcript(
            start_time,
            end_time,
            transcript,
        )
    )

    final_duration = (
        end_time
        - start_time
    )

    if (
        final_duration
        < float(
            MIN_HIGHLIGHT_SECONDS
        )
        or final_duration
        > hard_max
    ):
        return None

    result = dict(
        result
    )

    result[
        "start_time"
    ] = start_time

    result[
        "end_time"
    ] = end_time

    result[
        "start_complete"
    ] = _coerce_boundary_bool(
        result.get(
            "start_complete",
            True,
        )
    )

    result[
        "end_complete"
    ] = _coerce_boundary_bool(
        result.get(
            "end_complete",
            True,
        )
    )

    result[
        "confidence"
    ] = max(
        0,
        min(
            100,
            _coerce_int(
                result.get(
                    "confidence"
                ),
                0,
            ),
        ),
    )

    result[
        "reason"
    ] = str(
        result.get(
            "reason",
            "",
        )
    ).strip()

    return result


def _review_one_boundary(
    highlight: Dict,
    transcript: Dict,
    llm_fn: LLMFn,
) -> Dict:
    original = dict(
        highlight
    )

    original_start = _coerce_float(
        original.get(
            "start_time"
        )
    )

    original_end = _coerce_float(
        original.get(
            "end_time"
        )
    )

    video_duration = _coerce_float(
        transcript.get(
            "duration"
        ),
        original_end
        + BOUNDARY_LOOKAHEAD_SECONDS,
    )

    local = _local_boundary_refine(
        original,
        transcript,
    )

    context_start = max(
        0.0,
        original_start
        - BOUNDARY_LOOKBACK_SECONDS
        - 5.0,
    )

    context_end = min(
        video_duration,
        original_end
        + BOUNDARY_LOOKAHEAD_SECONDS
        + 5.0,
    )

    context_segments = (
        _boundary_context_segments(
            transcript,
            context_start,
            context_end,
        )
    )

    context = (
        _boundary_context_text(
            context_segments
        )
    )

    cache_key = (
        _boundary_cache_key(
            original,
            context,
        )
    )

    cached = _load_boundary_cache(
        cache_key
    )

    if cached is not None:
        validated = (
            _validate_boundary_result(
                cached,
                original,
                transcript,
            )
        )

        if validated is not None:
            validated[
                "_boundary_cache_hit"
            ] = True

            return validated

    local_start = _coerce_float(
        local.get(
            "start_time"
        ),
        original_start,
    )

    local_end = _coerce_float(
        local.get(
            "end_time"
        ),
        original_end,
    )

    start_low = max(
        0.0,
        original_start
        - BOUNDARY_LOOKBACK_SECONDS,
    )

    start_high = min(
        original_end - 1.0,
        original_start + 8.0,
    )

    hard_max = (
        float(
            MAX_HIGHLIGHT_SECONDS
        )
        + BOUNDARY_EXTENSION_SECONDS
    )

    end_low = max(
        local_start
        + float(
            MIN_HIGHLIGHT_SECONDS
        ),
        original_end - 12.0,
    )

    end_high = min(
        video_duration,
        original_end
        + BOUNDARY_LOOKAHEAD_SECONDS,
        local_start
        + hard_max,
    )

    pause_candidates = json.dumps(
        local.get(
            "pause_candidates",
            [],
        ),
        ensure_ascii=False,
        indent=2,
    )

    base_prompt = BOUNDARY_REVIEW_PROMPT.format(
        title=str(
            original.get(
                "title",
                "",
            )
        ),
        original_start=original_start,
        original_end=original_end,
        original_duration=(
            original_end
            - original_start
        ),
        local_start=local_start,
        local_end=local_end,
        start_low=start_low,
        start_high=start_high,
        end_low=end_low,
        end_high=end_high,
        minimum_duration=float(
            MIN_HIGHLIGHT_SECONDS
        ),
        soft_max=float(
            MAX_HIGHLIGHT_SECONDS
        ),
        hard_max=hard_max,
        pause_candidates=pause_candidates,
        context=context,
    )

    last_error = None

    for attempt in range(
        1,
        BOUNDARY_REVIEW_MAX_ATTEMPTS
        + 1,
    ):
        try:
            print(
                "[boundary] semantic review "
                f"attempt {attempt}/"
                f"{BOUNDARY_REVIEW_MAX_ATTEMPTS}",
                flush=True,
            )

            prompt = base_prompt

            if attempt > 1:
                prompt += (
                    "\n\nIMPORTANT RETRY: the previous answer was invalid or "
                    "did not prove a complete ending. Return valid JSON and "
                    "choose a semantically complete end_time."
                )

            raw = llm_fn(
                prompt
            )

            parsed = _parse_json_loose(
                raw
            )

            validated = (
                _validate_boundary_result(
                    parsed,
                    original,
                    transcript,
                )
            )

            if validated is None:
                raise ValueError(
                    "boundary timestamps outside allowed range"
                )

            if not validated.get(
                "end_complete",
                True,
            ):
                raise ValueError(
                    "model marked end as incomplete"
                )

            _save_boundary_cache(
                cache_key,
                validated,
            )

            return validated

        except Exception as exc:
            last_error = exc

            if (
                attempt
                < BOUNDARY_REVIEW_MAX_ATTEMPTS
            ):
                if _is_transient_llm_error(
                    exc
                ):
                    _sleep_before_retry(
                        "boundary review",
                        attempt,
                        BOUNDARY_REVIEW_MAX_ATTEMPTS,
                        exc,
                    )
                else:
                    print(
                        "[boundary] invalid review; "
                        "retrying in 1s...",
                        flush=True,
                    )

                    time.sleep(
                        1
                    )

    fallback = {
        "start_time": local_start,
        "end_time": local_end,
        "start_complete": True,
        "end_complete": True,
        "confidence": 55,
        "reason": (
            "local transcript/pause fallback"
            + (
                f": {last_error}"
                if last_error
                else ""
            )
        ),
        "_boundary_fallback": True,
    }

    validated = (
        _validate_boundary_result(
            fallback,
            original,
            transcript,
        )
    )

    if validated is not None:
        validated["_boundary_fallback"] = True
        return validated

    return {
        "start_time": original_start,
        "end_time": original_end,
        "start_complete": True,
        "end_complete": True,
        "confidence": 0,
        "reason": "original timestamps preserved",
        "_boundary_fallback": True,
    }


def refine_highlight_boundaries(
    highlights: List[Dict],
    transcript: Dict,
    llm_fn: LLMFn,
) -> List[Dict]:
    if (
        not BOUNDARY_REVIEW_ENABLED
        or not highlights
    ):
        return highlights

    segments = transcript.get(
        "segments",
        [],
    )

    if not isinstance(
        segments,
        list,
    ) or not segments:
        print(
            "[boundary] skipped: transcript has no segments",
            flush=True,
        )
        return highlights

    print(
        "[boundary] refining "
        f"{len(highlights)} final clip(s) "
        f"lookback={BOUNDARY_LOOKBACK_SECONDS:.0f}s "
        f"lookahead={BOUNDARY_LOOKAHEAD_SECONDS:.0f}s "
        f"soft_max={MAX_HIGHLIGHT_SECONDS}s "
        f"hard_max="
        f"{float(MAX_HIGHLIGHT_SECONDS) + BOUNDARY_EXTENSION_SECONDS:.0f}s",
        flush=True,
    )

    refined: List[
        Dict
    ] = []

    for index, highlight in enumerate(
        highlights,
        1,
    ):
        original_start = _coerce_float(
            highlight.get(
                "start_time"
            )
        )

        original_end = _coerce_float(
            highlight.get(
                "end_time"
            )
        )

        try:
            review = _review_one_boundary(
                highlight,
                transcript,
                llm_fn,
            )

            updated = dict(
                highlight
            )

            updated[
                "_boundary_original_start"
            ] = original_start

            updated[
                "_boundary_original_end"
            ] = original_end

            updated[
                "start_time"
            ] = _coerce_float(
                review.get(
                    "start_time"
                ),
                original_start,
            )

            updated[
                "end_time"
            ] = _coerce_float(
                review.get(
                    "end_time"
                ),
                original_end,
            )

            updated[
                "boundary_reviewed"
            ] = True

            updated[
                "boundary_confidence"
            ] = _coerce_int(
                review.get(
                    "confidence"
                ),
                0,
            )

            updated[
                "boundary_reason"
            ] = str(
                review.get(
                    "reason",
                    "",
                )
            ).strip()

            method = (
                "cache"
                if review.get(
                    "_boundary_cache_hit"
                )
                else (
                    "local-fallback"
                    if review.get(
                        "_boundary_fallback"
                    )
                    else "llm"
                )
            )

            updated[
                "boundary_method"
            ] = method

            print(
                "[boundary] "
                f"{index}/{len(highlights)} "
                f"{original_start:.2f}-{original_end:.2f}s "
                "-> "
                f"{updated['start_time']:.2f}-"
                f"{updated['end_time']:.2f}s "
                f"duration "
                f"{original_end - original_start:.1f}s"
                " -> "
                f"{updated['end_time'] - updated['start_time']:.1f}s "
                f"method={method} "
                f"confidence={updated['boundary_confidence']}",
                flush=True,
            )

            refined.append(
                updated
            )

        except Exception as exc:
            print(
                "[boundary] warning: "
                f"clip {index} refinement failed; "
                "keeping original timestamps. "
                f"Error: {exc}",
                flush=True,
            )

            refined.append(
                dict(
                    highlight
                )
            )

    return refined


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_highlights(
    transcript: Dict,
    num_clips: int = 3,
    llm_fn: Optional[
        LLMFn
    ] = None,
) -> Dict:

    llm_fn = (
        llm_fn
        or call_muapi_llm
    )

    duration = (
        _coerce_float(
            transcript.get(
                "duration",
                0,
            ),
            0.0,
        )
    )

    content_info = (
        detect_content_type(
            transcript,
            llm_fn=llm_fn,
        )
    )

    print(
        "[highlights] "
        f"content="
        f"{content_info.get('content_type')} "
        f"density="
        f"{content_info.get('density')} "
        f"duration="
        f"{duration:.0f}s",
        flush=True,
    )

    print(
        "[highlights] profile "
        f"aspect={_ASPECT_RATIO} "
        f"minimum={MIN_HIGHLIGHT_SECONDS}s "
        f"maximum={MAX_HIGHLIGHT_SECONDS}s",
        flush=True,
    )

    # ------------------------------------------------------------------
    # Long video
    # ------------------------------------------------------------------

    if (
        duration
        >= LONG_VIDEO_THRESHOLD
    ):

        chunks = (
            chunk_transcript(
                transcript
            )
        )

        print(
            "[highlights] long video - "
            f"splitting into "
            f"{len(chunks)} "
            "30-minute chunks",
            flush=True,
        )

        all_highlights: List[
            Dict
        ] = []

        for i, chunk in enumerate(
            chunks
        ):

            offset = (
                _coerce_float(
                    chunk.get(
                        "_offset",
                        0,
                    ),
                    0.0,
                )
            )

            chunk_index = int(
                chunk.get(
                    "_chunk_index",
                    i,
                )
            )

            chunk_duration = float(
                chunk.get(
                    "duration",
                    0,
                )
            )

            chunk_end = (
                offset
                + chunk_duration
            )

            print(
                "[highlights] "
                f"chunk {i + 1}/"
                f"{len(chunks)} "
                f"({offset / 60:.1f}m "
                f"-> "
                f"{chunk_end / 60:.1f}m)",
                flush=True,
            )

            text = (
                build_transcript_text(
                    chunk
                )
            )

            cache_key = (
                _chunk_cache_key(
                    text,
                    content_info,
                    chunk_duration,
                    PER_CHUNK_CANDIDATES,
                )
            )

            result = (
                _load_chunk_cache(
                    cache_key,
                    duration=chunk_duration,
                )
            )

            if result is not None:

                print(
                    "[highlights] "
                    f"chunk {i + 1} "
                    "cache hit "
                    f"({len(result.get('highlights', []))} "
                    "candidates)",
                    flush=True,
                )

            else:

                # A chunk shorter than the configured minimum can never
                # produce a valid highlight, so do not spend an API call.
                if chunk_duration < MIN_HIGHLIGHT_SECONDS:
                    print(
                        "[highlights] skipping chunk "
                        f"{i + 1}/{len(chunks)}: "
                        f"only {chunk_duration:.1f}s available, "
                        f"minimum={MIN_HIGHLIGHT_SECONDS}s",
                        flush=True,
                    )
                    result = {"highlights": []}

                else:
                    # Small tail chunks are allowed to try, but should never
                    # burn six API calls or abort an otherwise successful job.
                    short_tail_threshold = max(
                        float(MIN_HIGHLIGHT_SECONDS) * 4.0,
                        180.0,
                    )
                    chunk_attempts = (
                        2
                        if chunk_duration < short_tail_threshold
                        else MAX_HIGHLIGHT_API_ATTEMPTS
                    )

                    try:
                        result = (
                            call_highlight_api(
                                text,
                                content_info,
                                chunk_duration,
                                num_clips=(
                                    PER_CHUNK_CANDIDATES
                                ),
                                is_chunk=True,
                                llm_fn=llm_fn,
                                max_attempts=chunk_attempts,
                            )
                        )
                    except Exception as exc:
                        print(
                            "[highlights] warning: "
                            f"chunk {i + 1}/{len(chunks)} "
                            "produced no usable highlights and will be skipped. "
                            f"Error: {exc}",
                            flush=True,
                        )
                        result = {"highlights": []}

                    if result.get("highlights"):
                        _save_chunk_cache(
                            cache_key,
                            result,
                        )

                    print(
                        "[highlights] "
                        f"chunk {i + 1} "
                        "produced "
                        f"{len(result.get('highlights', []))} "
                        "candidates",
                        flush=True,
                    )

            for highlight in (
                result.get(
                    "highlights",
                    [],
                )
            ):

                adjusted = dict(
                    highlight
                )

                adjusted[
                    "start_time"
                ] = (
                    float(
                        adjusted[
                            "start_time"
                        ]
                    )
                    + offset
                )

                adjusted[
                    "end_time"
                ] = (
                    float(
                        adjusted[
                            "end_time"
                        ]
                    )
                    + offset
                )

                adjusted[
                    "_chunk_index"
                ] = chunk_index

                all_highlights.append(
                    adjusted
                )

        print(
            "[highlights] total before dedupe: "
            f"{len(all_highlights)}",
            flush=True,
        )

        if not all_highlights:
            raise RuntimeError(
                "No usable highlights were found in any chunk. "
                "Every chunk returned zero valid candidates."
            )

        deduped = (
            dedupe_highlights(
                all_highlights
            )
        )

        print(
            "[highlights] after dedupe: "
            f"{len(deduped)}",
            flush=True,
        )

        final = (
            rerank_global_highlights(
                deduped,
                num_clips=num_clips,
                llm_fn=llm_fn,
            )
        )

        final = refine_highlight_boundaries(
            final,
            transcript,
            llm_fn,
        )

        return {
            "highlights": final
        }

    # ------------------------------------------------------------------
    # Short video
    # ------------------------------------------------------------------

    text = (
        build_transcript_text(
            transcript
        )
    )

    result = (
        call_highlight_api(
            text,
            content_info,
            duration,
            num_clips=num_clips,
            llm_fn=llm_fn,
        )
    )

    highlights = (
        dedupe_highlights(
            result.get(
                "highlights",
                [],
            )
        )
    )

    highlights = (
        highlights[
            :num_clips
        ]
    )

    highlights = refine_highlight_boundaries(
        highlights,
        transcript,
        llm_fn,
    )

    return {
        "highlights": highlights
    }


