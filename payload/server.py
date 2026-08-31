import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request as UrlRequest, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from youtube_publisher import (
        YouTubeManager,
        build_metadata_prompt,
        apply_generated_metadata,
        load_sidecar,
    )
except Exception:
    YouTubeManager = None
    build_metadata_prompt = None
    apply_generated_metadata = None
    load_sidecar = None


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
FRONTEND_DIR = ROOT / "frontend"
ENV_FILE = ROOT / ".env"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CutLab AI", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

JOBS: Dict[str, Dict] = {}
JOBS_LOCK = threading.Lock()

SCHEDULE_JOBS: Dict[str, Dict] = {}
SCHEDULE_JOBS_LOCK = threading.Lock()

METADATA_JOBS: Dict[str, Dict] = {}
METADATA_JOBS_LOCK = threading.Lock()

MEDIA: Dict[str, Path] = {}
MEDIA_LOCK = threading.Lock()

YOUTUBE = YouTubeManager(ROOT) if YouTubeManager is not None else None


def read_env() -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not ENV_FILE.exists():
        return result

    try:
        from dotenv import dotenv_values
        for key, value in dotenv_values(ENV_FILE).items():
            if value is not None:
                result[str(key)] = str(value).strip()
        return result
    except Exception:
        pass

    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    except Exception:
        pass

    return result


def get_gpu_name() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
        if result.returncode == 0:
            values = result.stdout.strip().splitlines()
            if values:
                return values[0].strip()
    except Exception:
        pass
    return "NVIDIA GPU"



def lmstudio_base_url() -> str:
    env = read_env()
    return (
        os.getenv("LMSTUDIO_BASE_URL")
        or env.get("LMSTUDIO_BASE_URL")
        or "http://127.0.0.1:1234/v1"
    ).strip().rstrip("/")


def discover_lmstudio_models(timeout: float = 1.2) -> Dict:
    """Detect a local LM Studio server and list its OpenAI-compatible models."""
    base_url = lmstudio_base_url()
    request = UrlRequest(
        f"{base_url}/models",
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return {
            "online": False,
            "base_url": base_url,
            "models": [],
            "error": str(exc),
        }

    raw_models = payload.get("data", []) if isinstance(payload, dict) else []
    models: List[str] = []

    for item in raw_models:
        if isinstance(item, dict):
            model_id = str(item.get("id") or "").strip()
        else:
            model_id = str(item or "").strip()

        if model_id and model_id not in models:
            models.append(model_id)

    return {
        "online": True,
        "base_url": base_url,
        "models": models,
        "error": None,
    }


def check_cuda() -> Tuple[bool, str]:
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return True, get_gpu_name()
        return False, "CUDA nÃ£o detectada"
    except Exception as exc:
        return False, str(exc)


def check_ffmpeg() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_nvenc() -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return (
            (True, "H.264 NVENC")
            if "h264_nvenc" in output
            else (False, "NÃ£o disponÃ­vel")
        )
    except Exception as exc:
        return False, str(exc)


def check_ass_filter() -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")

        for line in output.splitlines():
            columns = line.split()
            if (
                len(columns) >= 3
                and columns[1].lower() == "ass"
                and columns[2].upper() == "V->V"
            ):
                return True, "libass / ASS"

        if re.search(r"(?im)\bass\s+V->V\b", output):
            return True, "libass / ASS"

        return False, "Filtro ASS nÃ£o encontrado"
    except Exception as exc:
        return False, str(exc)


def safe_directory(value: str) -> Path:
    raw = str(value or "").strip()
    path = Path(raw).expanduser() if raw else OUTPUT_DIR
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"NÃ£o foi possÃ­vel usar a pasta: {exc}",
        )
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="O destino nÃ£o Ã© uma pasta.")
    return path.resolve()


def is_final_video(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith("source_"):
        return False
    return not any(
        name.endswith(suffix)
        for suffix in (".cut.mp4", ".silent.mp4", ".render.mp4", ".temp.mp4")
    )


def natural_key(path: Path):
    match = re.match(r"^(\d+)", path.stem)
    number = int(match.group(1)) if match else 10**9
    return number, path.name.casefold()


def list_final_videos(directory: Path) -> List[Path]:
    videos = [
        path for path in directory.glob("*.mp4")
        if is_final_video(path)
    ]
    videos.sort(key=natural_key)
    return videos


def snapshot(directory: Path) -> Dict[str, Tuple[int, float]]:
    result = {}
    for path in list_final_videos(directory):
        try:
            stat = path.stat()
            result[str(path.resolve())] = (stat.st_size, stat.st_mtime)
        except OSError:
            pass
    return result


def find_generated(
    directory: Path,
    before: Dict[str, Tuple[int, float]],
) -> List[Path]:
    generated = []
    for path in list_final_videos(directory):
        try:
            stat = path.stat()
        except OSError:
            continue
        current = (stat.st_size, stat.st_mtime)
        if before.get(str(path.resolve())) != current:
            generated.append(path)
    generated.sort(key=natural_key)
    return generated


def register_media(path: Path) -> Dict:
    resolved = path.resolve()

    with MEDIA_LOCK:
        token = next(
            (key for key, value in MEDIA.items() if value == resolved),
            None,
        )
        if token is None:
            token = uuid.uuid4().hex
            MEDIA[token] = resolved

    stat = path.stat()
    title = re.sub(r"^\d+\s*-\s*", "", path.stem)

    return {
        "id": token,
        "name": path.name,
        "title": title,
        "size_mb": round(stat.st_size / 1024 / 1024, 1),
        "video_url": f"/media/{token}",
        "download_url": f"/download/{token}",
        "path": str(resolved),
    }


def get_media(token: str) -> Path:
    with MEDIA_LOCK:
        path = MEDIA.get(token)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo nÃ£o encontrado.")
    return path


def analyze_log(line: str) -> Tuple[Optional[int], Optional[str]]:
    lower = line.lower()

    if "[download/local]" in lower:
        if "reusing cached" in lower or "ready" in lower:
            return 12, "VÃ­deo pronto"
        return 5, "Baixando vÃ­deo"

    if "[transcribe/local]" in lower:
        if "cached segments" in lower or "wrote cache" in lower or " segments," in lower:
            return 32, "TranscriÃ§Ã£o concluÃ­da"
        return 18, "Transcrevendo Ã¡udio"

    if "[llm/nvidia]" in lower:
        if "sending request" in lower:
            return 38, "Analisando os melhores momentos"
        if "response received" in lower:
            return 42, "AnÃ¡lise recebida"

    if "[llm/groq]" in lower:
        if "sending request" in lower:
            return 38, "Groq analisando os melhores momentos"
        if "response received" in lower:
            return 42, "Resposta da Groq recebida"

    chunk = re.search(r"chunk\s+(\d+)/(\d+)", lower)
    if chunk:
        current = int(chunk.group(1))
        total = max(1, int(chunk.group(2)))
        return 36 + int(34 * current / total), f"Analisando bloco {current}/{total}"

    if "global ranking" in lower:
        return 72, "Selecionando os melhores cortes"
    if "[reframe/local]" in lower:
        return 77, "Ajustando enquadramento"
    if "[filters/local]" in lower:
        return 79, "Aplicando filtros"
    if "[captions/local]" in lower:
        return 81, "Aplicando legendas"

    match = re.search(r"\[clip/local\].*?(\d+)/(\d+)", lower)
    if match:
        current = int(match.group(1))
        total = max(1, int(match.group(2)))
        progress = min(98, 80 + int(18 * current / total))
        return progress, f"Renderizando Short {current}/{total}"

    return None, None


class JobRequest(BaseModel):
    youtube_url: str
    num_clips: int = 10
    provider: str = "nvidia"
    model: str = ""
    quality: str = "1080"
    aspect_ratio: str = "9:16"
    reframe_mode: str = "auto"
    transcription_language: str = "auto"
    output_dir: str = ""

    captions_enabled: bool = False
    caption_style: str = "reels_bold"
    caption_font: str = "Arial Black"
    caption_position: str = "bottom"
    caption_size: int = 54
    caption_words: int = 5

    filter_vignette: float = 0
    filter_brightness: float = 0
    filter_contrast: float = 0
    filter_saturation: float = 0
    filter_sharpen: float = 0
    filter_cinematic: float = 0
    filter_warm: float = 0
    filter_cool: float = 0
    filter_grayscale: float = 0


def update_job(job_id: str, **changes):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(changes)


def append_job_log(job_id: str, line: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append(line)
        if len(logs) > 600:
            del logs[:-600]


def serialize_job(job_id: str) -> Dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Processamento nÃ£o encontrado.")
        data = dict(job)

    started = data.get("started_at")
    finished = data.get("finished_at")
    data["elapsed"] = (
        max(0.0, (finished or time.monotonic()) - started)
        if started else 0.0
    )
    return data


def run_generation_job(job_id: str, request: JobRequest):
    destination = safe_directory(request.output_dir)
    before = snapshot(destination)

    command = [
        sys.executable,
        str(ROOT / "main.py"),
        request.youtube_url,
        "--mode",
        "local",
        "--num-clips",
        str(request.num_clips),
    ]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["LLM_PROVIDER"] = request.provider

    if request.provider == "nvidia":
        env["NVIDIA_MODEL"] = request.model

    elif request.provider == "gemini":
        env["GEMINI_MODEL"] = request.model

    elif request.provider == "groq":
        groq_key = (
            os.getenv("GROQ_API_KEY")
            or read_env().get("GROQ_API_KEY")
            or ""
        ).strip()

        if not groq_key:
            raise RuntimeError(
                "GROQ_API_KEY nÃ£o configurada. "
                "Rode CONFIGURAR_GROQ.bat ou adicione a chave ao .env."
            )

        # The local pipeline already has an OpenAI-compatible backend.
        # Groq exposes the same chat-completions protocol, so the child
        # process is pointed at Groq without replacing the existing LLM code.
        env["LLM_PROVIDER"] = "openai"
        env["OPENAI_API_KEY"] = groq_key
        env["OPENAI_BASE_URL"] = (
            os.getenv("GROQ_BASE_URL")
            or read_env().get("GROQ_BASE_URL")
            or "https://api.groq.com/openai/v1"
        )
        env["OPENAI_MODEL"] = request.model
        env["CUTLAB_ORIGINAL_LLM_PROVIDER"] = "groq"

    elif request.provider == "lmstudio":
        lm_status = discover_lmstudio_models(timeout=1.5)

        if not lm_status.get("online"):
            raise RuntimeError(
                "LM Studio nÃ£o estÃ¡ acessÃ­vel em "
                f"{lm_status.get('base_url')}. "
                "Inicie o servidor local do LM Studio e tente novamente."
            )

        env["LLM_PROVIDER"] = "openai"
        env["OPENAI_API_KEY"] = "lm-studio"
        env["OPENAI_BASE_URL"] = str(lm_status["base_url"])
        env["OPENAI_MODEL"] = request.model
        env["CUTLAB_ORIGINAL_LLM_PROVIDER"] = "lmstudio"

    else:
        env["OPENAI_MODEL"] = request.model

    env["CUTLAB_DOWNLOAD_QUALITY"] = request.quality
    env["CUTLAB_OUTPUT_RESOLUTION"] = request.quality
    env["CUTLAB_ASPECT_RATIO"] = request.aspect_ratio
    env["CUTLAB_REFRAME_MODE"] = request.reframe_mode
    env["CUTLAB_TRANSCRIPTION_LANGUAGE"] = request.transcription_language
    env["CUTLAB_MIN_HIGHLIGHT_SECONDS"] = (
        "300" if request.aspect_ratio == "16:9" else "30"
    )
    env["CUTLAB_MAX_HIGHLIGHT_SECONDS"] = (
        "1800" if request.aspect_ratio == "16:9" else "150"
    )
    env["CUTLAB_OUTPUT_DIR"] = str(destination)

    env["CUTLAB_CAPTIONS_ENABLED"] = "1" if request.captions_enabled else "0"
    env["CUTLAB_CAPTION_STYLE"] = request.caption_style
    env["CUTLAB_CAPTION_FONT"] = request.caption_font
    env["CUTLAB_CAPTION_POSITION"] = request.caption_position
    env["CUTLAB_CAPTION_SIZE"] = str(request.caption_size)
    env["CUTLAB_CAPTION_WORDS"] = str(request.caption_words)

    env["CUTLAB_FILTER_VIGNETTE"] = str(request.filter_vignette)
    env["CUTLAB_FILTER_BRIGHTNESS"] = str(request.filter_brightness)
    env["CUTLAB_FILTER_CONTRAST"] = str(request.filter_contrast)
    env["CUTLAB_FILTER_SATURATION"] = str(request.filter_saturation)
    env["CUTLAB_FILTER_SHARPEN"] = str(request.filter_sharpen)
    env["CUTLAB_FILTER_CINEMATIC"] = str(request.filter_cinematic)
    env["CUTLAB_FILTER_WARM"] = str(request.filter_warm)
    env["CUTLAB_FILTER_COOL"] = str(request.filter_cool)
    env["CUTLAB_FILTER_GRAYSCALE"] = str(request.filter_grayscale)

    flags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW

    started = time.monotonic()
    update_job(
        job_id,
        state="running",
        progress=1,
        status="Preparando vÃ­deo",
        started_at=started,
        output_dir=str(destination),
    )

    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )

        assert process.stdout is not None

        for raw in process.stdout:
            line = raw.rstrip()
            if not line:
                continue

            append_job_log(job_id, line)
            progress, status = analyze_log(line)
            changes = {}

            if progress is not None:
                with JOBS_LOCK:
                    current = JOBS.get(job_id, {}).get("progress", 1)
                changes["progress"] = max(current, progress)

            if status:
                changes["status"] = status

            if changes:
                update_job(job_id, **changes)

        return_code = process.wait()
        finished = time.monotonic()

        if return_code != 0:
            update_job(
                job_id,
                state="error",
                status="O processamento foi interrompido",
                finished_at=finished,
                return_code=return_code,
            )
            return

        generated = find_generated(destination, before)

        update_job(
            job_id,
            state="done",
            progress=100,
            status="Shorts gerados com sucesso",
            finished_at=finished,
            videos=[register_media(path) for path in generated],
            return_code=0,
        )

    except Exception as exc:
        append_job_log(job_id, f"[frontend] {type(exc).__name__}: {exc}")
        update_job(
            job_id,
            state="error",
            status="Erro ao executar o processamento",
            finished_at=time.monotonic(),
            error=str(exc),
        )


def parse_times(values: List[str]) -> List[dt_time]:
    parsed = []

    for value in values:
        raw = str(value).strip()

        try:
            hour_text, minute_text = raw.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)

            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError

        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"HorÃ¡rio invÃ¡lido: {raw}. Use HH:MM.",
            )

        parsed.append(dt_time(hour=hour, minute=minute))

    if not parsed:
        raise HTTPException(status_code=400, detail="Informe pelo menos um horÃ¡rio.")

    return parsed


def build_schedule(
    media_ids: List[str],
    start_date: str,
    times: List[str],
    posts_per_day: int,
    timezone_name: str,
) -> List[Dict]:
    if not media_ids:
        raise HTTPException(status_code=400, detail="Nenhum vÃ­deo selecionado.")

    if not (1 <= posts_per_day <= 10):
        raise HTTPException(
            status_code=400,
            detail="PublicaÃ§Ãµes por dia deve ficar entre 1 e 10.",
        )

    parsed_times = parse_times(times)

    if len(parsed_times) < posts_per_day:
        raise HTTPException(
            status_code=400,
            detail="A quantidade de horÃ¡rios Ã© menor que publicaÃ§Ãµes por dia.",
        )

    try:
        first_day = date.fromisoformat(start_date)
    except Exception:
        raise HTTPException(status_code=400, detail="Data inicial invÃ¡lida.")

    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        raise HTTPException(status_code=400, detail="Fuso horÃ¡rio invÃ¡lido.")

    schedule = []

    for index, media_id in enumerate(media_ids):
        path = get_media(media_id)
        day_number = index // posts_per_day
        slot_number = index % posts_per_day

        local_datetime = datetime.combine(
            first_day + timedelta(days=day_number),
            parsed_times[slot_number],
            tzinfo=zone,
        )

        publish_utc = local_datetime.astimezone(timezone.utc)

        schedule.append(
            {
                "index": index + 1,
                "media_id": media_id,
                "filename": path.name,
                "title": re.sub(r"^\d+\s*-\s*", "", path.stem),
                "local": local_datetime.isoformat(timespec="minutes"),
                "publish_at": (
                    publish_utc
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                ),
            }
        )

    return schedule



def _metadata_context(path: Path) -> Dict:
    sidecar: Dict = {}

    if load_sidecar is not None:
        try:
            sidecar = load_sidecar(path)
        except Exception:
            sidecar = {}

    highlight = (
        sidecar.get("highlight", {})
        if isinstance(sidecar, dict)
        else {}
    )

    if not isinstance(highlight, dict):
        highlight = {}

    clean_title = re.sub(
        r"^\d+\s*-\s*",
        "",
        path.stem,
    )

    return {
        "filename": path.name,
        "highlight_title": (
            highlight.get("title")
            or sidecar.get("title")
            or clean_title
        ),
        "hook_sentence": (
            highlight.get("hook_sentence")
            or sidecar.get("hook_sentence")
            or ""
        ),
        "virality_reason": (
            highlight.get("virality_reason")
            or sidecar.get("virality_reason")
            or ""
        ),
        "transcript": (
            sidecar.get("transcript_excerpt")
            or sidecar.get("transcript")
            or ""
        ),
    }


def _call_metadata_ai(
    prompt: str,
    provider: str,
    model: str,
) -> str:
    provider = str(
        provider
        or "nvidia"
    ).strip().lower()

    model = str(
        model
        or ""
    ).strip()

    env = read_env()

    if provider == "lmstudio":
        from openai import OpenAI

        status = discover_lmstudio_models(timeout=1.5)

        if not status.get("online"):
            raise RuntimeError(
                "LM Studio nÃ£o estÃ¡ acessÃ­vel em "
                f"{status.get('base_url')}."
            )

        selected = (
            model
            or os.getenv("LMSTUDIO_MODEL")
            or env.get("LMSTUDIO_MODEL")
            or (status.get("models") or [""])[0]
        )

        if not selected:
            raise RuntimeError(
                "Nenhum modelo foi encontrado no LM Studio. "
                "Carregue ou disponibilize um modelo e tente novamente."
            )

        client = OpenAI(
            api_key="lm-studio",
            base_url=str(status["base_url"]),
            timeout=180.0,
            max_retries=0,
        )

        print(
            f"[llm/lmstudio] model={selected}",
            flush=True,
        )
        print(
            "[llm/lmstudio] sending request...",
            flush=True,
        )

        response = client.chat.completions.create(
            model=selected,
            temperature=0.2,
            max_tokens=2048,
            stream=False,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Quando JSON for solicitado, "
                        "retorne somente JSON vÃ¡lido."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        text = str(
            response.choices[0].message.content
            or ""
        ).strip()

        print(
            "[llm/lmstudio] response received",
            flush=True,
        )

        if not text:
            raise RuntimeError(
                "LM Studio retornou resposta vazia."
            )

        return text

    if provider == "groq":
        from openai import OpenAI

        api_key = (
            os.getenv("GROQ_API_KEY")
            or env.get("GROQ_API_KEY")
        )

        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY nÃ£o configurada."
            )

        base_url = (
            os.getenv("GROQ_BASE_URL")
            or env.get("GROQ_BASE_URL")
            or "https://api.groq.com/openai/v1"
        )

        selected = (
            model
            or os.getenv("GROQ_MODEL")
            or env.get("GROQ_MODEL")
            or "qwen/qwen3.6-27b"
        )

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=90.0,
            max_retries=1,
        )

        print(
            f"[llm/groq] model={selected}",
            flush=True,
        )
        print(
            "[llm/groq] sending request...",
            flush=True,
        )

        response = client.chat.completions.create(
            model=selected,
            temperature=0.2,
            max_tokens=4096,
            stream=False,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Quando JSON for solicitado, "
                        "retorne somente JSON vÃ¡lido."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        text = str(
            response.choices[0].message.content
            or ""
        ).strip()

        print(
            "[llm/groq] response received",
            flush=True,
        )

        if not text:
            raise RuntimeError(
                "Groq retornou resposta vazia."
            )

        return text

    if provider in {
        "nvidia",
        "openai",
    }:
        from openai import OpenAI

        if provider == "nvidia":
            api_key = (
                os.getenv("NVIDIA_API_KEY")
                or env.get("NVIDIA_API_KEY")
            )

            if not api_key:
                raise RuntimeError(
                    "NVIDIA_API_KEY nÃ£o configurada."
                )

            base_url = (
                os.getenv("NVIDIA_BASE_URL")
                or env.get("NVIDIA_BASE_URL")
                or "https://integrate.api.nvidia.com/v1"
            )

            selected = (
                model
                or os.getenv("NVIDIA_MODEL")
                or env.get("NVIDIA_MODEL")
                or "nvidia/nemotron-3.5-lightning-30b-a3b"
            )

            candidates: List[str] = []

            for candidate in (
                selected,
                "nvidia/llama-3.3-nemotron-super-49b-v1.5",
                "deepseek-ai/deepseek-v4-flash-0731",
            ):
                if (
                    candidate
                    and candidate not in candidates
                ):
                    candidates.append(
                        candidate
                    )

            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=240.0,
                max_retries=0,
            )

            last_error: Optional[
                Exception
            ] = None

            for candidate in candidates:
                try:
                    response = (
                        client.chat.completions.create(
                            model=candidate,
                            temperature=0.2,
                            max_tokens=8192,
                            stream=False,
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        "Quando JSON for solicitado, "
                                        "retorne somente JSON vÃ¡lido."
                                    ),
                                },
                                {
                                    "role": "user",
                                    "content": prompt,
                                },
                            ],
                            extra_body={
                                "chat_template_kwargs": {
                                    "enable_thinking": False,
                                }
                            },
                        )
                    )

                    text = str(
                        response
                        .choices[0]
                        .message
                        .content
                        or ""
                    ).strip()

                    if text:
                        return text

                except Exception as exc:
                    last_error = exc

            raise RuntimeError(
                "Falha nos modelos NVIDIA: "
                f"{last_error}"
            )

        api_key = (
            os.getenv("OPENAI_API_KEY")
            or env.get("OPENAI_API_KEY")
        )

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY nÃ£o configurada."
            )

        selected = (
            model
            or os.getenv("OPENAI_MODEL")
            or env.get("OPENAI_MODEL")
            or "gpt-4o-mini"
        )

        client = OpenAI(
            api_key=api_key,
            timeout=240.0,
            max_retries=1,
        )

        response = (
            client.chat.completions.create(
                model=selected,
                temperature=0.2,
                max_tokens=8192,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Quando JSON for solicitado, "
                            "retorne somente JSON vÃ¡lido."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            )
        )

        return str(
            response
            .choices[0]
            .message
            .content
            or ""
        ).strip()

    if provider == "gemini":
        from google import genai

        api_key = (
            os.getenv("GEMINI_API_KEY")
            or env.get("GEMINI_API_KEY")
        )

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY nÃ£o configurada."
            )

        selected = (
            model
            or os.getenv("GEMINI_MODEL")
            or env.get("GEMINI_MODEL")
            or "gemini-3.6-flash"
        )

        client = genai.Client(
            api_key=api_key
        )

        response = (
            client.models.generate_content(
                model=selected,
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

    raise RuntimeError(
        f"Provedor invÃ¡lido: {provider}"
    )


class MetadataGenerateRequest(BaseModel):
    media_ids: List[str]
    provider: str = "nvidia"
    model: str = ""


class YouTubeVideoItem(BaseModel):
    media_id: str
    title: str
    description: str
    tags: List[str] = Field(
        default_factory=list
    )


class CompletePublishRequest(BaseModel):
    videos: List[YouTubeVideoItem]
    publication_mode: str = "public"
    made_for_kids: bool = False
    notify_subscribers: bool = False
    start_date: str = ""
    times: List[str] = Field(
        default_factory=list
    )
    posts_per_day: int = 1
    timezone: str = (
        "America/Sao_Paulo"
    )


def _validate_complete_publish(
    request: CompletePublishRequest,
):
    total = len(
        request.videos
    )

    if not (
        1
        <= total
        <= 50
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Selecione de 1 a 50 "
                "vÃ­deos por operaÃ§Ã£o."
            ),
        )

    if (
        request.publication_mode
        not in {
            "public",
            "unlisted",
            "private",
            "scheduled",
        }
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Modo de publicaÃ§Ã£o invÃ¡lido."
            ),
        )

    if (
        request.publication_mode
        == "scheduled"
    ):
        if not (
            1
            <= request.posts_per_day
            <= 10
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Use de 1 a 10 vÃ­deos "
                    "por dia."
                ),
            )

        build_schedule(
            [
                item.media_id
                for item in request.videos
            ],
            request.start_date,
            request.times,
            request.posts_per_day,
            request.timezone,
        )


def run_complete_publish_job(
    job_id: str,
    request: CompletePublishRequest,
):
    _validate_complete_publish(
        request
    )

    publish_times: Dict[
        str,
        str,
    ] = {}

    if (
        request.publication_mode
        == "scheduled"
    ):
        schedule = build_schedule(
            [
                item.media_id
                for item in request.videos
            ],
            request.start_date,
            request.times,
            request.posts_per_day,
            request.timezone,
        )

        now_utc = datetime.now(
            timezone.utc
        )

        for row in schedule:
            scheduled = (
                datetime.fromisoformat(
                    row[
                        "publish_at"
                    ].replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

            if scheduled <= now_utc:
                update_schedule_job(
                    job_id,
                    state="error",
                    status=(
                        "Existe um horÃ¡rio "
                        "no passado."
                    ),
                    error=(
                        "Ajuste a data "
                        "ou os horÃ¡rios."
                    ),
                )
                return

            publish_times[
                row["media_id"]
            ] = row[
                "publish_at"
            ]

    total = len(
        request.videos
    )

    successes = []
    failures = []
    consecutive_failures = 0

    for index, item in enumerate(
        request.videos,
        1,
    ):
        path = get_media(
            item.media_id
        )

        update_schedule_job(
            job_id,
            state="running",
            status=(
                f"Enviando {index}/{total}: "
                f"{path.name}"
            ),
            current=index,
            total=total,
        )

        def on_progress(
            fraction: float,
        ):
            fraction = max(
                0.0,
                min(
                    1.0,
                    float(
                        fraction
                    ),
                ),
            )

            overall = (
                (
                    index
                    - 1
                    + fraction
                )
                / total
                * 100.0
            )

            update_schedule_job(
                job_id,
                progress=round(
                    overall,
                    1,
                ),
            )

        try:
            publish_at = (
                publish_times.get(
                    item.media_id
                )
            )

            privacy_status = (
                "private"
                if (
                    request.publication_mode
                    == "scheduled"
                )
                else request.publication_mode
            )

            result = (
                YOUTUBE.upload_video(
                    path,
                    title=item.title,
                    description=(
                        item.description
                    ),
                    tags=item.tags,
                    privacy_status=(
                        privacy_status
                    ),
                    made_for_kids=(
                        request.made_for_kids
                    ),
                    notify_subscribers=(
                        request.notify_subscribers
                    ),
                    publish_at=publish_at,
                    progress_callback=(
                        on_progress
                    ),
                )
            )

            successes.append(
                {
                    "media_id": (
                        item.media_id
                    ),
                    "filename": path.name,
                    "publish_at": (
                        publish_at
                    ),
                    **result,
                }
            )

            consecutive_failures = 0

        except Exception as exc:
            failures.append(
                {
                    "media_id": (
                        item.media_id
                    ),
                    "filename": path.name,
                    "error": str(
                        exc
                    ),
                }
            )

            consecutive_failures += 1

            if (
                consecutive_failures
                >= 3
            ):
                break

    attempted = (
        len(successes)
        + len(failures)
    )

    update_schedule_job(
        job_id,
        state=(
            "done"
            if successes
            else "error"
        ),
        progress=(
            100
            if attempted >= total
            else round(
                attempted
                / total
                * 100,
                1,
            )
        ),
        status=(
            f"{len(successes)} vÃ­deos "
            "enviados ao YouTube"
        ),
        results=successes,
        failures=failures,
        completed=len(
            successes
        ),
        attempted=attempted,
        total=total,
        error=(
            None
            if successes
            else "Nenhum vÃ­deo foi enviado."
        ),
    )


class SchedulePreviewRequest(BaseModel):
    media_ids: List[str]
    start_date: str
    times: List[str]
    posts_per_day: int = 5
    timezone: str = "America/Sao_Paulo"


class BatchScheduleRequest(SchedulePreviewRequest):
    description: str = "Gerado com CutLab AI."
    tags: List[str] = Field(default_factory=lambda: ["shorts"])


def update_schedule_job(job_id: str, **changes):
    with SCHEDULE_JOBS_LOCK:
        if job_id in SCHEDULE_JOBS:
            SCHEDULE_JOBS[job_id].update(changes)


def serialize_schedule_job(job_id: str) -> Dict:
    with SCHEDULE_JOBS_LOCK:
        job = SCHEDULE_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Agendamento nÃ£o encontrado.")
        return dict(job)


def run_schedule_job(job_id: str, request: BatchScheduleRequest):
    schedule = build_schedule(
        request.media_ids,
        request.start_date,
        request.times,
        request.posts_per_day,
        request.timezone,
    )

    now_utc = datetime.now(timezone.utc)

    for item in schedule:
        publish_at = datetime.fromisoformat(
            item["publish_at"].replace("Z", "+00:00")
        )
        if publish_at <= now_utc:
            update_schedule_job(
                job_id,
                state="error",
                status="Existe um horÃ¡rio de publicaÃ§Ã£o no passado.",
                error="Altere a data inicial ou os horÃ¡rios.",
            )
            return

    total = len(schedule)
    successes = []
    failures = []
    consecutive_failures = 0

    for index, item in enumerate(schedule, 1):
        path = get_media(item["media_id"])

        update_schedule_job(
            job_id,
            state="running",
            status=f"Enviando {index}/{total}: {path.name}",
            current=index,
            total=total,
        )

        def on_progress(fraction: float):
            fraction = max(0.0, min(1.0, float(fraction)))
            overall = ((index - 1 + fraction) / total) * 100.0
            update_schedule_job(job_id, progress=round(overall, 1))

        try:
            result = YOUTUBE.upload_video(
                path,
                title=item["title"],
                description=request.description,
                tags=request.tags,
                privacy_status="private",
                made_for_kids=False,
                notify_subscribers=False,
                publish_at=item["publish_at"],
                progress_callback=on_progress,
            )
            successes.append({**item, **result})
            consecutive_failures = 0

        except Exception as exc:
            failures.append({**item, "error": str(exc)})
            consecutive_failures += 1

            if consecutive_failures >= 3:
                break

    attempted = len(successes) + len(failures)

    update_schedule_job(
        job_id,
        state="done" if successes else "error",
        progress=100 if attempted >= total else round(attempted / total * 100, 1),
        status=f"{len(successes)} vÃ­deos enviados e agendados",
        results=successes,
        failures=failures,
        completed=len(successes),
        attempted=attempted,
        total=total,
        error=None if successes else "Nenhum vÃ­deo foi agendado.",
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/config")
def api_config():
    env = read_env()
    cuda_ok, cuda_text = check_cuda()
    nvenc_ok, nvenc_text = check_nvenc()
    captions_ok, captions_text = check_ass_filter()
    ffmpeg_ok = check_ffmpeg()

    nvidia_models = [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "meta/llama-3.3-70b-instruct",
        "deepseek-ai/deepseek-v4-flash-0731",
        "nvidia/nemotron-3-super-120b-a12b",
    ]

    configured_nvidia = env.get("NVIDIA_MODEL", nvidia_models[0]).strip()

    if configured_nvidia not in nvidia_models:
        nvidia_models.insert(0, configured_nvidia)

    configured_groq = env.get(
        "GROQ_MODEL",
        "qwen/qwen3.6-27b",
    ).strip()

    lm_status = discover_lmstudio_models(timeout=0.8)
    lm_models = list(lm_status.get("models") or [])
    configured_lmstudio = env.get("LMSTUDIO_MODEL", "").strip()

    if configured_lmstudio and configured_lmstudio not in lm_models:
        lm_models.insert(0, configured_lmstudio)

    return {
        "provider": env.get("LLM_PROVIDER", "nvidia").strip().lower(),
        "lmstudio": {
            "online": bool(lm_status.get("online")),
            "base_url": lm_status.get("base_url"),
            "model_count": len(lm_models),
        },
        "models": {
            "nvidia": nvidia_models,
            "gemini": [env.get("GEMINI_MODEL", "gemini-3.6-flash")],
            "groq": [
                env.get("GROQ_MODEL", "qwen/qwen3.6-27b"),
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "qwen/qwen3.6-27b",
                "llama-3.1-8b-instant",
            ],
            "lmstudio": lm_models,
            "openai": [env.get("OPENAI_MODEL", "gpt-4o-mini")],
        },
        "captions": {
            "available": captions_ok,
            "styles": [
                {"id": "reels_bold", "name": "Reels Bold"},
                {"id": "tiktok", "name": "TikTok White"},
                {"id": "tiktok_yellow", "name": "TikTok Yellow"},
                {"id": "tiktok_neon", "name": "TikTok Neon"},
                {"id": "tiktok_animated", "name": "TikTok Animated"},
                {"id": "karaoke", "name": "Karaoke Multicolor"},
                {"id": "multi_pop", "name": "Multi Color Pop"},
                {"id": "minimal", "name": "Minimal"},
                {"id": "box", "name": "Box"},
                {"id": "yellow_pop", "name": "Yellow Pop"},
                {"id": "headline", "name": "Headline"},
            ],
            "fonts": [
                "Arial",
                "Arial Black",
                "Segoe UI",
                "Trebuchet MS",
                "Verdana",
                "Impact",
                "Georgia",
            ],
            "positions": [
                {"id": "bottom", "name": "Inferior"},
                {"id": "center", "name": "Centro"},
                {"id": "top", "name": "Superior"},
            ],
        },
        "system": {
            "cuda": {"ok": cuda_ok, "text": cuda_text},
            "nvenc": {"ok": nvenc_ok, "text": nvenc_text},
            "ffmpeg": {
                "ok": ffmpeg_ok,
                "text": "OK" if ffmpeg_ok else "NÃ£o encontrado",
            },
            "captions": {"ok": captions_ok, "text": captions_text},
            "whisper_model": env.get("LOCAL_WHISPER_MODEL", "base"),
            "whisper_device": env.get("LOCAL_WHISPER_DEVICE", "auto"),
        },
        "defaults": {
            "quality": "1080",
            "aspect_ratio": "9:16",
            "reframe_mode": "auto",
            "transcription_language": "auto",
            "output_dir": str(OUTPUT_DIR.resolve()),
        },
        "youtube": {
            "timezone": "America/Sao_Paulo",
            "posts_per_day": 5,
            "times": ["09:00", "12:00", "15:00", "18:00", "21:00"],
        },
    }


@app.get("/api/lmstudio/models")
def api_lmstudio_models():
    status = discover_lmstudio_models(timeout=2.0)
    return {
        "online": bool(status.get("online")),
        "base_url": status.get("base_url"),
        "models": status.get("models") or [],
        "error": status.get("error"),
    }


@app.post("/api/select-folder")
def select_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Escolher pasta")
        root.destroy()
        return {"path": selected or ""}

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"NÃ£o foi possÃ­vel abrir o seletor de pastas: {exc}",
        )


@app.post("/api/jobs")
def create_job(request: JobRequest):
    url = request.youtube_url.strip()

    if not url:
        raise HTTPException(status_code=400, detail="Cole uma URL do YouTube.")

    if "youtube.com" not in url and "youtu.be" not in url:
        raise HTTPException(
            status_code=400,
            detail="A URL informada nÃ£o parece ser do YouTube.",
        )

    if request.quality not in {"720", "1080"}:
        raise HTTPException(status_code=400, detail="Qualidade invÃ¡lida.")

    if request.aspect_ratio not in {"9:16", "16:9"}:
        raise HTTPException(status_code=400, detail="Formato invÃ¡lido.")

    if request.reframe_mode not in {"auto", "person", "content"}:
        raise HTTPException(status_code=400, detail="Enquadramento invÃ¡lido.")

    if request.transcription_language not in {
        "auto", "pt", "en", "es", "fr", "de", "it"
    }:
        raise HTTPException(
            status_code=400,
            detail="Idioma de transcriÃ§Ã£o invÃ¡lido.",
        )

    if request.captions_enabled and not check_ass_filter()[0]:
        raise HTTPException(
            status_code=400,
            detail="FFmpeg sem suporte ASS/libass.",
        )

    request.youtube_url = url
    safe_directory(request.output_dir)

    job_id = uuid.uuid4().hex

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "state": "queued",
            "progress": 0,
            "status": "Na fila",
            "started_at": None,
            "finished_at": None,
            "videos": [],
            "logs": [],
            "error": None,
        }

    threading.Thread(
        target=run_generation_job,
        args=(job_id, request),
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    data = serialize_job(job_id)
    if data.get("state") != "error":
        data["logs"] = []
    return data


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str):
    serialize_job(job_id)

    def stream():
        last = None

        while True:
            data = serialize_job(job_id)
            payload = {
                "id": data["id"],
                "state": data["state"],
                "progress": data["progress"],
                "status": data["status"],
                "elapsed": data["elapsed"],
                "videos": data.get("videos", []),
                "error": data.get("error"),
            }

            if data["state"] == "error":
                payload["logs"] = data.get("logs", [])

            encoded = json.dumps(payload, ensure_ascii=False)

            if encoded != last:
                yield f"data: {encoded}\n\n"
                last = encoded
            else:
                yield ": keep-alive\n\n"

            if data["state"] in {"done", "error"}:
                break

            time.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/media/{token}")
def media(token: str):
    return FileResponse(get_media(token), media_type="video/mp4")


@app.get("/download/{token}")
def download(token: str):
    path = get_media(token)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/youtube/status")
def youtube_status():
    if YOUTUBE is None:
        return {
            "available": False,
            "dependencies": False,
            "connected": False,
            "client_configured": False,
        }

    return {"available": True, **YOUTUBE.status()}


class YouTubeSecretRequest(BaseModel):
    payload: Dict


@app.post("/api/youtube/client-secret")
def youtube_client_secret(request: YouTubeSecretRequest):
    if YOUTUBE is None:
        raise HTTPException(status_code=503, detail="YouTube indisponÃ­vel.")

    try:
        YOUTUBE.save_client_secret(request.payload)
        return YOUTUBE.status()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/youtube/auth/start")
def youtube_auth_start():
    if YOUTUBE is None:
        raise HTTPException(status_code=503, detail="YouTube indisponÃ­vel.")

    try:
        return YOUTUBE.begin_auth(
            "http://127.0.0.1:8000/api/youtube/callback"
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/youtube/callback")
def youtube_callback(request: Request, state: str = ""):
    if YOUTUBE is None:
        return HTMLResponse("YouTube indisponÃ­vel", status_code=503)

    try:
        YOUTUBE.finish_auth(state, str(request.url))
        return HTMLResponse(
            """
            <html>
            <body style="font-family:sans-serif;background:#090b11;color:white;padding:40px">
              <h2>YouTube conectado ao CutLab AI.</h2>
              <p>VocÃª pode fechar esta aba.</p>
              <script>setTimeout(()=>window.close(),1200)</script>
            </body>
            </html>
            """
        )
    except Exception as exc:
        return HTMLResponse(f"Falha ao conectar: {exc}", status_code=400)


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    if YOUTUBE is None:
        raise HTTPException(status_code=503, detail="YouTube indisponÃ­vel.")

    try:
        YOUTUBE.disconnect()
        return YOUTUBE.status()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/youtube/files")
def youtube_files(directory: str = ""):
    folder = safe_directory(directory)
    videos = [register_media(path) for path in list_final_videos(folder)]

    return {
        "directory": str(folder),
        "count": len(videos),
        "videos": videos,
    }


@app.post("/api/youtube/schedule/preview")
def schedule_preview(request: SchedulePreviewRequest):
    schedule = build_schedule(
        request.media_ids,
        request.start_date,
        request.times,
        request.posts_per_day,
        request.timezone,
    )

    return {
        "count": len(schedule),
        "days": (len(schedule) + request.posts_per_day - 1) // request.posts_per_day,
        "items": schedule,
    }


@app.post("/api/youtube/schedule/start")
def schedule_start(request: BatchScheduleRequest):
    if YOUTUBE is None:
        raise HTTPException(status_code=503, detail="YouTube indisponÃ­vel.")

    status = YOUTUBE.status()

    if not status.get("connected"):
        raise HTTPException(
            status_code=400,
            detail="Conecte o canal do YouTube primeiro.",
        )

    build_schedule(
        request.media_ids,
        request.start_date,
        request.times,
        request.posts_per_day,
        request.timezone,
    )

    job_id = uuid.uuid4().hex

    with SCHEDULE_JOBS_LOCK:
        SCHEDULE_JOBS[job_id] = {
            "id": job_id,
            "state": "queued",
            "progress": 0,
            "status": "Preparando uploads",
            "current": 0,
            "total": len(request.media_ids),
            "results": [],
            "failures": [],
            "error": None,
        }

    threading.Thread(
        target=run_schedule_job,
        args=(job_id, request),
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.get("/api/youtube/schedule/jobs/{job_id}/events")
def schedule_events(job_id: str):
    serialize_schedule_job(job_id)

    def stream():
        last = None

        while True:
            data = serialize_schedule_job(job_id)
            encoded = json.dumps(data, ensure_ascii=False)

            if encoded != last:
                yield f"data: {encoded}\n\n"
                last = encoded
            else:
                yield ": keep-alive\n\n"

            if data["state"] in {"done", "error"}:
                break

            time.sleep(0.7)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )




def update_metadata_job(
    job_id: str,
    **changes,
):
    with METADATA_JOBS_LOCK:
        if job_id in METADATA_JOBS:
            METADATA_JOBS[
                job_id
            ].update(
                changes
            )


def serialize_metadata_job(
    job_id: str,
) -> Dict:
    with METADATA_JOBS_LOCK:
        job = METADATA_JOBS.get(
            job_id
        )

        if not job:
            raise HTTPException(
                status_code=404,
                detail=(
                    "GeraÃ§Ã£o de metadados "
                    "nÃ£o encontrada."
                ),
            )

        data = dict(
            job
        )

    started = data.get(
        "started_at"
    )

    finished = data.get(
        "finished_at"
    )

    data["elapsed"] = (
        max(
            0.0,
            (
                finished
                or time.monotonic()
            )
            - started,
        )
        if started
        else 0.0
    )

    return data


def append_metadata_log(
    job_id: str,
    message: str,
):
    with METADATA_JOBS_LOCK:
        job = METADATA_JOBS.get(
            job_id
        )

        if not job:
            return

        logs = job.setdefault(
            "logs",
            [],
        )

        logs.append(
            message
        )

        if len(logs) > 100:
            del logs[:-100]


def run_metadata_generation_job(
    job_id: str,
    request: MetadataGenerateRequest,
):
    started = time.monotonic()

    update_metadata_job(
        job_id,
        state="running",
        started_at=started,
        status="Preparando geraÃ§Ã£o com IA",
        progress=1,
    )

    try:
        from youtube_publisher import (
            metadata_is_complete,
        )
    except Exception:
        def metadata_is_complete(
            metadata,
        ):
            return bool(
                metadata.get(
                    "description"
                )
                and len(
                    metadata.get(
                        "tags",
                        [],
                    )
                ) >= 8
            )

    items = []
    errors = []
    total = len(
        request.media_ids
    )

    for position, media_id in enumerate(
        request.media_ids,
        1,
    ):
        path = get_media(
            media_id
        )

        context = _metadata_context(
            path
        )

        generated_item = None
        last_error = None

        append_metadata_log(
            job_id,
            (
                f"[metadata] vÃ­deo "
                f"{position}/{total}: "
                f"{path.name}"
            ),
        )

        for attempt in range(
            1,
            4,
        ):
            base_progress = (
                (
                    position
                    - 1
                )
                / total
                * 100.0
            )

            attempt_progress = (
                (
                    attempt
                    - 1
                )
                / 3.0
                * (
                    100.0
                    / total
                )
            )

            progress = min(
                98.0,
                max(
                    2.0,
                    base_progress
                    + attempt_progress,
                ),
            )

            update_metadata_job(
                job_id,
                status=(
                    f"VÃ­deo {position}/{total} â€¢ "
                    f"tentativa {attempt}/3 â€¢ "
                    f"{request.model or request.provider}"
                ),
                progress=round(
                    progress,
                    1,
                ),
                current=position,
                total=total,
                attempt=attempt,
            )

            append_metadata_log(
                job_id,
                (
                    "[metadata] enviando para IA "
                    f"(tentativa {attempt}/3)"
                ),
            )

            try:
                prompt = (
                    build_metadata_prompt(
                        [
                            context
                        ]
                    )
                )

                if attempt > 1:
                    prompt += (
                        "\n\nA resposta anterior veio incompleta. "
                        "Retorne obrigatoriamente tÃ­tulo, descriÃ§Ã£o "
                        "completa com pelo menos 180 caracteres, "
                        "2 parÃ¡grafos, CTA, 3 a 5 hashtags e "
                        "8 a 15 tags especÃ­ficas."
                    )

                raw = _call_metadata_ai(
                    prompt,
                    request.provider,
                    request.model,
                )

                append_metadata_log(
                    job_id,
                    (
                        "[metadata] resposta recebida; "
                        "validando campos"
                    ),
                )

                generated_map = (
                    apply_generated_metadata(
                        [
                            context
                        ],
                        raw,
                    )
                )

                candidate = (
                    generated_map.get(
                        context[
                            "filename"
                        ],
                        {},
                    )
                )

                if metadata_is_complete(
                    candidate
                ):
                    generated_item = (
                        candidate
                    )

                    append_metadata_log(
                        job_id,
                        (
                            "[metadata] tÃ­tulo, descriÃ§Ã£o "
                            "e tags validados"
                        ),
                    )

                    break

                last_error = RuntimeError(
                    "A IA retornou descriÃ§Ã£o ou tags incompletas."
                )

                append_metadata_log(
                    job_id,
                    (
                        "[metadata] resposta incompleta; "
                        "nova tentativa serÃ¡ feita"
                    ),
                )

            except Exception as exc:
                last_error = exc

                append_metadata_log(
                    job_id,
                    (
                        "[metadata] erro: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

        if generated_item is None:
            errors.append(
                {
                    "filename": path.name,
                    "error": str(
                        last_error
                        or "Metadados incompletos"
                    ),
                }
            )

            append_metadata_log(
                job_id,
                (
                    "[metadata] falha apÃ³s "
                    "3 tentativas: "
                    f"{path.name}"
                ),
            )

        else:
            items.append(
                {
                    "filename": (
                        context[
                            "filename"
                        ]
                    ),
                    "title": (
                        generated_item.get(
                            "title",
                            context[
                                "highlight_title"
                            ],
                        )
                    ),
                    "description": (
                        generated_item.get(
                            "description",
                            "",
                        )
                    ),
                    "tags": (
                        generated_item.get(
                            "tags",
                            [],
                        )
                    ),
                }
            )

        update_metadata_job(
            job_id,
            progress=round(
                position
                / total
                * 100.0,
                1,
            ),
        )

    finished = time.monotonic()

    if not items:
        update_metadata_job(
            job_id,
            state="error",
            status=(
                "A IA nÃ£o conseguiu gerar "
                "metadados completos"
            ),
            progress=100,
            finished_at=finished,
            items=[],
            errors=errors,
            error=(
                errors[-1]["error"]
                if errors
                else "Nenhum metadado gerado."
            ),
        )

        return

    update_metadata_job(
        job_id,
        state="done",
        status=(
            f"{len(items)} de {total} vÃ­deo(s) "
            "com metadados completos"
        ),
        progress=100,
        finished_at=finished,
        items=items,
        errors=errors,
        generated=len(
            items
        ),
        requested=total,
    )


@app.post(
    "/api/youtube/metadata/generate"
)
def youtube_metadata_generate(
    request: MetadataGenerateRequest,
):
    if not (
        1
        <= len(request.media_ids)
        <= 50
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Selecione de 1 a 50 vÃ­deos."
            ),
        )

    if (
        build_metadata_prompt is None
        or apply_generated_metadata is None
    ):
        raise HTTPException(
            status_code=500,
            detail=(
                "Gerador de metadados "
                "indisponÃ­vel."
            ),
        )

    job_id = (
        uuid.uuid4().hex
    )

    with METADATA_JOBS_LOCK:
        METADATA_JOBS[
            job_id
        ] = {
            "id": job_id,
            "state": "queued",
            "progress": 0,
            "status": (
                "Na fila para geraÃ§Ã£o com IA"
            ),
            "started_at": None,
            "finished_at": None,
            "current": 0,
            "total": len(
                request.media_ids
            ),
            "attempt": 0,
            "items": [],
            "errors": [],
            "logs": [],
            "error": None,
        }

    threading.Thread(
        target=(
            run_metadata_generation_job
        ),
        args=(
            job_id,
            request,
        ),
        daemon=True,
    ).start()

    return {
        "job_id": job_id
    }


@app.get(
    "/api/youtube/metadata/jobs/{job_id}/events"
)
def youtube_metadata_events(
    job_id: str,
):
    serialize_metadata_job(
        job_id
    )

    def stream():
        last = None

        while True:
            data = (
                serialize_metadata_job(
                    job_id
                )
            )

            payload = {
                "id": data["id"],
                "state": data["state"],
                "progress": data.get(
                    "progress",
                    0,
                ),
                "status": data.get(
                    "status",
                    "",
                ),
                "elapsed": data.get(
                    "elapsed",
                    0,
                ),
                "current": data.get(
                    "current",
                    0,
                ),
                "total": data.get(
                    "total",
                    0,
                ),
                "attempt": data.get(
                    "attempt",
                    0,
                ),
                "items": data.get(
                    "items",
                    [],
                ),
                "errors": data.get(
                    "errors",
                    [],
                ),
                "generated": data.get(
                    "generated",
                    0,
                ),
                "requested": data.get(
                    "requested",
                    0,
                ),
                "logs": data.get(
                    "logs",
                    [],
                ),
                "error": data.get(
                    "error"
                ),
            }

            encoded = json.dumps(
                payload,
                ensure_ascii=False,
            )

            if encoded != last:
                yield (
                    f"data: {encoded}\n\n"
                )
                last = encoded
            else:
                yield (
                    ": keep-alive\n\n"
                )

            if (
                data["state"]
                in {
                    "done",
                    "error",
                }
            ):
                break

            time.sleep(
                0.6
            )

    return StreamingResponse(
        stream(),
        media_type=(
            "text/event-stream"
        ),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post(
    "/api/youtube/publish/start"
)
def youtube_publish_start(
    request: CompletePublishRequest,
):
    if YOUTUBE is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "YouTube indisponÃ­vel."
            ),
        )

    if not (
        YOUTUBE.status()
        .get(
            "connected"
        )
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Conecte o canal "
                "do YouTube primeiro."
            ),
        )

    _validate_complete_publish(
        request
    )

    job_id = (
        uuid.uuid4().hex
    )

    with SCHEDULE_JOBS_LOCK:
        SCHEDULE_JOBS[
            job_id
        ] = {
            "id": job_id,
            "state": "queued",
            "progress": 0,
            "status": (
                "Preparando uploads"
            ),
            "current": 0,
            "total": len(
                request.videos
            ),
            "results": [],
            "failures": [],
            "error": None,
        }

    threading.Thread(
        target=(
            run_complete_publish_job
        ),
        args=(
            job_id,
            request,
        ),
        daemon=True,
    ).start()

    return {
        "job_id": job_id
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


