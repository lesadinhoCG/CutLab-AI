import os
import re
import queue
import threading
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import streamlit as st


# ============================================================
# PROJECT PATHS
# ============================================================

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
ENV_FILE = ROOT / ".env"

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# STREAMLIT PAGE
# ============================================================

st.set_page_config(
    page_title="CutLab AI",
    page_icon="âœ¦",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>

/* =========================================================
   GLOBAL
   ========================================================= */

html,
body,
[class*="css"] {
    font-family:
        Inter,
        ui-sans-serif,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}

.stApp {
    background:
        radial-gradient(
            circle at 18% 0%,
            rgba(99, 102, 241, 0.16),
            transparent 26%
        ),
        radial-gradient(
            circle at 88% 6%,
            rgba(168, 85, 247, 0.11),
            transparent 24%
        ),
        linear-gradient(
            180deg,
            #090b11 0%,
            #080a0f 100%
        );

    color: #f5f7fb;
}

.block-container {
    max-width: 1500px;
    padding-top: 1.6rem;
    padding-bottom: 5rem;
}


/* =========================================================
   SIDEBAR
   ========================================================= */

[data-testid="stSidebar"] {
    background:
        linear-gradient(
            180deg,
            #0e1119 0%,
            #090b11 100%
        );

    border-right:
        1px solid
        rgba(255, 255, 255, 0.06);
}

[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.3rem;
}


/* =========================================================
   BRAND
   ========================================================= */

.cutlab-header {
    display: flex;
    align-items: center;
    gap: 18px;

    min-height: 110px;

    padding:
        24px
        28px;

    margin-bottom: 28px;

    border:
        1px solid
        rgba(255,255,255,0.075);

    border-radius: 24px;

    background:
        linear-gradient(
            120deg,
            rgba(30, 32, 53, 0.88),
            rgba(17, 19, 28, 0.74)
        );

    box-shadow:
        0 22px 70px
        rgba(0, 0, 0, 0.25);

    backdrop-filter:
        blur(18px);
}

.cutlab-logo {
    width: 62px;
    height: 62px;

    display: flex;
    align-items: center;
    justify-content: center;

    border-radius: 18px;

    font-size: 28px;
    font-weight: 900;

    color: white;

    background:
        linear-gradient(
            135deg,
            #6366f1,
            #8b5cf6,
            #a855f7
        );

    box-shadow:
        0 12px 34px
        rgba(99, 102, 241, 0.32);
}

.cutlab-name {
    font-size: 46px;
    line-height: 1;
    font-weight: 900;

    letter-spacing: -2.4px;

    background:
        linear-gradient(
            90deg,
            #ffffff 0%,
            #d8d9ff 42%,
            #b6a8ff 100%
        );

    -webkit-background-clip: text;
    background-clip: text;

    -webkit-text-fill-color:
        transparent;
}


/* =========================================================
   SECTION TITLES
   ========================================================= */

.section-title {
    margin:
        6px
        0
        16px
        0;

    font-size: 23px;
    font-weight: 760;

    letter-spacing: -0.6px;

    color: #f4f5f8;
}

.section-note {
    margin-top: -10px;
    margin-bottom: 18px;

    font-size: 13px;

    color: #7e8492;
}


/* =========================================================
   INFO / STATUS CARDS
   ========================================================= */

.info-card {
    min-height: 84px;

    padding:
        17px
        18px;

    border-radius: 17px;

    background:
        rgba(255,255,255,0.026);

    border:
        1px solid
        rgba(255,255,255,0.055);
}

.info-label {
    color: #7e8492;

    font-size: 11px;
    font-weight: 700;

    text-transform: uppercase;

    letter-spacing: 0.9px;

    margin-bottom: 7px;
}

.info-value {
    color: #f4f6fa;

    font-size: 18px;
    font-weight: 760;

    letter-spacing: -0.4px;
}


/* =========================================================
   INPUTS
   ========================================================= */

.stTextInput input {
    min-height: 50px;

    border-radius:
        14px !important;

    background:
        #10131b !important;

    border:
        1px solid
        rgba(255,255,255,0.075) !important;

    color:
        #f5f7fb !important;
}

.stTextInput input:focus {
    border:
        1px solid
        rgba(129, 140, 248, 0.78) !important;

    box-shadow:
        0 0 0 1px
        rgba(129, 140, 248, 0.18) !important;
}

div[data-baseweb="select"] > div {
    min-height: 48px;

    border-radius:
        13px !important;

    background:
        #10131b !important;

    border-color:
        rgba(255,255,255,0.075) !important;
}


/* =========================================================
   PRIMARY BUTTON
   ========================================================= */

.stButton > button[kind="primary"] {
    min-height: 54px;

    border: 0;

    border-radius: 15px;

    background:
        linear-gradient(
            90deg,
            #6366f1,
            #805ad5,
            #9333ea
        );

    color: white;

    font-size: 15px;
    font-weight: 800;

    letter-spacing: 0.4px;

    box-shadow:
        0 15px 34px
        rgba(99, 102, 241, 0.23);

    transition:
        transform 0.15s ease,
        box-shadow 0.15s ease;
}

.stButton > button[kind="primary"]:hover {
    transform:
        translateY(-1px);

    box-shadow:
        0 19px 40px
        rgba(99, 102, 241, 0.32);
}


/* =========================================================
   NORMAL BUTTON
   ========================================================= */

.stButton > button:not([kind="primary"]) {
    border-radius: 13px;

    border:
        1px solid
        rgba(255,255,255,0.075);

    background:
        rgba(255,255,255,0.035);
}


/* =========================================================
   METRICS
   ========================================================= */

[data-testid="stMetric"] {
    padding:
        16px
        17px;

    border-radius:
        16px;

    background:
        rgba(255,255,255,0.025);

    border:
        1px solid
        rgba(255,255,255,0.055);
}


/* =========================================================
   PROGRESS
   ========================================================= */

.stProgress > div > div {
    border-radius: 999px;
}


/* =========================================================
   VIDEO
   ========================================================= */

video {
    border-radius: 16px !important;

    background: #000;

    border:
        1px solid
        rgba(255,255,255,0.07);
}


/* =========================================================
   DOWNLOAD
   ========================================================= */

.stDownloadButton > button {
    width: 100%;

    min-height: 43px;

    border-radius: 12px;

    font-weight: 700;
}


/* =========================================================
   EXPANDERS
   ========================================================= */

[data-testid="stExpander"] {
    border-radius: 15px;

    border:
        1px solid
        rgba(255,255,255,0.06);

    background:
        rgba(255,255,255,0.018);
}


/* =========================================================
   STREAMLIT CLEANUP
   ========================================================= */

#MainMenu {
    visibility: hidden;
}

footer {
    visibility: hidden;
}

header {
    background: transparent !important;
}

</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# ENV
# ============================================================

def read_env() -> Dict[str, str]:
    """
    Load .env values without showing secrets.
    """

    result: Dict[str, str] = {}

    if not ENV_FILE.exists():
        return result

    try:
        from dotenv import dotenv_values

        values = dotenv_values(
            ENV_FILE
        )

        for key, value in values.items():

            if value is not None:

                result[
                    str(key)
                ] = str(
                    value
                ).strip()

        return result

    except Exception:
        pass

    try:

        lines = (
            ENV_FILE
            .read_text(
                encoding="utf-8"
            )
            .splitlines()
        )

        for line in lines:

            line = line.strip()

            if not line:
                continue

            if line.startswith(
                "#"
            ):
                continue

            if "=" not in line:
                continue

            key, value = (
                line.split(
                    "=",
                    1,
                )
            )

            result[
                key.strip()
            ] = value.strip()

    except Exception:
        pass

    return result


# ============================================================
# SYSTEM CHECKS
# ============================================================

@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def get_gpu_name() -> str:

    try:

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )

        if (
            result.returncode
            == 0
        ):

            name = (
                result.stdout
                .strip()
                .splitlines()
            )

            if name:
                return name[0]

    except Exception:
        pass

    return "NVIDIA GPU"


@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def check_cuda() -> Tuple[
    bool,
    str,
]:

    try:

        import ctranslate2

        gpu_count = (
            ctranslate2
            .get_cuda_device_count()
        )

        if gpu_count > 0:

            return (
                True,
                get_gpu_name(),
            )

        return (
            False,
            "CUDA nÃ£o detectada",
        )

    except Exception as exc:

        return (
            False,
            str(exc),
        )


@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def check_ffmpeg() -> bool:

    try:

        result = subprocess.run(
            [
                "ffmpeg",
                "-version",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )

        return (
            result.returncode
            == 0
        )

    except Exception:
        return False


@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def check_nvenc() -> Tuple[
    bool,
    str,
]:

    try:

        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-encoders",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )

        output = (
            (result.stdout or "")
            + (result.stderr or "")
        )

        if "h264_nvenc" in output:

            return (
                True,
                "H.264 NVENC",
            )

        return (
            False,
            "NÃ£o disponÃ­vel",
        )

    except Exception as exc:

        return (
            False,
            str(exc),
        )


# ============================================================
# VIDEO OUTPUT HELPERS
# ============================================================

def is_final_short(
    path: Path,
) -> bool:

    name = (
        path.name.lower()
    )

    if name.startswith(
        "source_"
    ):
        return False

    blocked_suffixes = (
        ".cut.mp4",
        ".silent.mp4",
        ".render.mp4",
        ".temp.mp4",
    )

    if any(
        name.endswith(
            suffix
        )
        for suffix
        in blocked_suffixes
    ):
        return False

    return True


def list_final_shorts() -> List[Path]:

    videos: List[Path] = []

    for path in OUTPUT_DIR.glob(
        "*.mp4"
    ):

        if is_final_short(
            path
        ):

            videos.append(
                path
            )

    return videos


def snapshot_output() -> Dict[
    str,
    Tuple[int, float],
]:

    snapshot: Dict[
        str,
        Tuple[int, float],
    ] = {}

    for path in list_final_shorts():

        try:

            stat = (
                path.stat()
            )

            snapshot[
                str(path.resolve())
            ] = (
                stat.st_size,
                stat.st_mtime,
            )

        except OSError:
            pass

    return snapshot


def find_generated_shorts(
    before: Dict[
        str,
        Tuple[int, float],
    ]
) -> List[Path]:

    generated: List[Path] = []

    for path in list_final_shorts():

        try:

            stat = (
                path.stat()
            )

        except OSError:
            continue

        key = str(
            path.resolve()
        )

        current = (
            stat.st_size,
            stat.st_mtime,
        )

        previous = (
            before.get(
                key
            )
        )

        if (
            previous is None
            or current != previous
        ):

            generated.append(
                path
            )

    generated.sort(
        key=lambda p:
            p.stat().st_mtime
    )

    return generated


def recent_shorts(
    limit: int = 12,
) -> List[Path]:

    videos = (
        list_final_shorts()
    )

    videos.sort(
        key=lambda p:
            p.stat().st_mtime,
        reverse=True,
    )

    return videos[
        :limit
    ]


# ============================================================
# LOG ANALYSIS
# ============================================================

def analyze_log(
    line: str,
) -> Tuple[
    Optional[int],
    Optional[str],
    Optional[str],
]:

    lower = (
        line.lower()
    )

    # --------------------------------------------------------
    # Download
    # --------------------------------------------------------

    if (
        "[download/local]"
        in lower
    ):

        if (
            "reusing cached"
            in lower
            or "ready"
            in lower
        ):

            return (
                12,
                "VÃ­deo pronto",
                "download",
            )

        return (
            5,
            "Baixando vÃ­deo",
            "download",
        )

    # --------------------------------------------------------
    # Whisper
    # --------------------------------------------------------

    if (
        "[transcribe/local]"
        in lower
    ):

        if (
            "cached segments"
            in lower
            or "wrote cache"
            in lower
            or " segments,"
            in lower
        ):

            return (
                32,
                "TranscriÃ§Ã£o concluÃ­da",
                "whisper",
            )

        if (
            "device=cuda"
            in lower
        ):

            return (
                18,
                "Whisper usando CUDA",
                "whisper",
            )

        return (
            18,
            "Transcrevendo Ã¡udio",
            "whisper",
        )

    # --------------------------------------------------------
    # LLM
    # --------------------------------------------------------

    if (
        "[llm/local]"
        in lower
    ):

        return (
            35,
            "Conectando Ã  IA",
            "ai",
        )

    if (
        "[llm/nvidia]"
        in lower
    ):

        if (
            "sending request"
            in lower
        ):

            return (
                38,
                "NVIDIA analisando",
                "ai",
            )

        if (
            "response received"
            in lower
        ):

            return (
                42,
                "Resposta da NVIDIA recebida",
                "ai",
            )

    # --------------------------------------------------------
    # Highlights
    # --------------------------------------------------------

    chunk_match = re.search(
        r"chunk\s+(\d+)/(\d+)",
        lower,
    )

    if chunk_match:

        current = int(
            chunk_match.group(1)
        )

        total = max(
            1,
            int(
                chunk_match.group(2)
            ),
        )

        progress = (
            36
            + int(
                34
                * (
                    current
                    / total
                )
            )
        )

        return (
            progress,
            (
                f"Analisando bloco "
                f"{current}/{total}"
            ),
            "ai",
        )

    if (
        "global ranking"
        in lower
    ):

        return (
            72,
            "Selecionando os melhores cortes",
            "ai",
        )

    if (
        "after dedupe"
        in lower
    ):

        return (
            70,
            "Candidatos preparados",
            "ai",
        )

    # --------------------------------------------------------
    # Reframe
    # --------------------------------------------------------

    if (
        "[reframe/local]"
        in lower
    ):

        return (
            78,
            "Calculando enquadramento",
            "render",
        )

    # --------------------------------------------------------
    # Render
    # --------------------------------------------------------

    clip_match = re.search(
        r"\[clip/local\].*?(\d+)/(\d+)",
        lower,
    )

    if clip_match:

        current = int(
            clip_match.group(1)
        )

        total = max(
            1,
            int(
                clip_match.group(2)
            ),
        )

        progress = (
            78
            + int(
                20
                * (
                    current
                    / total
                )
            )
        )

        return (
            min(
                98,
                progress,
            ),
            (
                f"Renderizando "
                f"{current}/{total}"
            ),
            "render",
        )

    if (
        "[clip/local]"
        in lower
        and "rendering"
        in lower
    ):

        return (
            82,
            "Renderizando Short",
            "render",
        )

    return (
        None,
        None,
        None,
    )


# ============================================================
# ELAPSED TIME
# ============================================================

def format_elapsed(
    seconds: float,
) -> str:
    """Format elapsed seconds as HH:MM:SS or MM:SS."""

    total = max(
        0,
        int(seconds),
    )

    hours = (
        total // 3600
    )

    minutes = (
        (total % 3600)
        // 60
    )

    secs = (
        total % 60
    )

    if hours > 0:

        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{secs:02d}"
        )

    return (
        f"{minutes:02d}:"
        f"{secs:02d}"
    )


# ============================================================
# PIPELINE
# ============================================================

def run_pipeline(
    youtube_url: str,
    num_clips: int,
    provider: str,
    model: str,
):

    before_snapshot = (
        snapshot_output()
    )

    command = [
        sys.executable,
        str(
            ROOT
            / "main.py"
        ),
        youtube_url,
        "--mode",
        "local",
        "--num-clips",
        str(
            num_clips
        ),
    ]

    environment = (
        os.environ.copy()
    )

    environment[
        "PYTHONIOENCODING"
    ] = "utf-8"

    environment[
        "LLM_PROVIDER"
    ] = provider

    if provider == "nvidia":

        environment[
            "NVIDIA_MODEL"
        ] = model

    elif provider == "gemini":

        environment[
            "GEMINI_MODEL"
        ] = model

    elif provider == "openai":

        environment[
            "OPENAI_MODEL"
        ] = model

    creationflags = 0

    if (
        os.name == "nt"
        and hasattr(
            subprocess,
            "CREATE_NO_WINDOW",
        )
    ):

        creationflags = (
            subprocess.CREATE_NO_WINDOW
        )

    # ========================================================
    # CLEAN PROGRESS UI
    # ========================================================

    progress_bar = st.progress(
        1,
        text="Preparando processamento...",
    )

    info_columns = st.columns(
        [3, 1]
    )

    with info_columns[0]:

        status_placeholder = (
            st.empty()
        )

    with info_columns[1]:

        elapsed_placeholder = (
            st.empty()
        )

    status_placeholder.markdown(
        "**Preparando vÃ­deo...**"
    )

    elapsed_placeholder.markdown(
        "**Tempo decorrido**  \n00:00"
    )

    # ========================================================
    # START PROCESS
    # ========================================================

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )

    assert (
        process.stdout
        is not None
    )

    started_at = (
        time.monotonic()
    )

    logs: List[str] = []

    output_queue = (
        queue.Queue()
    )

    # ========================================================
    # BACKGROUND STDOUT READER
    # ========================================================

    def read_process_output():

        try:

            for line in (
                process.stdout
            ):

                output_queue.put(
                    line.rstrip()
                )

        finally:

            output_queue.put(
                None
            )

    reader_thread = threading.Thread(
        target=read_process_output,
        daemon=True,
    )

    reader_thread.start()

    # ========================================================
    # PROGRESS STATE
    # ========================================================

    current_progress = 1

    current_status = (
        "Preparando vÃ­deo..."
    )

    output_finished = False

    while True:

        # ----------------------------------------------------
        # Consume available backend logs
        # ----------------------------------------------------

        while True:

            try:

                line = (
                    output_queue
                    .get_nowait()
                )

            except queue.Empty:
                break

            if line is None:

                output_finished = True
                break

            if not line:
                continue

            logs.append(
                line
            )

            (
                detected_progress,
                detected_status,
                _stage,
            ) = analyze_log(
                line
            )

            if (
                detected_progress
                is not None
            ):

                current_progress = max(
                    current_progress,
                    detected_progress,
                )

            if detected_status:

                current_status = (
                    detected_status
                )

        # ----------------------------------------------------
        # Update elapsed time continuously
        # ----------------------------------------------------

        elapsed = (
            time.monotonic()
            - started_at
        )

        elapsed_text = (
            format_elapsed(
                elapsed
            )
        )

        elapsed_placeholder.markdown(
            "**Tempo decorrido**  \n"
            f"{elapsed_text}"
        )

        status_placeholder.markdown(
            f"**{current_status}**"
        )

        progress_bar.progress(
            min(
                current_progress,
                99,
            ),
            text=(
                f"{current_progress}%"
            ),
        )

        # ----------------------------------------------------
        # Finished?
        # ----------------------------------------------------

        if (
            process.poll()
            is not None
            and output_finished
        ):

            break

        time.sleep(
            0.20
        )

    return_code = (
        process.wait()
    )

    total_elapsed = (
        time.monotonic()
        - started_at
    )

    total_elapsed_text = (
        format_elapsed(
            total_elapsed
        )
    )

    # ========================================================
    # ERROR
    # ========================================================

    if return_code != 0:

        progress_bar.progress(
            min(
                current_progress,
                99,
            ),
            text="Erro",
        )

        status_placeholder.error(
            "O processamento foi interrompido."
        )

        elapsed_placeholder.markdown(
            "**Tempo decorrido**  \n"
            f"{total_elapsed_text}"
        )

        return {
            "success": False,
            "logs": logs,
            "videos": [],
            "elapsed": total_elapsed,
        }

    # ========================================================
    # SUCCESS
    # ========================================================

    progress_bar.progress(
        100,
        text="100%",
    )

    status_placeholder.success(
        "Shorts gerados com sucesso."
    )

    elapsed_placeholder.markdown(
        "**Tempo total**  \n"
        f"{total_elapsed_text}"
    )

    videos = (
        find_generated_shorts(
            before_snapshot
        )
    )

    return {
        "success": True,
        "logs": logs,
        "videos": videos,
        "elapsed": total_elapsed,
    }


# ============================================================
# VIDEO CARD
# ============================================================

def show_video_card(
    path: Path,
    index: int,
):

    raw_title = (
        path.stem
    )

    title = re.sub(
        r"^\d+\s*-\s*",
        "",
        raw_title,
    )

    st.markdown(
        f"### {index:02d} Â· {title}"
    )

    st.video(
        str(
            path
        )
    )

    try:

        stat = (
            path.stat()
        )

        size_mb = (
            stat.st_size
            / 1024
            / 1024
        )

        st.caption(
            f"{size_mb:.1f} MB"
        )

        data = (
            path.read_bytes()
        )

        st.download_button(
            label="Baixar MP4",
            data=data,
            file_name=path.name,
            mime="video/mp4",
            key=(
                f"download_"
                f"{index}_"
                f"{path.name}"
            ),
            use_container_width=True,
        )

    except Exception as exc:

        st.warning(
            "NÃ£o foi possÃ­vel preparar "
            f"o download: {exc}"
        )


# ============================================================
# BRAND HEADER
# ============================================================

st.markdown(
    """
<div class="cutlab-header">
    <div class="cutlab-logo">âœ¦</div>
    <div class="cutlab-name">CutLab AI</div>
</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# ENV / SYSTEM INFORMATION
# ============================================================

env_values = (
    read_env()
)

cuda_ok, cuda_text = (
    check_cuda()
)

nvenc_ok, nvenc_text = (
    check_nvenc()
)

ffmpeg_ok = (
    check_ffmpeg()
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown(
        "## CutLab AI"
    )

    st.caption(
        "ConfiguraÃ§Ã£o do processamento"
    )

    st.divider()

    # --------------------------------------------------------
    # Provider
    # --------------------------------------------------------

    provider_options = [
        "nvidia",
        "gemini",
        "openai",
    ]

    configured_provider = (
        env_values
        .get(
            "LLM_PROVIDER",
            "nvidia",
        )
        .strip()
        .lower()
    )

    if (
        configured_provider
        not in provider_options
    ):

        configured_provider = (
            "nvidia"
        )

    provider = st.selectbox(
        "Provedor de IA",
        provider_options,
        index=(
            provider_options.index(
                configured_provider
            )
        ),
        format_func=lambda value: {
            "nvidia": "NVIDIA NIM",
            "gemini": "Google Gemini",
            "openai": "OpenAI",
        }.get(
            value,
            value,
        ),
    )

    # --------------------------------------------------------
    # NVIDIA models
    # --------------------------------------------------------

    if (
        provider
        == "nvidia"
    ):

        configured_model = (
            env_values
            .get(
                "NVIDIA_MODEL",
                "nvidia/nemotron-3.5-lightning-30b-a3b",
            )
            .strip()
        )

        nvidia_models = [
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "nvidia/llama-3.3-nemotron-super-49b-v1.5",
            "meta/llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-v4-flash-0731",
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-ultra-550b-a55b",
        ]

        if (
            configured_model
            not in nvidia_models
        ):

            nvidia_models.insert(
                0,
                configured_model,
            )

        model = st.selectbox(
            "Modelo",
            nvidia_models,
            index=(
                nvidia_models.index(
                    configured_model
                )
            ),
        )

    # --------------------------------------------------------
    # Gemini
    # --------------------------------------------------------

    elif (
        provider
        == "gemini"
    ):

        model = st.text_input(
            "Modelo",
            value=(
                env_values.get(
                    "GEMINI_MODEL",
                    "gemini-3.6-flash",
                )
            ),
        )

    # --------------------------------------------------------
    # OpenAI
    # --------------------------------------------------------

    else:

        model = st.text_input(
            "Modelo",
            value=(
                env_values.get(
                    "OPENAI_MODEL",
                    "gpt-4o-mini",
                )
            ),
        )

    st.divider()

    # --------------------------------------------------------
    # System
    # --------------------------------------------------------

    st.markdown(
        "### Sistema"
    )

    if cuda_ok:

        st.success(
            f"CUDA Â· {cuda_text}"
        )

    else:

        st.error(
            "CUDA indisponÃ­vel"
        )

    if nvenc_ok:

        st.success(
            f"NVENC Â· {nvenc_text}"
        )

    else:

        st.warning(
            "NVENC indisponÃ­vel"
        )

    if ffmpeg_ok:

        st.success(
            "FFmpeg Â· OK"
        )

    else:

        st.error(
            "FFmpeg nÃ£o encontrado"
        )

    st.divider()

    st.caption(
        "Whisper"
    )

    st.write(
        env_values.get(
            "LOCAL_WHISPER_MODEL",
            "base",
        )
    )

    st.caption(
        "Dispositivo"
    )

    st.write(
        env_values.get(
            "LOCAL_WHISPER_DEVICE",
            "auto",
        )
    )


# ============================================================
# MAIN INPUT
# ============================================================

st.markdown(
    '<div class="section-title">Novo projeto</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="section-note">Cole o vÃ­deo e escolha quantos cortes deseja gerar.</div>',
    unsafe_allow_html=True,
)


input_col, clips_col = (
    st.columns(
        [3.2, 1]
    )
)

with input_col:

    youtube_url = (
        st.text_input(
            "URL do YouTube",
            placeholder=(
                "https://www.youtube.com/watch?v=..."
            ),
            label_visibility="collapsed",
        )
    )

with clips_col:

    num_clips = st.selectbox(
        "Quantidade de cortes",
        [
            3,
            5,
            7,
            10,
            15,
            20,
        ],
        index=3,
        label_visibility="collapsed",
    )


# ============================================================
# PROJECT INFORMATION
# ============================================================

info_1, info_2, info_3, info_4 = (
    st.columns(4)
)

with info_1:

    st.markdown(
        f"""
<div class="info-card">
    <div class="info-label">Cortes</div>
    <div class="info-value">{num_clips}</div>
</div>
""",
        unsafe_allow_html=True,
    )

with info_2:

    st.markdown(
        """
<div class="info-card">
    <div class="info-label">Formato</div>
    <div class="info-value">9:16</div>
</div>
""",
        unsafe_allow_html=True,
    )

with info_3:

    provider_display = {
        "nvidia": "NVIDIA",
        "gemini": "Gemini",
        "openai": "OpenAI",
    }.get(
        provider,
        provider,
    )

    st.markdown(
        f"""
<div class="info-card">
    <div class="info-label">InteligÃªncia</div>
    <div class="info-value">{provider_display}</div>
</div>
""",
        unsafe_allow_html=True,
    )

with info_4:

    render_name = (
        "NVENC"
        if nvenc_ok
        else "CPU"
    )

    st.markdown(
        f"""
<div class="info-card">
    <div class="info-label">Render</div>
    <div class="info-value">{render_name}</div>
</div>
""",
        unsafe_allow_html=True,
    )


# ============================================================
# GENERATE BUTTON
# ============================================================

st.write("")

generate_button = st.button(
    "âœ¦  GERAR SHORTS",
    type="primary",
    use_container_width=True,
)


# ============================================================
# RUN
# ============================================================

if generate_button:

    url = (
        youtube_url
        .strip()
    )

    if not url:

        st.error(
            "Cole a URL do vÃ­deo."
        )

        st.stop()

    if (
        "youtube.com"
        not in url
        and "youtu.be"
        not in url
    ):

        st.error(
            "A URL informada nÃ£o parece "
            "ser um vÃ­deo do YouTube."
        )

        st.stop()

    st.divider()

    st.markdown(
        '<div class="section-title">Processamento</div>',
        unsafe_allow_html=True,
    )

    result = run_pipeline(
        youtube_url=url,
        num_clips=int(
            num_clips
        ),
        provider=provider,
        model=model,
    )

    st.session_state[
        "last_result"
    ] = result


# ============================================================
# RESULTS
# ============================================================

last_result = (
    st.session_state.get(
        "last_result"
    )
)

if last_result:

    st.divider()

    if (
        last_result.get(
            "success"
        )
    ):

        videos = (
            last_result.get(
                "videos",
                [],
            )
        )

        st.markdown(
            '<div class="section-title">Shorts gerados</div>',
            unsafe_allow_html=True,
        )

        if videos:

            st.caption(
                f"{len(videos)} arquivo(s) gerado(s)"
            )

            for row_start in range(
                0,
                len(videos),
                3,
            ):

                columns = (
                    st.columns(3)
                )

                row = (
                    videos[
                        row_start:
                        row_start + 3
                    ]
                )

                for offset, (
                    column,
                    video,
                ) in enumerate(
                    zip(
                        columns,
                        row,
                    )
                ):

                    with column:

                        with st.container(
                            border=True
                        ):

                            show_video_card(
                                video,
                                row_start
                                + offset
                                + 1,
                            )

        else:

            st.warning(
                "O processamento foi concluÃ­do, "
                "mas nenhum novo MP4 final "
                "foi detectado."
            )

    else:

        st.error(
            "O processamento terminou "
            "com erro."
        )

        logs = (
            last_result.get(
                "logs",
                [],
            )
        )

        with st.expander(
            "Ver log completo",
            expanded=True,
        ):

            st.code(
                "\n".join(
                    logs
                ),
                language="text",
            )


# ============================================================
# RECENT SHORTS
# ============================================================

st.divider()

with st.expander(
    "Shorts recentes",
    expanded=False,
):

    recent = (
        recent_shorts(
            limit=12
        )
    )

    if not recent:

        st.caption(
            "Nenhum Short encontrado."
        )

    else:

        for row_start in range(
            0,
            len(recent),
            4,
        ):

            columns = (
                st.columns(4)
            )

            row = (
                recent[
                    row_start:
                    row_start + 4
                ]
            )

            for (
                column,
                video,
            ) in zip(
                columns,
                row,
            ):

                with column:

                    st.video(
                        str(video)
                    )

                    title = re.sub(
                        r"^\d+\s*-\s*",
                        "",
                        video.stem,
                    )

                    st.caption(
                        title
                    )

