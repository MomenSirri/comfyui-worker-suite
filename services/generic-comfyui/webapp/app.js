const els = {
  form: document.querySelector("#jobForm"),
  endpointId: document.querySelector("#endpointId"),
  apiKey: document.querySelector("#apiKey"),
  comfyApiKey: document.querySelector("#comfyApiKey"),
  rememberSettings: document.querySelector("#rememberSettings"),
  workflowFile: document.querySelector("#workflowFile"),
  workflowJson: document.querySelector("#workflowJson"),
  prompt: document.querySelector("#prompt"),
  negativePrompt: document.querySelector("#negativePrompt"),
  promptNodeId: document.querySelector("#promptNodeId"),
  negativePromptNodeId: document.querySelector("#negativePromptNodeId"),
  imageName: document.querySelector("#imageName"),
  imageFile: document.querySelector("#imageFile"),
  imageUrl: document.querySelector("#imageUrl"),
  preview: document.querySelector("#preview"),
  statusText: document.querySelector("#statusText"),
  submitButton: document.querySelector("#submitButton"),
  copyPayload: document.querySelector("#copyPayload"),
  clearResults: document.querySelector("#clearResults"),
  loadExample: document.querySelector("#loadExample"),
  responseJson: document.querySelector("#responseJson"),
  videoResults: document.querySelector("#videoResults"),
};

const settingsKey = "comfy-serverless-webapp";
let selectedImageData = "";
let workflowDetectedImageName = "";
const videoExtensions = [".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".gif"];
const comfyCreditsPerUsd = 211;
const runningRunpodStatuses = new Set(["IN_QUEUE", "IN_PROGRESS", "RETRYING"]);
const failedRunpodStatuses = new Set(["FAILED", "CANCELLED", "TIMED_OUT"]);
const pollIntervalMs = 5000;
const maxPollMs = 15 * 60 * 1000;

function setStatus(message, kind = "") {
  els.statusText.textContent = message;
  els.statusText.className = kind;
}

function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}

function parseWorkflow() {
  try {
    return JSON.parse(els.workflowJson.value);
  } catch (error) {
    throw new Error(`Workflow JSON is invalid: ${error.message}`);
  }
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function isLikelyUrl(value) {
  return /^https?:\/\//i.test(value.trim());
}

function getLoadImageNames(workflow) {
  return Object.values(workflow)
    .filter((node) => node && node.class_type === "LoadImage")
    .map((node) => node.inputs && node.inputs.image)
    .filter((name) => typeof name === "string" && name.trim())
    .map((name) => name.trim());
}

function hasClipTextEncode(workflow) {
  return Object.values(workflow).some((node) => node && node.class_type === "CLIPTextEncode");
}

function numberFromInput(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function truthyInput(value, fallback = false) {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "boolean") return value;
  return !["0", "false", "no", "off", "none", ""].includes(String(value).trim().toLowerCase());
}

function estimateKlingCostUsd(workflow) {
  let total = 0;
  const details = [];

  for (const [nodeId, node] of Object.entries(workflow)) {
    if (!node || typeof node !== "object") continue;
    if (!String(node.class_type || "").toLowerCase().includes("kling")) continue;

    const inputs = node.inputs || {};
    const modelName = String(inputs.model_name || inputs.model || "").toLowerCase();
    const resolution = String(inputs.resolution || inputs["model.resolution"] || "1080p").toLowerCase();
    const generateAudio = truthyInput(inputs.generate_audio, true);

    let duration = numberFromInput(inputs.duration, 0);
    const storyboardDurationKeys = Object.keys(inputs).filter((key) =>
      /^multi_shot\.storyboard_\d+_duration$/.test(key)
    );
    if (storyboardDurationKeys.length) {
      duration = storyboardDurationKeys.reduce(
        (sum, key) => sum + numberFromInput(inputs[key], 0),
        0
      );
    }

    if (!duration) continue;

    const mode = resolution === "4k" ? "4k" : resolution === "720p" ? "720p" : "1080p";
    const isV3 = modelName.includes("v3") || node.class_type === "KlingVideoNode";
    const rates = isV3 && generateAudio
      ? { "720p": 0.126, "1080p": 0.168, "4k": 0.42 }
      : { "720p": 0.084, "1080p": 0.112, "4k": 0.42 };
    const usd = rates[mode] * duration;
    total += usd;
    details.push({ nodeId, modelName: modelName || "kling", resolution, duration, generateAudio, usd });
  }

  if (!details.length) return null;
  return { usd: total, details };
}

function formatCostEstimate(estimate) {
  if (!estimate) return "";
  const credits = estimate.usd * comfyCreditsPerUsd;
  return `Estimated Comfy cost: ${credits.toFixed(1)} credits ($${estimate.usd.toFixed(2)})`;
}

function formatReturnedCreditUsage(output) {
  const usage = output && output.credit_usage;
  if (
    usage &&
    usage.total_estimated_credits !== null &&
    usage.total_estimated_credits !== undefined
  ) {
    const usd =
      usage.total_estimated_usd !== null && usage.total_estimated_usd !== undefined
        ? ` ($${Number(usage.total_estimated_usd).toFixed(2)})`
        : "";
    return ` Credits: ${Number(usage.total_estimated_credits).toFixed(2)}${usd}`;
  }

  const credits = output && output.comfy_credits;
  if (credits && credits.credits_spent !== null && credits.credits_spent !== undefined) {
    return ` Credits: ${credits.credits_spent}`;
  }
  return "";
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function runpodProxyRequest({ endpointId, apiKey, action, payload, jobId }) {
  const response = await fetch("/api/runpod", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpointId, apiKey, action, payload, jobId }),
  });
  const data = await response.json();
  if (!response.ok || data.error) {
    const detail = data && data.details ? ` ${JSON.stringify(data.details)}` : "";
    const error = new Error(
      (data && data.error ? data.error : "RunPod request failed") + detail
    );
    error.responseData = data;
    throw error;
  }
  return data;
}

async function waitForRunpodCompletion({ endpointId, apiKey, initialData }) {
  let data = initialData;
  const startedAt = Date.now();

  while (runningRunpodStatuses.has(String(data.status || ""))) {
    if (!data.id) {
      throw new Error(`RunPod returned ${data.status} without a job id`);
    }
    if (Date.now() - startedAt > maxPollMs) {
      throw new Error(`RunPod job ${data.id} is still ${data.status} after 15 minutes`);
    }

    setStatus(`RunPod job ${data.status.toLowerCase().replace("_", " ")}. Waiting for result...`, "");
    await sleep(pollIntervalMs);
    data = await runpodProxyRequest({
      endpointId,
      apiKey,
      action: "status",
      jobId: data.id,
    });
    els.responseJson.textContent = prettyJson(data);
    renderVideos(data);
  }

  if (failedRunpodStatuses.has(String(data.status || ""))) {
    throw new Error(`RunPod job ${data.id || ""} ended with status ${data.status}`);
  }

  return data;
}

function syncWorkflowHints() {
  try {
    const workflow = parseWorkflow();
    const imageNames = getLoadImageNames(workflow);
    const estimate = estimateKlingCostUsd(workflow);
    const parts = [];
    workflowDetectedImageName = imageNames[0] || "";

    if (workflowDetectedImageName) {
      els.imageName.value = workflowDetectedImageName;
      parts.push(`Workflow expects image filename: ${workflowDetectedImageName}`);
    }
    if (estimate) parts.push(formatCostEstimate(estimate));
    if (parts.length) setStatus(parts.join(". "), "");
  } catch {
    workflowDetectedImageName = "";
  }
}

async function buildPayload() {
  const workflow = parseWorkflow();
  const input = { workflow };

  const prompt = els.prompt.value.trim();
  const negativePrompt = els.negativePrompt.value.trim();
  const promptNodeId = els.promptNodeId.value.trim();
  const negativePromptNodeId = els.negativePromptNodeId.value.trim();
  const comfyApiKey = els.comfyApiKey.value.trim();
  const imageName = els.imageName.value.trim() || "input_image.png";
  const imageUrl = els.imageUrl.value.trim();

  const workflowHasClipText = hasClipTextEncode(workflow);
  if (prompt && (workflowHasClipText || promptNodeId)) input.prompt = prompt;
  if (negativePrompt && (workflowHasClipText || negativePromptNodeId)) {
    input.negative_prompt = negativePrompt;
  }
  if (promptNodeId) input.prompt_node_id = promptNodeId;
  if (negativePromptNodeId) input.negative_prompt_node_id = negativePromptNodeId;
  if (comfyApiKey) input.comfy_org_api_key = comfyApiKey;

  if ((prompt || negativePrompt) && !workflowHasClipText && !promptNodeId && !negativePromptNodeId) {
    console.info("Generic prompt fields ignored because this workflow has no CLIPTextEncode nodes.");
  }

  if (imageUrl) {
    if (!isLikelyUrl(imageUrl)) {
      throw new Error("Image URL must start with http:// or https://");
    }
    input.images = [{ name: imageName, url: imageUrl }];
  } else if (selectedImageData) {
    input.images = [{ name: imageName, image: selectedImageData }];
  }

  return { input };
}

function saveSettings() {
  if (!els.rememberSettings.checked) {
    localStorage.removeItem(settingsKey);
    return;
  }
  localStorage.setItem(
    settingsKey,
    JSON.stringify({
      endpointId: els.endpointId.value.trim(),
      rememberSettings: true,
    })
  );
}

function loadSettings() {
  try {
    const settings = JSON.parse(localStorage.getItem(settingsKey) || "{}");
    if (settings.endpointId) els.endpointId.value = settings.endpointId;
    els.rememberSettings.checked = Boolean(settings.rememberSettings);
  } catch {
    localStorage.removeItem(settingsKey);
  }
}

function mimeForFilename(filename) {
  const ext = filename.toLowerCase().split(".").pop();
  if (ext === "webm") return "video/webm";
  if (ext === "mov") return "video/quicktime";
  if (ext === "gif") return "image/gif";
  return "video/mp4";
}

function getOutput(runpodResponse) {
  return runpodResponse && typeof runpodResponse === "object" && "output" in runpodResponse
    ? runpodResponse.output
    : runpodResponse;
}

function mediaUrl(item) {
  if (!item || !item.data) return "";
  if (item.type === "base64") {
    return `data:${mimeForFilename(item.filename || "output.mp4")};base64,${item.data}`;
  }
  return item.data;
}

function isVideoItem(item) {
  if (!item || typeof item !== "object") return false;
  const filename = String(item.filename || "").toLowerCase();
  const data = String(item.data || "").toLowerCase();
  const mediaType = String(item.media_type || "").toLowerCase();
  return (
    mediaType === "video" ||
    videoExtensions.some((extension) => filename.endsWith(extension) || data.includes(extension))
  );
}

function collectVideoItems(runpodResponse) {
  const output = getOutput(runpodResponse) || {};
  const videos = [];

  for (const key of ["videos", "images", "files", "audio", "animated"]) {
    const items = output[key];
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      if (isVideoItem(item)) videos.push(item);
    }
  }

  if (videos.length) return videos;

  const seen = new Set();
  function walk(value) {
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    if (!value || typeof value !== "object") return;

    if (isVideoItem(value) && value.data && !seen.has(value.data)) {
      seen.add(value.data);
      videos.push(value);
    }

    for (const child of Object.values(value)) walk(child);
  }
  walk(runpodResponse);
  return videos;
}

function renderVideos(runpodResponse) {
  const videos = collectVideoItems(runpodResponse);
  els.videoResults.innerHTML = "";

  if (!videos.length) {
    els.videoResults.className = "video-results empty";
    els.videoResults.textContent = "No video links found in the response";
    return;
  }

  els.videoResults.className = "video-results";
  for (const item of videos) {
    const url = mediaUrl(item);
    const card = document.createElement("article");
    card.className = "video-card";

    const video = document.createElement("video");
    video.controls = true;
    video.src = url;
    card.append(video);

    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = item.filename ? `${item.filename}: ${url}` : url;
    card.append(link);
    els.videoResults.append(card);
  }
}

async function submitJob(event) {
  event.preventDefault();
  els.submitButton.disabled = true;
  setStatus("Sending job...", "");

  try {
    if (window.location.protocol === "file:") {
      throw new Error(
        "Open the app through http://127.0.0.1:7860, not by opening index.html directly."
      );
    }

    const payload = await buildPayload();
    const estimate = estimateKlingCostUsd(payload.input.workflow);
    const endpointId = els.endpointId.value.trim();
    const apiKey = els.apiKey.value.trim();
    saveSettings();

    let data = await runpodProxyRequest({
      endpointId,
      apiKey,
      action: "runsync",
      payload,
    });
    els.responseJson.textContent = prettyJson(data);
    renderVideos(data);
    data = await waitForRunpodCompletion({ endpointId, apiKey, initialData: data });

    const output = getOutput(data) || {};
    const creditText = formatReturnedCreditUsage(output);
    const estimateText = creditText ? "" : estimate ? ` ${formatCostEstimate(estimate)}.` : "";
    setStatus(`Completed.${creditText}${estimateText}`, "ok");
  } catch (error) {
    const message =
      error.message === "Failed to fetch"
        ? "Could not reach the local webapp proxy. Open http://127.0.0.1:7860 and keep python webapp/server.py running."
        : error.message;
    setStatus(message, "error");
    els.responseJson.textContent = prettyJson(error.responseData || { error: message });
  } finally {
    els.submitButton.disabled = false;
  }
}

function loadExample() {
  const exampleWorkflow = {
    "6": {
      inputs: {
        text: "A cinematic image-to-video shot",
        clip: ["30", 1],
      },
      class_type: "CLIPTextEncode",
      _meta: { title: "CLIP Text Encode (Positive Prompt)" },
    },
    "7": {
      inputs: {
        image: "input_image.png",
        upload: "image",
      },
      class_type: "LoadImage",
      _meta: { title: "Load Image" },
    },
  };

  els.workflowJson.value = prettyJson(exampleWorkflow);
  els.prompt.value = "A slow cinematic camera move, smooth motion, detailed lighting";
  els.promptNodeId.value = "6";
  els.imageName.value = "input_image.png";
  setStatus("Example loaded. Replace it with your exported Kling image-to-video workflow.", "");
}

els.workflowFile.addEventListener("change", async () => {
  const file = els.workflowFile.files[0];
  if (!file) return;
  els.workflowJson.value = await file.text();
  syncWorkflowHints();
});

els.workflowJson.addEventListener("blur", syncWorkflowHints);

els.workflowJson.addEventListener("input", () => {
  workflowDetectedImageName = "";
});

els.imageFile.addEventListener("change", async () => {
  const file = els.imageFile.files[0];
  if (!file) return;
  selectedImageData = await fileToDataUrl(file);
  els.imageUrl.value = "";
  if (!workflowDetectedImageName) {
    syncWorkflowHints();
  }
  els.imageName.value = workflowDetectedImageName || file.name || els.imageName.value;
  els.preview.className = "preview";
  els.preview.innerHTML = "";
  const img = document.createElement("img");
  img.src = selectedImageData;
  img.alt = "Input preview";
  els.preview.append(img);
});

els.imageUrl.addEventListener("input", () => {
  if (!els.imageUrl.value.trim()) return;
  selectedImageData = "";
  els.imageFile.value = "";
  els.preview.className = "preview";
  els.preview.innerHTML = "";
  const img = document.createElement("img");
  img.src = els.imageUrl.value.trim();
  img.alt = "Input preview";
  img.onerror = () => {
    els.preview.className = "preview empty";
    els.preview.textContent = "Preview unavailable for this URL";
  };
  els.preview.append(img);
});

els.copyPayload.addEventListener("click", async () => {
  try {
    const payload = await buildPayload();
    await navigator.clipboard.writeText(prettyJson(payload));
    setStatus("Payload copied", "ok");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

els.clearResults.addEventListener("click", () => {
  els.responseJson.textContent = "{}";
  els.videoResults.className = "video-results empty";
  els.videoResults.textContent = "No videos yet";
  setStatus("", "");
});

els.loadExample.addEventListener("click", loadExample);
els.form.addEventListener("submit", submitJob);
loadSettings();
