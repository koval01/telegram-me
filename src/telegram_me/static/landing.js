// Landing page: service health and the "Try it" box.

const features = JSON.parse(document.body.dataset.features || "{}");
const assets = document.body.dataset.assets || "";
const CHANNEL = /^[a-zA-Z][a-zA-Z0-9_]{3,31}$/;
const $ = (id) => document.getElementById(id);

// Swagger UI used to live at `/`: keep its deep links (`/#/Channel/...`) working.
if (location.hash.startsWith("#/")) location.replace(`/api${location.hash}`);

// Health

async function checkHealth() {
  const badge = $("health");
  const started = performance.now();
  let state = "down";
  let label = "Unavailable";
  let node = "—";
  let elapsed = "—";
  try {
    const response = await fetch("/healthz", { cache: "no-store" });
    elapsed = `${Math.round(performance.now() - started)} ms`;
    node = response.headers.get("X-App-Node") || "—";
    if (response.ok) {
      state = "ok";
      label = "Operational";
    } else {
      label = `Degraded (${response.status})`;
    }
  } catch {
    // Network error: keep "Unavailable".
  }
  badge.dataset.state = state;
  badge.querySelector(".status-text").textContent = label;
  $("health-status").textContent = label;
  $("health-time").textContent = elapsed;
  $("health-node").textContent = node;
}

checkHealth();
setInterval(() => {
  if (document.visibilityState === "visible") checkHealth();
}, 60_000);

// Try it

const form = $("try-form");
const input = $("channel");
const field = input.closest(".field");
const help = $("channel-help");
const helpText = help.innerHTML;
const submit = form.querySelector("button[type=submit]");
const requestLine = $("request-line");

if (features.pow) document.querySelector(".pow-note").hidden = false;

/** `durov` from `durov`, `@durov`, `t.me/durov`, `https://t.me/s/durov/123`. */
function channelName(value) {
  const text = value.trim().replace(/^@/, "");
  const link = text.match(/^(?:https?:\/\/)?(?:www\.)?(?:t|telegram)\.me\/(?:s\/)?([^/?#]+)/i);
  return link ? link[1] : text;
}

function targetFor(channel) {
  return `/v1/body/${encodeURIComponent(channel)}`;
}

function showRequest() {
  const channel = channelName(input.value) || "{channel}";
  requestLine.textContent = `GET ${targetFor(channel).replace("%7Bchannel%7D", "{channel}")}`;
}

function setError(message) {
  if (message) {
    field.dataset.invalid = "";
    help.dataset.error = "";
    help.textContent = message;
    input.setAttribute("aria-invalid", "true");
  } else {
    delete field.dataset.invalid;
    delete help.dataset.error;
    help.innerHTML = helpText;
    input.removeAttribute("aria-invalid");
  }
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

/** Pretty-printed JSON with spans for keys, strings, numbers and literals. */
function highlight(value) {
  const json = JSON.stringify(value, null, 2);
  const token = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  const span = (kind, text) => `<span class="j-${kind}">${escapeHtml(text)}</span>`;
  let html = "";
  let last = 0;
  for (const match of json.matchAll(token)) {
    const [whole, string, colon, literal, number] = match;
    html += escapeHtml(json.slice(last, match.index));
    if (string) html += colon ? span("key", string) + colon : span("str", string);
    else if (literal) html += span("lit", literal);
    else html += span("num", number);
    last = match.index + whole.length;
  }
  return html + escapeHtml(json.slice(last));
}

function formatBytes(size) {
  return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`;
}

function showSummary(body) {
  const channel = body && body.channel;
  if (!channel) {
    $("summary").hidden = true;
    return;
  }
  const avatar = $("summary-avatar");
  avatar.hidden = !channel.avatar;
  if (channel.avatar) avatar.src = channel.avatar;
  $("summary-title").textContent = (channel.title && channel.title.string) || channel.username;
  const posts = (body.content && body.content.posts) || [];
  const subscribers = channel.counters && channel.counters.subscribers;
  $("summary-sub").textContent = [
    `@${channel.username}`,
    subscribers ? `${subscribers} subscribers` : null,
    `${posts.length} posts in this page`,
  ]
    .filter(Boolean)
    .join(" · ");
  const latest = posts[posts.length - 1];
  const text = latest && latest.content && latest.content.text && latest.content.text.string;
  $("summary-post").textContent = text ? `Latest: ${text}` : "";
  $("summary").hidden = false;
}

function showResult(status, statusText, elapsed, size, body, raw) {
  const meta = $("result-meta");
  meta.replaceChildren();
  const code = document.createElement("span");
  code.className = "code";
  code.textContent = `${status} ${statusText}`.trim();
  code.toggleAttribute(status >= 200 && status < 300 ? "data-ok" : "data-error", true);
  const time = document.createElement("span");
  time.textContent = `${elapsed} ms`;
  const length = document.createElement("span");
  length.textContent = formatBytes(size);
  meta.append(code, time, length);
  showSummary(status === 200 ? body : null);
  $("json").innerHTML = body === undefined ? escapeHtml(raw) : highlight(body);
  $("result").hidden = false;
}

async function run(channel) {
  const target = targetFor(channel);
  submit.disabled = true;
  submit.textContent = features.pow ? "Solving…" : "Fetching…";
  const started = performance.now();
  try {
    let response;
    if (features.pow) {
      const { powFetch } = await import(`./pow.js?v=${assets}`);
      response = await powFetch(target);
    } else {
      response = await fetch(target);
    }
    const raw = await response.text();
    let body;
    try {
      body = JSON.parse(raw);
    } catch {
      body = undefined;
    }
    const elapsed = Math.round(performance.now() - started);
    showResult(response.status, response.statusText, elapsed, new Blob([raw]).size, body, raw);
  } catch (error) {
    showResult(0, "Network error", Math.round(performance.now() - started), 0, undefined, String(error));
  } finally {
    submit.disabled = false;
    submit.textContent = "Fetch";
  }
}

input.addEventListener("input", () => {
  setError(null);
  showRequest();
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const channel = channelName(input.value);
  if (!CHANNEL.test(channel)) {
    setError("Not a channel username: 5–32 letters, digits or underscores, starting with a letter.");
    input.focus();
    return;
  }
  setError(null);
  input.value = channel;
  showRequest();
  const url = new URL(location.href);
  url.searchParams.set("channel", channel);
  history.replaceState(null, "", url);
  run(channel);
});

$("copy-curl").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const channel = channelName(input.value) || "durov";
  const command = `curl ${location.origin}${targetFor(channel)}`;
  try {
    await navigator.clipboard.writeText(command);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Copy failed";
  }
  setTimeout(() => {
    button.textContent = "Copy as curl";
  }, 1500);
});

const requested = new URLSearchParams(location.search).get("channel");
if (requested) {
  input.value = requested;
  form.requestSubmit();
}
showRequest();
