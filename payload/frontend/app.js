const $ = s => document.querySelector(s);

const app = $("#app");
const sidebarToggle = $("#sidebarToggle");
const provider = $("#provider");
const model = $("#model");
const lmStudioStatus = $("#lmStudioStatus");
const youtubeUrl = $("#youtubeUrl");
const quality = $("#quality");
const outputDir = $("#outputDir");
const numClips = $("#numClips");
const aspectRatio = $("#aspectRatio");
const reframeMode = $("#reframeMode");
const reframeHelp = $("#reframeHelp");
const durationHelp = $("#durationHelp");
const transcriptionLanguage = $("#transcriptionLanguage");
const captionsEnabled = $("#captionsEnabled");
const captionStyle = $("#captionStyle");
const captionFont = $("#captionFont");
const captionPosition = $("#captionPosition");
const captionSize = $("#captionSize");
const captionWords = $("#captionWords");
const captionStage = $("#captionStage");
const captionPreview = $("#captionPreview");
const generateButton = $("#generate");
const progressPanel = $("#progressPanel");
const progressStatus = $("#progressStatus");
const elapsed = $("#elapsed");
const bar = $("#bar");
const percent = $("#percent");
const success = $("#success");
const totalTime = $("#totalTime");
const errorBox = $("#error");
const errorMessage = $("#errorMessage");
const errorLogs = $("#errorLogs");
const results = $("#results");
const resultCount = $("#resultCount");
const emptyResults = $("#emptyResults");
const resultsAccordion = $("#resultsAccordion");

const scheduleFolder = $("#scheduleFolder");
const scheduleVideoCount = $("#scheduleVideoCount");
const scheduleStartDate = $("#scheduleStartDate");
const postsPerDay = $("#postsPerDay");
const scheduleTimezone = $("#scheduleTimezone");
const scheduleTimes = $("#scheduleTimes");
const schedulePreviewBox = $("#schedulePreview");
const scheduleProgress = $("#scheduleProgress");
const scheduleProgressStatus = $("#scheduleProgressStatus");
const scheduleProgressPercent = $("#scheduleProgressPercent");
const scheduleProgressBar = $("#scheduleProgressBar");
const youtubeVideoEditor = $("#youtubeVideoEditor");
const youtubeScheduleBox = $("#youtubeScheduleBox");
const startYoutubePublish = $("#startYoutubePublish");
const youtubeAccordion = $("#youtubeAccordion");

let config = null;
let pipelineSource = null;
let scheduleSource = null;
let scheduleVideos = [];
let captionTimer = null;
let captionActiveIndex = 0;
let metadataSource = null;
let metadataStartedAt = 0;


const STORAGE_KEYS = {
  outputDir: "cutlab.outputDir",
  scheduleFolder: "cutlab.scheduleFolder",
};

function saveLocalSetting(key, value) {
  try {
    localStorage.setItem(
      key,
      String(value || "")
    );
  } catch {}
}

function loadLocalSetting(key) {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}


const escapeHtml = value => String(value ?? "").replace(
  /[&<>"']/g,
  char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char])
);

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds || 0)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = value => String(value).padStart(2, "0");

  return h
    ? `${two(h)}:${two(m)}:${two(s)}`
    : `${two(m)}:${two(s)}`;
}

function setOptions(element, items, selected) {
  element.innerHTML = "";

  for (const item of items) {
    const value = typeof item === "string" ? item : item.id;
    const name = typeof item === "string" ? item : item.name;
    const option = document.createElement("option");

    option.value = value;
    option.textContent = name;
    option.selected = value === selected;

    element.appendChild(option);
  }
}

sidebarToggle.addEventListener("click", () => {
  app.classList.toggle("sidebar-collapsed");
});

function updateLmStudioStatus(message, state = "") {
  if (!lmStudioStatus) {
    return;
  }

  if (provider.value !== "lmstudio") {
    lmStudioStatus.classList.add("hidden");
    return;
  }

  lmStudioStatus.classList.remove("hidden", "online", "offline");
  if (state) {
    lmStudioStatus.classList.add(state);
  }
  lmStudioStatus.textContent = message;
}

async function refreshLmStudioModels() {
  updateLmStudioStatus("Procurando servidor local do LM Studio...");

  try {
    const response = await fetch("/api/lmstudio/models");
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Falha ao consultar LM Studio.");
    }

    if (!data.online) {
      setOptions(model, [], "");
      updateLmStudioStatus(
        `Offline â€¢ ${data.base_url || "http://127.0.0.1:1234/v1"}`,
        "offline"
      );
      return;
    }

    const values = data.models || [];
    setOptions(model, values, values[0]);

    if (values.length) {
      updateLmStudioStatus(
        `Online â€¢ ${values.length} modelo(s) detectado(s)`,
        "online"
      );
    } else {
      updateLmStudioStatus(
        "Servidor online, mas nenhum modelo foi encontrado.",
        "offline"
      );
    }
  } catch (error) {
    setOptions(model, [], "");
    updateLmStudioStatus(
      `LM Studio indisponÃ­vel â€¢ ${error?.message || error}`,
      "offline"
    );
  }
}

function fillModels() {
  if (provider.value === "lmstudio") {
    refreshLmStudioModels();
    return;
  }

  updateLmStudioStatus("");

  const rawValues = config?.models?.[provider.value] || [];
  const seen = new Set();
  const values = rawValues.filter(item => {
    const value = typeof item === "string" ? item : item.id;
    if (!value || seen.has(value)) {
      return false;
    }
    seen.add(value);
    return true;
  });

  setOptions(model, values, values[0]);
}

provider.addEventListener("change", () => fillModels());

function updateReframeHelp() {
  if (aspectRatio.value === "16:9") {
    reframeHelp.textContent = "Em 16:9 o quadro original Ã© preservado.";
    durationHelp.textContent = "Cortes horizontais: mÃ­nimo de 5 minutos.";
    return;
  }

  const descriptions = {
    auto: "O CutLab decide automaticamente entre rosto e conteÃºdo.",
    person: "Podcast/entrevista: crop vertical focado no rosto ou orador.",
    content: "Tela/texto: preserva todo o quadro com fundo desfocado.",
  };

  reframeHelp.textContent =
    descriptions[reframeMode.value] || descriptions.auto;
  durationHelp.textContent = "Cortes verticais: mÃ­nimo de 30 segundos.";
}

aspectRatio.addEventListener("change", updateReframeHelp);
reframeMode.addEventListener("change", updateReframeHelp);

const percentFilters = [
  "vignette",
  "sharpen",
  "cinematic",
  "warm",
  "cool",
  "grayscale",
];

const allFilters = [
  "vignette",
  "brightness",
  "contrast",
  "saturation",
  "sharpen",
  "cinematic",
  "warm",
  "cool",
  "grayscale",
];

for (const id of allFilters) {
  const input = $(`#${id}`);
  const output = $(`#${id}Out`);

  input.addEventListener("input", () => {
    output.textContent = percentFilters.includes(id)
      ? `${input.value}%`
      : input.value;
  });
}

function previewWords() {
  const words = [
    "ISSO",
    "MUDA",
    "TUDO",
    "NO",
    "SEU",
    "VÃDEO",
    "AGORA",
    "MESMO",
    "COM",
    "IA",
  ];

  const count = Math.max(
    2,
    Math.min(10, Number(captionWords.value || 5))
  );

  return words.slice(0, count);
}

function animatedStyle() {
  return [
    "tiktok_animated",
    "karaoke",
    "multi_pop",
  ].includes(captionStyle.value);
}

function updateCaptionPreview() {
  if (captionTimer) {
    clearInterval(captionTimer);
    captionTimer = null;
  }

  captionActiveIndex = 0;

  const words = previewWords();

  captionPreview.className =
    `caption-preview caption-${captionStyle.value}`;

  captionStage.className =
    `caption-stage caption-pos-${captionPosition.value}`;

  captionPreview.style.fontFamily =
    `"${captionFont.value}", Arial, sans-serif`;

  const selectedSize = Math.max(
    28,
    Math.min(86, Number(captionSize.value || 54))
  );

  captionPreview.style.fontSize =
    `${Math.max(22, selectedSize * 0.70)}px`;

  const draw = () => {
    captionPreview.innerHTML = words.map((word, index) => {
      const active = animatedStyle() && index === captionActiveIndex;
      return `<span class="word ${active ? "active" : ""}">${escapeHtml(word)}</span>`;
    }).join(" ");
  };

  draw();

  if (animatedStyle()) {
    captionTimer = setInterval(() => {
      captionActiveIndex = (captionActiveIndex + 1) % words.length;
      draw();
    }, 520);
  }
}

[
  captionStyle,
  captionFont,
  captionPosition,
].forEach(element => {
  element.addEventListener("change", updateCaptionPreview);
});

[
  captionSize,
  captionWords,
].forEach(element => {
  element.addEventListener("input", updateCaptionPreview);
});

async function loadConfig() {
  const response = await fetch("/api/config");
  config = await response.json();

  provider.value = config.provider || "nvidia";
  fillModels();

  quality.value = config.defaults?.quality || "1080";
  aspectRatio.value = config.defaults?.aspect_ratio || "9:16";
  reframeMode.value = config.defaults?.reframe_mode || "auto";
  transcriptionLanguage.value = config.defaults?.transcription_language || "auto";
  const savedOutputDir =
    loadLocalSetting(
      STORAGE_KEYS.outputDir
    );

  const savedScheduleFolder =
    loadLocalSetting(
      STORAGE_KEYS.scheduleFolder
    );

  outputDir.value =
    savedOutputDir
    || config.defaults?.output_dir
    || "";

  scheduleFolder.value =
    savedScheduleFolder
    || outputDir.value;

  setOptions(captionStyle, config.captions.styles, "reels_bold");
  setOptions(captionFont, config.captions.fonts, "Arial Black");
  setOptions(captionPosition, config.captions.positions, "bottom");

  scheduleTimezone.value =
    config.youtube?.timezone || "America/Sao_Paulo";

  postsPerDay.value =
    config.youtube?.posts_per_day || 5;

  scheduleTimes.value = (
    config.youtube?.times ||
    ["09:00", "12:00", "15:00", "18:00", "21:00"]
  ).join(", ");

  const system = config.system;

  $("#systemStatus").innerHTML = [
    ["CUDA", system.cuda],
    ["NVENC", system.nvenc],
    ["FFmpeg", system.ffmpeg],
    ["Legendas", system.captions],
  ].map(([name, value]) => (
    `<div class="status-item">`
    + `<b class="${value.ok ? "ok" : "bad"}">â— ${escapeHtml(name)}</b>`
    + `<br><small>${escapeHtml(value.text)}</small>`
    + `</div>`
  )).join("");

  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  scheduleStartDate.value = tomorrow.toISOString().slice(0, 10);

  updateReframeHelp();
  updateCaptionPreview();
}

async function chooseFolderFor(element) {
  const response = await fetch(
    "/api/select-folder",
    { method: "POST" }
  );

  const data = await response.json();

  if (!response.ok) {
    alert(data.detail || "Falha ao abrir seletor.");
    return;
  }

  if (data.path) {
    element.value = data.path;

    if (element === outputDir) {
      saveLocalSetting(
        STORAGE_KEYS.outputDir,
        data.path
      );
    }

    if (element === scheduleFolder) {
      saveLocalSetting(
        STORAGE_KEYS.scheduleFolder,
        data.path
      );
    }
  }
}

$("#chooseFolder").addEventListener("click", async () => {
  await chooseFolderFor(outputDir);

  if (!scheduleFolder.value) {
    scheduleFolder.value = outputDir.value;
  }
});

$("#scheduleChooseFolder").addEventListener("click", async () => {
  await chooseFolderFor(scheduleFolder);
});


outputDir.addEventListener(
  "change",
  () => {
    saveLocalSetting(
      STORAGE_KEYS.outputDir,
      outputDir.value.trim()
    );
  }
);

scheduleFolder.addEventListener(
  "change",
  () => {
    saveLocalSetting(
      STORAGE_KEYS.scheduleFolder,
      scheduleFolder.value.trim()
    );
  }
);


function generationPayload() {
  return {
    youtube_url: youtubeUrl.value.trim(),
    num_clips: Number(numClips.value),
    provider: provider.value,
    model: model.value,
    quality: quality.value,
    aspect_ratio: aspectRatio.value,
    reframe_mode: reframeMode.value,
    transcription_language: transcriptionLanguage.value,
    output_dir: outputDir.value.trim(),

    captions_enabled: captionsEnabled.checked,
    caption_style: captionStyle.value,
    caption_font: captionFont.value,
    caption_position: captionPosition.value,
    caption_size: Number(captionSize.value),
    caption_words: Number(captionWords.value),

    filter_vignette: Number($("#vignette").value),
    filter_brightness: Number($("#brightness").value),
    filter_contrast: Number($("#contrast").value),
    filter_saturation: Number($("#saturation").value),
    filter_sharpen: Number($("#sharpen").value),
    filter_cinematic: Number($("#cinematic").value),
    filter_warm: Number($("#warm").value),
    filter_cool: Number($("#cool").value),
    filter_grayscale: Number($("#grayscale").value),
  };
}

function resetGenerationUi() {
  success.classList.add("hidden");
  errorBox.classList.add("hidden");
  progressPanel.classList.remove("hidden");

  bar.style.width = "1%";
  percent.textContent = "1%";
  progressStatus.textContent = "Preparando vÃ­deo";
  elapsed.textContent = "00:00";

  results.innerHTML = "";
  emptyResults.classList.remove("hidden");
  resultCount.textContent = "0";
}

function renderVideos(videos) {
  results.innerHTML = "";
  emptyResults.classList.toggle("hidden", videos.length > 0);
  resultCount.textContent = String(videos.length);

  videos.forEach((video, index) => {
    const card = document.createElement("article");
    card.className = "video-card";

    card.innerHTML = (
      `<video controls preload="metadata" src="${escapeHtml(video.video_url)}"></video>`
      + `<div class="video-body">`
      + `<b>${String(index + 1).padStart(2, "0")} Â· ${escapeHtml(video.title)}</b>`
      + `<div class="video-actions">`
      + `<a href="${escapeHtml(video.download_url)}" download>Baixar</a>`
      + `<button class="youtube-card-action" type="button">YouTube</button>`
      + `</div></div>`
    );

    card.querySelector(
      ".youtube-card-action"
    ).addEventListener(
      "click",
      () => {
        const existing = scheduleVideos.find(
          item => item.id === video.id
        );

        if (existing) {
          existing.selected = true;
        } else {
          scheduleVideos.push({
            ...video,
            selected: true,
          });
        }

        renderYoutubeEditor();
        youtubeAccordion.open = true;
        youtubeAccordion.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }
    );

    results.appendChild(card);
  });

  resultsAccordion.open = true;

  if (videos.length > 0) {
    scheduleVideos = videos
      .slice(0, 50)
      .map(video => ({
        ...video,
        selected: true,
      }));

    renderYoutubeEditor();
  }
}

function watchGenerationJob(jobId) {
  if (pipelineSource) {
    pipelineSource.close();
  }

  pipelineSource = new EventSource(
    `/api/jobs/${jobId}/events`
  );

  pipelineSource.onmessage = event => {
    const data = JSON.parse(event.data);
    const progress = Math.max(
      1,
      Math.min(100, Number(data.progress || 0))
    );

    bar.style.width = `${progress}%`;
    percent.textContent = `${Math.round(progress)}%`;
    progressStatus.textContent = data.status || "Processando";
    elapsed.textContent = formatElapsed(data.elapsed);

    if (data.state === "done") {
      pipelineSource.close();
      generateButton.disabled = false;
      success.classList.remove("hidden");
      totalTime.textContent =
        `Tempo total: ${formatElapsed(data.elapsed)}`;
      saveLocalSetting(
        STORAGE_KEYS.outputDir,
        outputDir.value.trim()
      );

      renderVideos(data.videos || []);
    }

    if (data.state === "error") {
      pipelineSource.close();
      generateButton.disabled = false;
      errorBox.classList.remove("hidden");
      errorMessage.textContent =
        data.error || data.status || "Erro";
      errorLogs.textContent = (data.logs || []).join("\n");
    }
  };
}

generateButton.addEventListener("click", async () => {
  if (!youtubeUrl.value.trim()) {
    alert("Cole a URL do YouTube.");
    return;
  }

  resetGenerationUi();
  generateButton.disabled = true;

  const response = await fetch(
    "/api/jobs",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(generationPayload()),
    }
  );

  const data = await response.json();

  if (!response.ok) {
    generateButton.disabled = false;
    progressPanel.classList.add("hidden");
    alert(data.detail || "Falha ao iniciar.");
    return;
  }

  watchGenerationJob(data.job_id);
});

function publicationMode() {
  return (
    document.querySelector(
      'input[name="publicationMode"]:checked'
    )?.value
    || "public"
  );
}

function madeForKids() {
  return (
    document.querySelector(
      'input[name="madeForKids"]:checked'
    )?.value
    === "true"
  );
}

function selectedYoutubeVideos() {
  return scheduleVideos.filter(
    video => video.selected !== false
  );
}

function ensureMetadata(video) {
  if (!video.metadata) {
    video.metadata = {
      title: video.title || video.name || "Short",
      description:
        "Confira este destaque e compartilhe sua opiniÃ£o nos comentÃ¡rios.\n\n#Shorts",
      tags: ["shorts"],
    };
  }

  return video.metadata;
}

function updateYoutubeCount() {
  const selected = selectedYoutubeVideos().length;

  scheduleVideoCount.textContent =
    `${selected} selecionados de ${scheduleVideos.length} vÃ­deos carregados`;
}

function renderYoutubeEditor() {
  if (scheduleVideos.length === 0) {
    youtubeVideoEditor.innerHTML =
      '<div class="empty">Nenhum vÃ­deo carregado.</div>';
    updateYoutubeCount();
    return;
  }

  youtubeVideoEditor.innerHTML = "";

  scheduleVideos.forEach((video, index) => {
    const metadata = ensureMetadata(video);
    const card = document.createElement("div");

    card.className = "youtube-edit-card";

    card.innerHTML = `
      <div class="youtube-edit-head">
        <input
          class="yt-select"
          type="checkbox"
          ${video.selected === false ? "" : "checked"}
        >
        <div>
          <b>${String(index + 1).padStart(2, "0")} Â· ${escapeHtml(video.name)}</b>
          <br>
          <small>${escapeHtml(video.size_mb || "")} MB</small>
        </div>
        <button class="ghost yt-ai-one" type="button">âœ¦ IA</button>
      </div>

      <div class="youtube-edit-grid">
        <div class="full-span">
          <label>TÃ­tulo</label>
          <input
            class="yt-title"
            type="text"
            maxlength="100"
            value="${escapeHtml(metadata.title)}"
          >
        </div>

        <div>
          <label>DescriÃ§Ã£o</label>
          <textarea class="yt-description">${escapeHtml(metadata.description)}</textarea>
        </div>

        <div>
          <label>Tags separadas por vÃ­rgula</label>
          <textarea class="yt-tags">${escapeHtml((metadata.tags || []).join(", "))}</textarea>
        </div>
      </div>
    `;

    const sync = () => {
      video.selected =
        card.querySelector(".yt-select").checked;

      video.metadata = {
        title:
          card.querySelector(".yt-title").value.trim(),
        description:
          card.querySelector(".yt-description").value.trim(),
        tags:
          card.querySelector(".yt-tags").value
            .split(",")
            .map(value => value.trim())
            .filter(Boolean),
      };

      updateYoutubeCount();
    };

    card.querySelectorAll(
      ".yt-select,.yt-title,.yt-description,.yt-tags"
    ).forEach(element => {
      element.addEventListener("input", sync);
      element.addEventListener("change", sync);
    });

    card.querySelector(".yt-ai-one").addEventListener(
      "click",
      async () => {
        await generateMetadataFor([video]);
      }
    );

    youtubeVideoEditor.appendChild(card);
  });

  updateYoutubeCount();
}

async function loadScheduleVideosFromFolder() {
  const directory =
    scheduleFolder.value.trim()
    || outputDir.value.trim();

  const response = await fetch(
    "/api/youtube/files?"
    + new URLSearchParams({
      directory,
    })
  );

  const data = await response.json();

  if (!response.ok) {
    alert(data.detail || "NÃ£o foi possÃ­vel carregar a pasta.");
    return;
  }

  if ((data.videos || []).length > 50) {
    alert(
      `A pasta possui ${data.videos.length} vÃ­deos. `
      + `O limite por operaÃ§Ã£o Ã© 50. `
      + `Os primeiros 50 serÃ£o carregados.`
    );
  }

  scheduleVideos = (data.videos || [])
    .slice(0, 50)
    .map(video => ({
      ...video,
      selected: true,
    }));

  scheduleFolder.value = data.directory;

  saveLocalSetting(
    STORAGE_KEYS.scheduleFolder,
    data.directory
  );

  renderYoutubeEditor();
  schedulePreviewBox.classList.add("hidden");
}

$("#loadScheduleVideos").addEventListener(
  "click",
  loadScheduleVideosFromFolder
);

$("#selectAllYoutube").addEventListener(
  "click",
  () => {
    scheduleVideos.forEach(video => {
      video.selected = true;
    });
    renderYoutubeEditor();
  }
);

$("#clearYoutubeSelection").addEventListener(
  "click",
  () => {
    scheduleVideos.forEach(video => {
      video.selected = false;
    });
    renderYoutubeEditor();
  }
);


function ensureMetadataProgressUi() {
  let box = $("#metadataProgressBox");

  if (box) {
    return box;
  }

  box = document.createElement(
    "div"
  );

  box.id = (
    "metadataProgressBox"
  );

  box.className = (
    "metadata-progress hidden"
  );

  box.innerHTML = `
    <div class="metadata-progress-head">
      <div>
        <b id="metadataProgressStatus">
          Preparando IA
        </b>
        <small id="metadataProgressDetail">
          Aguardando...
        </small>
      </div>

      <div class="metadata-progress-time">
        <small>Tempo</small>
        <strong id="metadataElapsed">00:00</strong>
      </div>
    </div>

    <div class="track">
      <div id="metadataProgressBar"></div>
    </div>

    <div class="metadata-progress-foot">
      <span id="metadataProgressPercent">0%</span>
      <span id="metadataProgressActivity">
        Iniciando
      </span>
    </div>

    <details class="metadata-log-details">
      <summary>Log da geraÃ§Ã£o</summary>
      <pre id="metadataLogs"></pre>
    </details>
  `;

  const toolbar = (
    $("#generateMetadata")
    ?.closest(
      ".youtube-toolbar"
    )
  );

  if (toolbar) {
    toolbar.insertAdjacentElement(
      "afterend",
      box
    );
  }

  return box;
}


function setMetadataButtonsBusy(
  busy
) {
  const globalButton = (
    $("#generateMetadata")
  );

  if (globalButton) {
    globalButton.disabled = busy;

    globalButton.textContent = (
      busy
      ? "âœ¦ Gerando metadados..."
      : "âœ¦ Gerar tÃ­tulo, descriÃ§Ã£o e tags com IA"
    );
  }

  document.querySelectorAll(
    ".yt-ai-one"
  ).forEach(
    button => {
      button.disabled = busy;
    }
  );
}


function updateMetadataProgress(
  data
) {
  const box = (
    ensureMetadataProgressUi()
  );

  box.classList.remove(
    "hidden"
  );

  const progress = Math.max(
    0,
    Math.min(
      100,
      Number(
        data.progress
        || 0
      )
    )
  );

  $("#metadataProgressStatus").textContent = (
    data.status
    || "Gerando metadados"
  );

  $("#metadataElapsed").textContent = (
    formatElapsed(
      data.elapsed
      || 0
    )
  );

  $("#metadataProgressPercent").textContent = (
    `${Math.round(progress)}%`
  );

  $("#metadataProgressBar").style.width = (
    `${Math.max(1, progress)}%`
  );

  const current = Number(
    data.current
    || 0
  );

  const total = Number(
    data.total
    || 0
  );

  const attempt = Number(
    data.attempt
    || 0
  );

  $("#metadataProgressDetail").textContent = (
    total > 0
    ? (
        `VÃ­deo ${current || 1}/${total}`
        + (
          attempt > 0
          ? ` â€¢ tentativa ${attempt}/3`
          : ""
        )
      )
    : "Aguardando IA"
  );

  const logs = (
    data.logs
    || []
  );

  $("#metadataLogs").textContent = (
    logs.join("\n")
  );

  $("#metadataProgressActivity").textContent = (
    logs.length
    ? logs[
        logs.length - 1
      ]
    : "Processando"
  );
}


function applyGeneratedMetadataItems(
  chosen,
  items
) {
  const byName = new Map(
    (items || []).map(
      item => [
        item.filename,
        item,
      ]
    )
  );

  chosen.forEach(
    video => {
      const generated = (
        byName.get(
          video.name
        )
      );

      if (generated) {
        video.metadata = {
          title:
            generated.title
            || video.title
            || video.name,
          description:
            generated.description
            || "",
          tags:
            generated.tags
            || [],
        };
      }
    }
  );

  renderYoutubeEditor();
}


function watchMetadataJob(
  jobId,
  chosen
) {
  if (metadataSource) {
    metadataSource.close();
  }

  setMetadataButtonsBusy(
    true
  );

  metadataSource = (
    new EventSource(
      `/api/youtube/metadata/jobs/${jobId}/events`
    )
  );

  metadataSource.onmessage = (
    event
  ) => {
    const data = JSON.parse(
      event.data
    );

    updateMetadataProgress(
      data
    );

    if (
      data.state
      === "done"
    ) {
      metadataSource.close();
      metadataSource = null;

      applyGeneratedMetadataItems(
        chosen,
        data.items
        || []
      );

      setMetadataButtonsBusy(
        false
      );

      const failures = (
        data.errors
        || []
      );

      $("#metadataProgressActivity").textContent = (
        failures.length
        ? (
            `${data.generated || 0} gerados â€¢ `
            + `${failures.length} falharam`
          )
        : "Metadados concluÃ­dos"
      );

      if (failures.length > 0) {
        alert(
          `${data.generated || 0} de ${data.requested || chosen.length} vÃ­deo(s) receberam metadados completos. `
          + `${failures.length} ainda falharam. O log mostra os detalhes.`
        );
      }
    }

    if (
      data.state
      === "error"
    ) {
      metadataSource.close();
      metadataSource = null;

      setMetadataButtonsBusy(
        false
      );

      $("#metadataProgressActivity").textContent = (
        "Falha na geraÃ§Ã£o"
      );

      alert(
        data.error
        || data.status
        || "Falha ao gerar metadados."
      );
    }
  };

  metadataSource.onerror = () => {
    if (!metadataSource) {
      return;
    }

    $("#metadataProgressActivity").textContent = (
      "ConexÃ£o com o progresso interrompida"
    );
  };
}


async function generateMetadataFor(videos) {
  const chosen = (
    videos.filter(Boolean)
  );

  if (
    chosen.length
    === 0
  ) {
    alert(
      "Selecione pelo menos um vÃ­deo."
    );
    return;
  }

  const box = (
    ensureMetadataProgressUi()
  );

  box.classList.remove(
    "hidden"
  );

  $("#metadataProgressStatus").textContent = (
    "Iniciando geraÃ§Ã£o com IA"
  );

  $("#metadataProgressDetail").textContent = (
    `${chosen.length} vÃ­deo(s) selecionado(s)`
  );

  $("#metadataProgressPercent").textContent = (
    "0%"
  );

  $("#metadataProgressBar").style.width = (
    "1%"
  );

  $("#metadataElapsed").textContent = (
    "00:00"
  );

  $("#metadataProgressActivity").textContent = (
    "Enviando solicitaÃ§Ã£o ao servidor"
  );

  $("#metadataLogs").textContent = (
    ""
  );

  setMetadataButtonsBusy(
    true
  );

  try {
    const response = await fetch(
      "/api/youtube/metadata/generate",
      {
        method: "POST",
        headers: {
          "Content-Type": (
            "application/json"
          ),
        },
        body: JSON.stringify({
          media_ids: (
            chosen.map(
              video => video.id
            )
          ),
          provider: (
            provider.value
          ),
          model: (
            model.value
          ),
        }),
      }
    );

    const data = (
      await response.json()
    );

    if (!response.ok) {
      throw new Error(
        data.detail
        || "Falha ao iniciar geraÃ§Ã£o."
      );
    }

    $("#metadataProgressActivity").textContent = (
      "IA trabalhando..."
    );

    watchMetadataJob(
      data.job_id,
      chosen
    );

  } catch (error) {
    setMetadataButtonsBusy(
      false
    );

    $("#metadataProgressActivity").textContent = (
      "Falha ao iniciar"
    );

    $("#metadataLogs").textContent = (
      String(
        error?.message
        || error
      )
    );

    alert(
      error?.message
      || "Falha ao iniciar geraÃ§Ã£o de metadados."
    );
  }
}

$("#generateMetadata").addEventListener(
  "click",
  async () => {
    await generateMetadataFor(
      selectedYoutubeVideos()
    );
  }
);

function parsedScheduleTimes() {
  return scheduleTimes.value
    .split(",")
    .map(value => value.trim())
    .filter(Boolean);
}

function schedulePayload() {
  return {
    media_ids: selectedYoutubeVideos().map(video => video.id),
    start_date: scheduleStartDate.value,
    times: parsedScheduleTimes(),
    posts_per_day: Number(postsPerDay.value),
    timezone: scheduleTimezone.value,
  };
}

document.querySelectorAll(
  'input[name="publicationMode"]'
).forEach(element => {
  element.addEventListener(
    "change",
    () => {
      const scheduled =
        publicationMode() === "scheduled";

      youtubeScheduleBox.classList.toggle(
        "hidden",
        !scheduled
      );

      startYoutubePublish.textContent = (
        scheduled
        ? "ENVIAR E AGENDAR"
        : "ENVIAR AO YOUTUBE"
      );
    }
  );
});

async function previewSchedule() {
  const selected = selectedYoutubeVideos();

  if (selected.length === 0) {
    alert("Selecione pelo menos um vÃ­deo.");
    return;
  }

  const perDay = Number(postsPerDay.value);

  if (perDay < 1 || perDay > 10) {
    alert("Use de 1 a 10 vÃ­deos por dia.");
    return;
  }

  const response = await fetch(
    "/api/youtube/schedule/preview",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(schedulePayload()),
    }
  );

  const data = await response.json();

  if (!response.ok) {
    alert(data.detail || "Agenda invÃ¡lida.");
    return;
  }

  schedulePreviewBox.classList.remove("hidden");

  schedulePreviewBox.innerHTML = data.items.map(item => (
    `<div class="schedule-item">`
    + `<b>#${item.index}</b>`
    + `<div>${escapeHtml(item.title)}`
    + `<br><small>${escapeHtml(item.filename)}</small></div>`
    + `<div>${escapeHtml(item.local)}</div>`
    + `</div>`
  )).join("");
}

$("#previewSchedule").addEventListener(
  "click",
  previewSchedule
);

function watchScheduleJob(jobId) {
  if (scheduleSource) {
    scheduleSource.close();
  }

  scheduleProgress.classList.remove("hidden");

  scheduleSource = new EventSource(
    `/api/youtube/schedule/jobs/${jobId}/events`
  );

  scheduleSource.onmessage = event => {
    const data = JSON.parse(event.data);

    const progress = Math.max(
      0,
      Math.min(
        100,
        Number(data.progress || 0)
      )
    );

    scheduleProgressBar.style.width =
      `${Math.max(1, progress)}%`;

    scheduleProgressPercent.textContent =
      `${Math.round(progress)}%`;

    scheduleProgressStatus.textContent =
      data.status || "Enviando vÃ­deos";

    if (data.state === "done") {
      scheduleSource.close();
      startYoutubePublish.disabled = false;

      const failures = data.failures || [];

      alert(
        `${data.completed || 0} vÃ­deos enviados ao YouTube.`
        + (
          failures.length
          ? ` ${failures.length} falharam.`
          : ""
        )
        + (
          publicationMode() === "scheduled"
          ? "\n\nOs uploads concluÃ­dos jÃ¡ ficaram agendados no YouTube."
          : ""
        )
      );
    }

    if (data.state === "error") {
      scheduleSource.close();
      startYoutubePublish.disabled = false;

      alert(
        data.error
        || data.status
        || "Falha ao enviar vÃ­deos."
      );
    }
  };
}

startYoutubePublish.addEventListener(
  "click",
  async () => {
    const selected = selectedYoutubeVideos();

    if (
      selected.length < 1
      || selected.length > 50
    ) {
      alert("Selecione de 1 a 50 vÃ­deos.");
      return;
    }

    selected.forEach(ensureMetadata);

    const mode = publicationMode();
    const perDay = Number(postsPerDay.value);

    if (
      mode === "scheduled"
      && (
        perDay < 1
        || perDay > 10
      )
    ) {
      alert(
        "No agendamento, use de 1 a 10 vÃ­deos por dia."
      );
      return;
    }

    const labels = {
      public: "PUBLICAR agora como PÃšBLICO",
      unlisted: "enviar como NÃƒO LISTADO",
      private: "enviar como PRIVADO",
      scheduled: "ENVIAR E AGENDAR",
    };

    if (!confirm(
      `${labels[mode]} ${selected.length} vÃ­deo(s)?`
    )) {
      return;
    }

    startYoutubePublish.disabled = true;

    const response = await fetch(
      "/api/youtube/publish/start",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          videos: selected.map(video => ({
            media_id: video.id,
            title: video.metadata.title,
            description: video.metadata.description,
            tags: video.metadata.tags,
          })),
          publication_mode: mode,
          made_for_kids: madeForKids(),
          notify_subscribers:
            $("#notifySubscribers").checked,
          start_date: scheduleStartDate.value,
          times: parsedScheduleTimes(),
          posts_per_day: perDay,
          timezone: scheduleTimezone.value,
        }),
      }
    );

    const data = await response.json();

    if (!response.ok) {
      startYoutubePublish.disabled = false;

      alert(
        data.detail
        || "Falha ao iniciar upload."
      );
      return;
    }

    watchScheduleJob(data.job_id);
  }
);

async function youtubeStatus() {
  try {
    const response = await fetch("/api/youtube/status");
    const data = await response.json();

    let text = "OAuth ainda nÃ£o configurado";

    if (data.connected) {
      text = "Canal conectado";
    } else if (data.client_configured) {
      text = "OAuth configurado, aguardando conexÃ£o";
    }

    $("#ytStatus").textContent = text;
    $("#ytMainStatus").textContent = text;
    $("#ytConnect").disabled = !data.client_configured;
  } catch {
    $("#ytStatus").textContent = "YouTube indisponÃ­vel";
    $("#ytMainStatus").textContent = "YouTube indisponÃ­vel";
  }
}

$("#ytImport").addEventListener("click", () => {
  $("#ytSecret").click();
});

$("#ytSecret").addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) {
    return;
  }

  try {
    const payload = JSON.parse(await file.text());

    const response = await fetch(
      "/api/youtube/client-secret",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ payload }),
      }
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Falha ao importar OAuth.");
    }

    await youtubeStatus();
  } catch (error) {
    alert(error.message);
  }
});

$("#ytConnect").addEventListener("click", async () => {
  const response = await fetch(
    "/api/youtube/auth/start",
    { method: "POST" }
  );

  const data = await response.json();

  if (!response.ok) {
    alert(data.detail || "Falha ao iniciar OAuth.");
    return;
  }

  window.open(
    data.authorization_url,
    "cutlab-youtube",
    "width=720,height=780"
  );

  setTimeout(youtubeStatus, 2500);
});

$("#ytDisconnect").addEventListener("click", async () => {
  await fetch(
    "/api/youtube/disconnect",
    { method: "POST" }
  );

  await youtubeStatus();
});

async function restoreCutsAfterRefresh() {
  const directory =
    outputDir.value.trim()
    || config?.defaults?.output_dir
    || "";

  if (!directory) {
    return;
  }

  try {
    const response = await fetch(
      "/api/youtube/files?"
      + new URLSearchParams({
        directory,
      })
    );

    const data = await response.json();

    if (
      response.ok
      && (data.videos || []).length > 0
    ) {
      renderVideos(
        (data.videos || []).slice(0, 50)
      );

      scheduleFolder.value =
        data.directory;
    }

  } catch (error) {
    console.warn(
      "CutLab: nÃ£o foi possÃ­vel restaurar os cortes.",
      error
    );
  }
}

async function bootCutLab() {
  await loadConfig();

  await Promise.all([
    youtubeStatus(),
    restoreCutsAfterRefresh(),
  ]);
}

bootCutLab();


