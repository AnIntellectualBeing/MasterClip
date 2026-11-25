"use strict";
/* MasterClip home: 1 Video type + get video -> 2 Design (designer.js) -> 3 Find hype moments. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  step: 1,
  source: null, // {kind:'url'|'file', url, file, info, start, end, liveMinutes}
  templates: [],
  fonts: [],
  recipe: { length: 30, count: 3, language: "auto" },
  job: null,
  poll: null,
};

/* Top bar "More ▾" menu: the less-used things (new window, save folder, phone remote, help). */
(function moreMenu() {
  const btn = $("#moreBtn"), pop = $("#moreMenu");
  const show = (on) => { pop.hidden = !on; btn.setAttribute("aria-expanded", String(on)); };
  btn.addEventListener("click", (e) => { e.stopPropagation(); show(pop.hidden); });
  pop.addEventListener("click", (e) => { if (e.target.closest(".more-item")) show(false); });
  document.addEventListener("click", (e) => { if (!pop.hidden && !e.target.closest(".more-menu")) show(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { show(false); btn.focus(); } });
})();
/* One-time "New in 1.6" note: shown until the buyer presses Got it (remembered on this PC). */
(function whatsNew() {
  const KEY = "mc-seen-new-1.6";
  let seen = false;
  try { seen = localStorage.getItem(KEY) === "1"; } catch { /* private mode: show it */ }
  const box = $("#whatsNew");
  if (!box || seen) return;
  box.hidden = false;
  $("#whatsNewX").addEventListener("click", () => {
    box.hidden = true;
    try { localStorage.setItem(KEY, "1"); } catch { /* ignore */ }
  });
})();
$("#newWindow").addEventListener("click", async () => {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.new_window) {
      await window.pywebview.api.new_window();
    } else {
      const opened = window.open("/?new=1", "_blank");
      if (!opened) toast("Allow pop-ups for MasterClip to open another window.");
    }
  } catch (err) { notice("Couldn't open a new window. " + err.message); }
});
function continuousSource() {
  return state.source && state.source.kind === "url" && state.source.info.is_live && !state.source.liveMinutes;
}
/* 4K is offered only when the link really has it (the site lists its sizes when we open it). */
function paintQuality(info) {
  const select = $("#exportQuality");
  const k4 = select && select.querySelector('option[value="2160"]');
  if (!k4) return;
  const h = info && info.max_height;
  if (!h) { k4.disabled = false; k4.textContent = "4K · 2160p · best available source"; return; }
  if (h >= 2160) { k4.disabled = false; k4.textContent = "4K · 2160p · this video has 4K"; return; }
  k4.disabled = true;
  k4.textContent = `4K · not available (this video goes up to ${h}p)`;
  if (select.value === "2160") select.value = "1080";
}
function paintContinuousChoice() {
  $("#clipCountSetting").hidden = !!continuousSource();
  $("#continuousNote").hidden = !continuousSource();
  $("#qualitySetting").hidden = !!(state.source && state.source.info && state.source.info.is_live);
}

/* ---------- helpers ---------- */
async function api(path, options = {}) {
  const init = { ...options };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(init.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (netError) {
    // The engine behind the window stopped answering: say so instead of freezing.
    if (window.ClipCrash) window.ClipCrash.engineLost(path);
    const error = new Error("MasterClip's engine stopped answering. See the help on screen.");
    error.status = 0;
    throw error;
  }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    let msg = data && data.detail;
    // 422 = a form value the server refused. Never show pydantic's wording to a buyer.
    if (Array.isArray(msg)) msg = friendlyFieldError(msg);
    if (res.status === 402 && window.ClipLicence) window.ClipLicence.open(msg);
    const error = new Error(msg || "Something went wrong. Please try again.");
    error.status = res.status;
    throw error;
  }
  return data;
}
function friendlyFieldError(list) {
  const first = list[0] || {};
  const field = String((first.loc || []).slice(-1)[0] || "");
  const names = { key: "licence key", source: "video link", topic: "search words", name: "name", url: "link" };
  const what = names[field] || "one of the settings";
  if (/at least/.test(first.msg || "")) return `That ${what} is too short. Check it and try again.`;
  if (/at most/.test(first.msg || "")) return `That ${what} is too long. Check it and try again.`;
  if (/required|missing/i.test(first.msg || "")) return `Please fill in the ${what}.`;
  return `Please check the ${what} and try again.`;
}
function clock(seconds) {
  seconds = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function count(n) {
  if (n == null) return "";
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "K";
  return String(n);
}
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) if (child != null) node.append(child.nodeType ? child : document.createTextNode(child));
  return node;
}
function notice(message) {
  $("#noticeText").textContent = message;
  $("#notice").hidden = !message;
  if (message) $("#notice").scrollIntoView({ behavior: "smooth", block: "center" });
}
let toastTimer;
function toast(message) {
  const t = $("#toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}
function segOn(seg, button) {
  $$("button", seg).forEach((b) => b.classList.toggle("on", b === button));
}

/* ---------- steps ---------- */
function go(step) {
  state.step = step;
  [1, 2, 3].forEach((n) => { $(`#step${n}`).hidden = n !== step; });
  $$(".stepper li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle("active", n === step);
    li.classList.toggle("done", n < step);
  });
  notice("");
  if (step === 2) window.ClipDesigner.enter(state.source);
  paintContinuousChoice();
  if (step === 3) renderSummary();
  if (step !== 1) $("#welcomeBack").hidden = true;
  window.scrollTo({ top: 0, behavior: "smooth" });
}
$$("[data-go]").forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.go))));
// The step bar is clickable: hop between Get video, Design and Find hype moments any time.
$$(".stepper li").forEach((li) => {
  li.style.cursor = "pointer";
  li.setAttribute("role", "button");
  li.tabIndex = 0;
  const hop = () => {
    const n = Number(li.dataset.step);
    if (n === state.step) return;
    if (n === 3 && !state.source && !state.job) { toast("Pick a video first (step 1)."); return; }
    go(n);
  };
  li.addEventListener("click", hop);
  li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); hop(); } });
});
$("#noticeClose").addEventListener("click", () => notice(""));

/* ---------- step 1: what are you making? ---------- */
$$("[data-kind-pick]").forEach((b) => b.addEventListener("click", () => window.ClipDesigner.setKind(b.dataset.kindPick)));

/* ---------- step 1: tabs ---------- */
$$(".tab[data-tab]").forEach((tab) => tab.addEventListener("click", () => {
  $$(".tab[data-tab]").forEach((t) => t.classList.toggle("active", t === tab));
  $("#tabFind").hidden = tab.dataset.tab !== "find";
  $("#tabLink").hidden = tab.dataset.tab !== "link";
  $("#tabFile").hidden = tab.dataset.tab !== "file";
  notice("");
}));

/* search */
let searchToken = 0;
async function runSearch(topic) {
  topic = topic.trim();
  if (topic.length < 2) { notice("Type a game or streamer name first."); return; }
  notice("");
  const token = ++searchToken;
  const box = $("#searchState");
  box.hidden = false;
  box.className = "soft-state";
  box.textContent = "Searching YouTube and Twitch for live streams and recent VODs…";
  $("#results").replaceChildren();
  $("#siteFilter").hidden = true;
  try {
    let job = await api("/api/discover", { method: "POST", body: { topic, max_results: 24 } });
    while (job.status === "searching") {
      await new Promise((r) => setTimeout(r, 600));
      if (token !== searchToken) return;
      job = await api(`/api/discover/${job.id}`);
    }
    if (token !== searchToken) return;
    if (job.status !== "complete") throw new Error(job.error || "Search didn't work. Try again.");
    if (!job.results.length) {
      box.textContent = (job.warnings && job.warnings[0]) || "Nothing found. Try a shorter search.";
      return;
    }
    box.hidden = true;
    searchResults = job.results;
    $("#searchNote").textContent = (job.warnings && job.warnings[0]) || "";
    paintResults();
  } catch (err) {
    box.className = "soft-state bad";
    box.textContent = err.message;
  }
}
let searchResults = [];
let searchSite = "all";
function paintResults() {
  const counts = { all: searchResults.length };
  searchResults.forEach((r) => { counts[r.site] = (counts[r.site] || 0) + 1; });
  if (searchSite !== "all" && !counts[searchSite]) searchSite = "all";
  $$("#siteFilter [data-site]").forEach((b) => {
    const site = b.dataset.site;
    b.textContent = `${site === "all" ? "All" : site} ${counts[site] || 0}`;
    b.disabled = !counts[site];
    b.classList.toggle("on", site === searchSite);
  });
  $("#siteFilter").hidden = !searchResults.length;
  const shown = searchSite === "all" ? searchResults : searchResults.filter((r) => r.site === searchSite);
  $("#results").replaceChildren(...shown.map(resultCard));
}
$$("#siteFilter [data-site]").forEach((b) => b.addEventListener("click", () => { searchSite = b.dataset.site; paintResults(); }));
function resultCard(item) {
  const tag = item.live
    ? el("span", { class: "badge live tag", text: `LIVE${item.views != null ? " · " + count(item.views) + " watching" : ""}` })
    : el("span", { class: "badge vod tag", text: "PAST STREAM" });
  const site = item.site || "YouTube";
  const meta = item.live ? item.channel : `${item.channel}${item.views != null ? " · " + count(item.views) + " views" : ""}`;
  return el("button", { class: "result", type: "button", onclick: () => pickUrl(item.url, item) },
    el("div", { class: "thumb" },
      el("img", { src: item.thumbnail || "", alt: "", loading: "lazy" }),
      tag,
      el("span", { class: `site-badge ${site.toLowerCase()}`, text: site }),
      item.duration ? el("span", { class: "len", text: clock(item.duration) }) : null),
    el("div", { class: "result-text" }, el("strong", { text: item.title }), el("span", { text: meta })));
}
$("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); runSearch($("#searchInput").value); });
$$("#quickChips .chip").forEach((chip) => chip.addEventListener("click", () => {
  $("#searchInput").value = chip.textContent;
  runSearch(chip.textContent);
}));

/* link */
$("#linkForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = $("#linkInput").value.trim();
  if (!/^https?:\/\//i.test(url)) { notice("Paste a full link that starts with https://"); return; }
  pickUrl(url);
});

async function pickUrl(url, preview) {
  notice("");
  showPicked({ thumb: preview && preview.thumbnail, badge: "Opening…", title: (preview && preview.title) || url, meta: "Reading the video, one moment…" });
  $("#rangeBox").hidden = true;
  $("#liveBox").hidden = true;
  $("#toStep2").disabled = true;
  try {
    const info = await api("/api/inspect", { method: "POST", body: { url } });
    state.source = { kind: "url", url: info.url, info, liveMinutes: 0 };
    paintQuality(info);
    setTimeout(() => setPostMode(state.autoPost ? "auto" : "manual"), 0);
    forgetFinishedJob();
    const badge = info.is_live ? "LIVE" : info.duration ? `PAST STREAM · ${clock(info.duration)}` : "VIDEO";
    const seen = info.views != null ? info.views : preview && preview.views;
    const views = seen != null ? ` · ${count(seen)} ${info.is_live ? "watching" : "views"}` : "";
    showPicked({ thumb: info.thumbnail, badge, live: info.is_live, title: info.title, meta: `${info.channel || info.site}${views}` });
    if (info.is_live) {
      $("#liveBox").hidden = false;
    } else if (info.duration > 0) {
      setupRange(info);
    }
    $("#toStep2").disabled = false;
  } catch (err) {
    state.source = null;
    $("#picked").hidden = true;
    notice(err.message);
  }
}
function showPicked({ thumb, badge, live, title, meta }) {
  $("#picked").hidden = false;
  $("#pickedThumb").src = thumb || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E";
  const b = $("#pickedBadge");
  b.textContent = badge;
  b.className = "badge" + (live ? " live" : "");
  $("#pickedTitle").textContent = title;
  $("#pickedMeta").textContent = meta || "";
  $("#picked").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
$("#pickedClear").addEventListener("click", () => {
  state.source = null;
  $("#picked").hidden = true;
});

/* VOD range */
const SCALE = 1000;
function setupRange(info) {
  const box = $("#rangeBox");
  box.hidden = false;
  const heat = info.heat || [];
  const svg = $("#heat");
  if (heat.length) {
    const max = Math.max(...heat.map((h) => h.value), 0.01);
    let d = `M0,60`;
    heat.forEach((h) => {
      const x = (h.start / info.duration) * SCALE, x2 = (h.end / info.duration) * SCALE;
      const y = 58 - (h.value / max) * 52;
      d += ` L${x.toFixed(1)},${y.toFixed(1)} L${x2.toFixed(1)},${y.toFixed(1)}`;
    });
    d += ` L${SCALE},60 Z`;
    svg.innerHTML = `<path d="${d}" fill="#F7B49B" opacity=".75"/>`;
  } else {
    svg.innerHTML = "";
  }
  $$('[data-range="hot"]')[0].hidden = !heat.length;
  const s = info.suggested || { start: 0, end: Math.min(info.duration, 600) };
  setRange(s.start, s.end);
  // Long VODs: scan the whole stream by default (only the hottest parts get downloaded).
  $$('[data-range="whole"]')[0].hidden = info.duration < 1800;
  setWhole(info.duration >= 1800);
  $("#rangeHint").textContent = heat.length
    ? "The orange hills show what viewers re-watched most. We download only the part you pick."
    : "We download only the part you pick, so it's quick.";
}
function setWhole(on) {
  state.source.whole = on;
  $$("[data-range]").forEach((b) => b.classList.toggle("on", on && b.dataset.range === "whole"));
  $("#rangeBox").classList.toggle("whole", on);
  if (on) {
    $("#rangeLabel").textContent = "Whole stream";
    $("#rangeHint").textContent = "⚡ We read the whole stream (most-replayed + chat) without downloading it, then download only the hottest few minutes. Fast even for a 9-hour stream.";
  }
}
function setRange(start, end) {
  const d = state.source.info.duration;
  start = Math.max(0, Math.min(start, d - 10));
  end = Math.max(start + 10, Math.min(end, d, start + 4 * 3600));
  state.source.start = start;
  state.source.end = end;
  $("#rangeStart").value = Math.round((start / d) * SCALE);
  $("#rangeEnd").value = Math.round((end / d) * SCALE);
  paintRange();
}
function paintRange() {
  const d = state.source.info.duration, s = state.source.start, e = state.source.end;
  $("#rangeFill").style.left = (s / d) * 100 + "%";
  $("#rangeFill").style.width = ((e - s) / d) * 100 + "%";
  $("#rangeLabel").textContent = `${clock(s)} → ${clock(e)} · ${Math.max(1, Math.round((e - s) / 60))} min`;
}
["#rangeStart", "#rangeEnd"].forEach((id) => $(id).addEventListener("input", () => {
  const d = state.source.info.duration;
  let s = (Number($("#rangeStart").value) / SCALE) * d;
  let e = (Number($("#rangeEnd").value) / SCALE) * d;
  if (e - s < 10) {
    if (id === "#rangeStart") s = e - 10; else e = s + 10;
  }
  if (e - s > 4 * 3600) {
    if (id === "#rangeStart") e = s + 4 * 3600; else s = e - 4 * 3600;
  }
  setWhole(false);
  setRange(s, e);
}));
$$("[data-range]").forEach((b) => b.addEventListener("click", () => {
  const info = state.source.info, d = info.duration, w = Math.min(600, d);
  if (b.dataset.range === "whole") { setWhole(true); return; }
  setWhole(false);
  if (b.dataset.range === "hot") setRange(info.suggested.start, info.suggested.end);
  if (b.dataset.range === "first") setRange(Math.min(d * 0.03, d - w), Math.min(d * 0.03, d - w) + w);
  if (b.dataset.range === "last") setRange(d - w, d);
}));
$$("#liveMinutes button").forEach((b) => b.addEventListener("click", () => {
  segOn($("#liveMinutes"), b);
  state.source.liveMinutes = Number(b.dataset.min);
  $("#liveHint").textContent = state.source.liveMinutes
    ? "We record from when you press Find hype moments, then find the best moments in it. Earlier moments aren't recorded."
    : "Start once: we check each new minute and make clips of good moments until you press Stop or the stream ends. Clip now is optional. Keep MasterClip and your PC running.";
  paintContinuousChoice();
}));

/* file */
function pickFile(file) {
  if (!file) return;
  if (!/\.(mp4|mkv|mov|webm|avi|m4v|ts)$/i.test(file.name)) { notice("That file isn't a video we can open. Use MP4, MKV, MOV or WEBM."); return; }
  state.source = { kind: "file", file };
  paintQuality(null);
  forgetFinishedJob();
  $("#fileLabel").textContent = file.name;
  $("#rangeBox").hidden = true;
  $("#liveBox").hidden = true;
  $("#toStep2").disabled = false;
  showPicked({ badge: "YOUR FILE", title: file.name, meta: `${(file.size / 1048576).toFixed(0)} MB · stays on this computer` });
}
$("#fileInput").addEventListener("change", (e) => pickFile(e.target.files[0]));
const drop = $("#dropZone");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

$("#toStep2").addEventListener("click", () => {
  if (!state.source) { notice("Pick a video first."); return; }
  go(2);
});

/* ---------- step 2: design (templates, previews, captions live in designer.js) ---------- */
function setLength(len) {
  const btn = $(`#lengthSeg [data-len="${len}"]`);
  if (!btn) return;
  segOn($("#lengthSeg"), btn);
  state.recipe.length = Number(len);
  $("#customLength").hidden = true;
}
$$("#lengthSeg button").forEach((b) => b.addEventListener("click", () => {
  segOn($("#lengthSeg"), b);
  const custom = b.dataset.len === "custom";
  $("#customLength").hidden = !custom;
  state.recipe.length = custom ? Number($("#customLength").value) || 20 : Number(b.dataset.len);
}));
$("#customLength").addEventListener("input", (e) => {
  state.recipe.length = Math.max(5, Math.min(180, Number(e.target.value) || 20));
});
function setCount(n) {
  state.recipe.count = Math.max(1, Math.min(20, n));
  $("#countValue").textContent = state.recipe.count;
}
$("#countMinus").addEventListener("click", () => setCount(state.recipe.count - 1));
$("#countPlus").addEventListener("click", () => setCount(state.recipe.count + 1));
// Remember the spoken language and caption language between videos (same streamer, same language).
try {
  const lang = localStorage.getItem("clipstudio.lang"), capIn = localStorage.getItem("clipstudio.capin");
  if (lang && $(`#language option[value="${lang}"]`)) $("#language").value = lang;
  if (capIn && $(`#captionsIn option[value="${capIn}"]`)) $("#captionsIn").value = capIn;
} catch { /* private mode */ }
$("#language").addEventListener("change", (e) => {
  state.recipe.language = e.target.value;
  try { localStorage.setItem("clipstudio.lang", e.target.value); } catch { /* private mode */ }
});
$("#captionsIn").addEventListener("change", (e) => {
  try { localStorage.setItem("clipstudio.capin", e.target.value); } catch { /* private mode */ }
});
$("#toStep3").addEventListener("click", () => go(3));

/* ---------- step 3: make ---------- */
const ACTIVE = ["queued", "importing", "transcribing", "analyzing", "rendering", "live"];
function jobActive() { return !!(state.job && ACTIVE.includes(state.job.status)); }
// A new video was picked: the clips of an earlier, finished job must not block "Make my clips".
function forgetFinishedJob() {
  if (jobActive()) return;
  state.job = null;
  state.painted = null;
  $("#done").hidden = true;
  $("#failed").hidden = true;
  $("#working").hidden = true;
}
function renderSummary() {
  const src = state.source;
  const busy = jobActive();
  // Back from Design with clips already made: offer the new look on the same clips.
  const finished = !busy && state.job && state.job.status === "complete" && (state.job.clips || []).length && !state.job.live;
  $("#restyleBox").hidden = !finished;
  $("#makeClips").textContent = finished ? "🔥 Find new hype moments" : "🔥 Find hype moments";
  // Clips are still being made (or the window is showing an earlier job): say so, never a dead end.
  if (busy || !src) {
    const from = state.job && state.job.title ? ` from “${state.job.title}”` : "";
    $("#summary").textContent = busy
      ? `Clips are still being made${from}. They show up below. Press ${state.job.status === "live" ? "Stop" : "Cancel"} to start something new.`
      : state.job ? `Your clips${from}.` : "Pick a video first.";
    $("#makeBox").hidden = true;
    return;
  }
  let where = "";
  if (src && src.kind === "url") {
    where = src.info.is_live ? `record ${src.liveMinutes} min of the live stream` : src.whole ? "scan the whole stream and download only its hottest parts" : src.info.duration ? `use ${clock(src.start)}–${clock(src.end)} of the stream` : "use the video";
  } else if (src) where = `use ${src.file.name}`;
  const n = state.recipe.count;
  const plan = window.ClipDesigner.summary(state.recipe.length);
  const best = n === 1 ? "the most hype moment" : `the ${n} most hype moments`;
  const firstRun = state.speech && state.speech.state === "download" ? " The first captioned video downloads the speech model once (about 150 MB)." : "";
  $("#summary").textContent = (src && src.kind === "url" && src.info.is_live && !src.liveMinutes
    ? `We'll keep checking each new minute for good moments and ${plan}, until you press Stop or the stream ends. No clip target and no need to press Clip now.${state.autoPost ? " Finished clips post automatically at your chosen speed." : " Each clip is saved as soon as it's ready."}`
    : `We'll ${where}, find ${best} and ${plan}.`) + firstRun;
  $("#makeBox").hidden = false;
}
function recipePayload() {
  return { ...window.ClipDesigner.payload(), length: state.recipe.length, count: state.recipe.count, auto_post: state.autoPost === true,
    posting_slot: window.ClipPosting ? window.ClipPosting.sessionSlot() : 0,
    campaign_id: state.campaign ? state.campaign.id : null,
    mix: state.mix || "balanced",
    resolution: state.source && state.source.info && state.source.info.is_live ? 1080 : Number($("#exportQuality").value) };
}
// Moment mix: which moments the picker prefers.
const MIX_HINTS = {
  balanced: "A bit of everything: big events, loud reactions and funny talk.",
  action: "Chases, crashes, loud reactions and big things on screen come first.",
  dialogue: "Funny back-and-forth and jokes come first.",
};
function setMix(mix) {
  if (!MIX_HINTS[mix]) mix = "balanced";
  state.mix = mix;
  $$("#mixSeg button").forEach((b) => b.classList.toggle("on", b.dataset.v === mix));
  $("#mixHint").textContent = MIX_HINTS[mix];
  try { localStorage.setItem("clipstudio.mix", mix); } catch { /* private mode */ }
}
$$("#mixSeg button").forEach((b) => b.addEventListener("click", () => setMix(b.dataset.v)));
try { setMix(localStorage.getItem("clipstudio.mix") || "balanced"); } catch { setMix("balanced"); }
// First screen: auto-post every clip, or pick by hand.
function setPostMode(mode) {
  state.autoPost = mode === "auto";
  $$("[data-postmode]").forEach((b) => b.classList.toggle("on", b.dataset.postmode === mode));
  try { sessionStorage.setItem("clipstudio.postmode", mode); } catch { /* private mode */ }
  // Auto-post = non-stop clipping: the 2/5/10 min choice doesn't apply, so it hides (and comes back when off).
  const minutes = $("#liveMinutes");
  if (minutes) {
    minutes.hidden = state.autoPost;
    if (state.autoPost && state.source && state.source.info && state.source.info.is_live) {
      state.source.liveMinutes = 0;
      segOn(minutes, minutes.querySelector('[data-min="0"]'));
      $("#liveHint").textContent = "🤖 Auto-post is on: we keep clipping non-stop and post each clip, until you press Stop or the stream ends.";
    }
  }
}
$$("[data-postmode]").forEach((b) => b.addEventListener("click", async () => {
  setPostMode(b.dataset.postmode);
  if (b.dataset.postmode === "auto") {
    if (window.ClipPosting) window.ClipPosting.openAutoSetup();
  }
}));
try { setPostMode(sessionStorage.getItem("clipstudio.postmode") || "manual"); } catch { setPostMode("manual"); }
async function trialUsedUp() {
  // Ask first, so a big file isn't copied just to hear "trial over" (T4: silent freeze on 402).
  try {
    const lic = await api("/api/licence");
    return !lic.licensed && lic.trial_left <= 0;
  } catch { return false; }
}
function showPayBox(message) {
  $("#working").hidden = true;
  $("#makeBox").hidden = false;
  $("#payBox").hidden = false;
  if (window.ClipLicence) window.ClipLicence.open(message || "Your free videos are used up. Enter your licence key to keep making clips.");
}
$("#payOpen").addEventListener("click", () => window.ClipLicence && window.ClipLicence.open());
$("#makeClips").addEventListener("click", async () => {
  notice("");
  const src = state.source;
  if (!src) { go(1); notice("Pick a video first."); return; }
  if (state.autoPost && window.ClipPosting && !await window.ClipPosting.ensureReady()) return;
  $("#payBox").hidden = true;
  if (await trialUsedUp()) { showPayBox(); return; }
  $("#failed").hidden = true;
  $("#done").hidden = true;
  $("#makeBox").hidden = true;
  state.phased = true;
  state.started = Date.now();
  showWorking("Starting…", 0);
  try {
    let job;
    if (src.kind === "url") {
      const body = {
        source_type: "url", source: src.url, captions: window.ClipDesigner.captionsOn(), language: $("#language").value,
        source_height: Number($("#exportQuality").value),
        captions_in: $("#captionsIn").value,
        from_discovery: true, recipe: recipePayload(), format: "vertical",
      };
      if (src.info.is_live && !src.liveMinutes) body.live_rolling = true;
      else if (src.info.is_live) body.capture_seconds = src.liveMinutes * 60;
      else if (src.whole) {
        body.scan_whole = true;
        // backup range if the site blocks the hot parts
        if (src.info.duration > 0 && src.end > src.start) { body.section_start = src.start; body.section_end = src.end; }
      } else if (src.info.duration > 0) { body.section_start = src.start; body.section_end = src.end; }
      job = await api("/api/projects", { method: "POST", body });
    } else {
      const form = new FormData();
      form.append("file", src.file);
      form.append("format", "vertical");
      form.append("captions", String(window.ClipDesigner.captionsOn()));
      form.append("language", $("#language").value);
      form.append("captions_in", $("#captionsIn").value);
      form.append("recipe", JSON.stringify(recipePayload()));
      showWorking("Copying your video into MasterClip…", 0.01);
      job = await api("/api/projects/upload", { method: "POST", body: form });
    }
    watchJob(job.id);
  } catch (err) {
    $("#working").hidden = true;
    $("#makeBox").hidden = false;
    if (err.status === 402) showPayBox(err.message);
    else notice(err.message);
  }
});
// Exciting lines while we work; the real step shows small underneath.
const HYPE_LINES = [
  ["🔗 Grabbing the stream…", "📡 Locking on to the stream…", "⚡ Warming up the clip engine…"],
  ["🎧 Listening to every word…", "🗣️ Catching what they say…", "😂 Hearing the funny bits…"],
  ["🔥 Finding hype moments…", "🎯 Hunting the perfect moments…", "💥 Spotting the loudest reactions…", "😱 Looking for the crazy parts…", "🏆 Picking the best of the best…"],
  ["✂️ Cutting clean clips…", "🎬 Adding captions and titles…", "✨ Making it look viral…", "🚀 Almost there, polishing your clips…"],
];
function hypeLine(step) {
  const lines = HYPE_LINES[Math.min(step, 3)];
  return lines[Math.floor(Date.now() / 3500) % lines.length];
}
// The bar runs ahead (fast to 85 %, then a slow creep) so it never looks stuck; it jumps to the
// real progress whenever the real one is further along.
function fakeProgress() {
  const t = state.started ? (Date.now() - state.started) / 1000 : 0;
  let f = 0.85 * (1 - Math.exp(-t / 14));
  if (f > 0.84) f = Math.min(0.97, 0.85 + Math.max(0, t - 40) / 900);
  return f;
}
function paintBar() {
  if ($("#working").hidden) { clearInterval(state.barTimer); state.barTimer = null; return; }
  state.shown = Math.max(state.shown || 0, state.real || 0, state.phased ? fakeProgress() : 0);
  const pct = Math.min(99, Math.floor(state.shown * 100));
  $("#workBar").style.width = pct + "%";
  $("#workPercent").textContent = pct + "%";
  if (state.phased) $("#workMessage").textContent = hypeLine(state.step || 0);
}
function showWorking(message, p, job) {
  $("#working").hidden = false;
  $("#workMessage").textContent = message;
  $("#workDetail").textContent = state.phased && message ? message : "";
  // One bar for the whole job: getting + listening + finding = 0-60 %, making the clips = 60-100 %
  // (the server's two phases each count 0-100 %, which looked stuck at 1 % when clips started).
  let step = 0, overall = p || 0;
  if (job) {
    const s = job.status, raw = job.progress || 0;
    step = s === "rendering" ? 3 : s === "transcribing" ? 1 : s === "analyzing" ? (raw < 0.3 ? 0 : 2) : 0;
    if (state.phased) overall = s === "rendering" ? 0.6 + 0.4 * raw : 0.6 * raw;
    else overall = raw;
    paintWorkClips(job);
  }
  if (!state.phased) state.shown = 0;
  state.real = overall;
  state.step = step;
  paintBar();
  if (!state.barTimer) state.barTimer = setInterval(paintBar, 250);
  $("#workSteps").hidden = !state.phased;
  $$("#workSteps li").forEach((li) => {
    const n = Number(li.dataset.s);
    li.classList.toggle("done", n < step);
    li.classList.toggle("now", n === step);
  });
  const secs = state.started ? Math.round((Date.now() - state.started) / 1000) : 0;
  const left = job ? timeLeft(job) : null;
  $("#workTime").textContent = secs > 3
    ? `${clock(secs)} so far · ` + (left == null ? "working out the time left…" : left < 45 ? "almost done" : `about ${left < 90 ? "1 min" : Math.round(left / 60) + " min"} left`)
    : "";
}
// Time left from real speed: how fast the current step moves, plus the clips still to make
// (each measured as they finish; about 40 s until the first one is done).
function timeLeft(job) {
  const now = Date.now(), raw = job.progress || 0, s = job.status;
  const t = state.eta || (state.eta = {});
  if (t.job !== job.id || t.status !== s) Object.assign(t, { job: job.id, status: s, since: now, raw0: raw, clipAt: now, clips: (job.clips || []).length });
  const r = job.recipe || {};
  const per = (r.formats || []).length > 1 || r.kind === "both" ? 2 : 1;
  const want = Math.max(1, (r.count || 3) * per);
  const done = (job.clips || []).length;
  if (done > t.clips) { t.perClip = (now - t.clipAt) / 1000 / (done - t.clips); t.clipAt = now; t.clips = done; }
  const perClip = t.perClip || 40;
  const renderLeft = Math.max(0, want - done) * perClip;
  if (s === "rendering") return renderLeft;
  const moved = raw - t.raw0, spent = (now - t.since) / 1000;
  if (s !== "transcribing" || spent < 15 || moved < 0.03) return t.last != null && t.lastStatus === s ? t.last : null;
  const est = Math.round(spent * (1 - raw) / moved + renderLeft + 20);
  t.last = t.last == null ? est : Math.round(0.7 * t.last + 0.3 * est);
  t.lastStatus = s;
  return t.last;
}
function paintWorkClips(job) {
  // Clips show up here as soon as each one is made, before the rest are done.
  const clips = job.status === "rendering" ? job.clips || [] : [];
  const box = $("#workClips");
  box.hidden = !clips.length;
  if (box.dataset.n === String(clips.length)) return;
  box.dataset.n = String(clips.length);
  box.replaceChildren(el("strong", { text: `Ready so far: ${clips.length}` }),
    el("div", { class: "work-clip-row" }, ...clips.map((c) => el("video", { src: c.url, muted: true, preload: "metadata", playsinline: true, controls: true, title: c.title || "" }))));
}
const FRIENDLY_STAGE = {
  queued: "Waiting to start…",
  importing: null,
  transcribing: null,
  analyzing: null,
  rendering: null,
};
// One video is made at a time. If ours waits, say why and let the buyer stop the older one
// (a friend's app looked broken: every new video sat behind a stuck one).
async function explainQueue(job) {
  const note = $("#queueNote");
  if (Date.now() - new Date(job.created_at).getTime() < 8000) return;
  let ahead;
  try {
    const { jobs } = await api("/api/jobs");
    ahead = (jobs || []).find((j) => j.id !== job.id && ["importing", "transcribing", "analyzing", "rendering"].includes(j.status) && !j.live);
  } catch { return; }
  if (!ahead) { note.hidden = true; return; }
  note.hidden = false;
  note.replaceChildren(
    el("span", { text: `Another video is still being made (“${ahead.title || "video"}”: ${ahead.message || ahead.status}). Yours starts right after it. ` }),
    el("button", { class: "ghost small", type: "button", text: "Stop the other one",
      onclick: async (e) => { e.target.disabled = true; try { await api(`/api/jobs/${ahead.id}/cancel`, { method: "POST" }); e.target.textContent = "Stopping…"; } catch (err) { e.target.textContent = err.message; } } }));
}
function watchJob(id) {
  clearInterval(state.poll);
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${id}`);
      state.job = job;
      try { sessionStorage.setItem("clipstudio.lastJob", id); localStorage.setItem("clipstudio.lastJob", id); } catch { /* private mode */ }
      if (job.status === "live" || (job.live && job.status === "queued")) {
        $("#working").hidden = true;
        showLive(job);
        return;
      }
      $("#liveBar").hidden = true;
      if (["queued", "importing", "transcribing", "analyzing", "rendering"].includes(job.status)) {
        showWorking(FRIENDLY_STAGE[job.status] || job.message, job.progress, job);
        if (job.status === "queued") await explainQueue(job); else $("#queueNote").hidden = true;
        return;
      }
      $("#queueNote").hidden = true;
      clearInterval(state.poll);
      state.phased = false;
      state.shown = 0;
      $("#working").hidden = true;
      if (job.status === "complete" && (job.clips || []).length) {
        showClips(job);
      } else if (job.status === "complete") {
        showFailed(job.message || "We couldn't find good moments. Try a longer or different part of the stream.");
      } else {
        showFailed(job.error || job.message || "Something went wrong.");
      }
    } catch (err) {
      clearInterval(state.poll);
      showFailed(err.message);
    }
  };
  tick();
  state.poll = setInterval(tick, 1000);
}
function showFailed(message) {
  $("#working").hidden = true;
  const box = $("#failed");
  box.hidden = false;
  const fromLink = state.source && state.source.kind === "url";
  const download = fromLink && /youtube|sign-in|signed-in|private|doesn't exist|busy|too many|download|couldn't open|blocked|region|network|internet/i.test(String(message));
  const buttons = [
    // same video, same settings: nothing to set up again
    el("button", { class: "primary small", type: "button", onclick: () => { box.hidden = true; $("#makeBox").hidden = false; $("#makeClips").click(); }, text: "↻ Try again" }),
    el("button", { class: "ghost small", type: "button", onclick: () => { box.hidden = true; $("#makeBox").hidden = false; go(1); }, text: "Pick another video" }),
  ];
  if (fromLink) {
    buttons.push(el("button", { class: "ghost small", type: "button", text: "📁 Use a file from my PC",
      onclick: () => { box.hidden = true; $("#makeBox").hidden = false; go(1); $("#fileInput").click(); } }));
  }
  const tip = download
    ? el("p", { class: "muted small-print", text: "If the site keeps blocking the download, save the video to your PC (for example from your own channel's dashboard) and use it as a file. Captions are then made on this PC." })
    : null;
  box.replaceChildren(el("strong", { text: "That didn't work. " }), message, el("div", { class: "row-gap failed-actions" }, ...buttons), ...(tip ? [tip] : []));
}
$("#changeDesign").addEventListener("click", () => go(2));
$("#restyleBtn").addEventListener("click", async () => {
  if (!state.job) return;
  const formats = window.ClipDesigner.payload().formats;
  try {
    await api(`/api/jobs/${state.job.id}/restyle`, { method: "POST", body: { formats } });
    $("#restyleBox").hidden = true;
    $("#makeBox").hidden = true;
    $("#done").hidden = true;
    state.phased = false;
    state.started = Date.now();
    showWorking("Restyling your clips…", 0.01);
    watchJob(state.job.id);
  } catch (err) { notice(err.message); }
});
$("#cancelJob").addEventListener("click", async () => {
  if (!state.job) return;
  try { await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" }); } catch { /* ignore */ }
});

function showLive(job) {
  const live = job.live || {};
  $("#done").hidden = false;
  $("#makeBox").hidden = true;
  $("#liveBar").hidden = false;
  $("#startOver").hidden = true;
  $("#openTranscript").hidden = $("#openChatHelp").hidden = true; // new clips after Stop
  const stopping = ["stopping", "finishing"].includes(live.state);
  $("#liveTitle").textContent = stopping ? "Finishing up…" : live.state === "starting" ? "Connecting to the stream…" : "Recording live";
  const n = (job.clips || []).length;
  const bits = [`${clock(live.recorded || 0)} recorded`, `${n} clip${n === 1 ? "" : "s"} so far`];
  if (!stopping && live.window && live.state !== "starting") {
    const next = Math.max(0, (live.clipped || 0) + live.window - (live.recorded || 0));
    bits.push(live.state === "clipping" ? "making clips now" : `next automatic scan in ${clock(next)}`);
  }
  if (job.saved_to) bits.push("saved to your PC");
  $("#liveMeta").textContent = " · " + bits.join(" · ");
  $("#liveFolder").hidden = !job.saved_to;
  const hasChat = live.chat && live.chat !== "none" && !stopping;
  $("#liveHype").hidden = !hasChat;
  if (hasChat) {
    const hype = Math.max(0, Math.min(100, live.hype || 0));
    $("#hypeFill").style.width = `${hype}%`;
    $("#hypeFill").className = hype >= 85 ? "peak" : hype >= 55 ? "hot" : "";
    $("#hypeText").textContent = live.chat === "reading"
      ? `Chat: ${live.hype_label || "quiet"} (${live.chat_per_min || 0}/min)`
      : live.chat === "connecting" ? "Connecting to chat…" : live.chat === "no chat" ? "Chat is off on this stream" : "Chat reconnecting…";
  }
  $("#liveClip").hidden = stopping || live.state === "starting";
  $("#liveClip").disabled = !!live.clip_now || live.state === "clipping";
  $("#liveClip").textContent = live.clip_now ? "✂ Clipping…" : "✂ Clip now";
  $("#liveStop").disabled = stopping;
  $("#liveStop").textContent = stopping ? "Stopping…" : "■ Stop";
  $("#doneTitle").textContent = n ? `${n} clip${n === 1 ? "" : "s"} so far` : "Your clips will show up here";
  const key = `${job.id}:${n}:live`;
  if (state.painted !== key) { state.painted = key; paintCards(job); }
}
$("#liveClip").addEventListener("click", async () => {
  if (!state.job) return;
  $("#liveClip").disabled = true;
  $("#liveClip").textContent = "✂ Clipping…";
  try { await api(`/api/jobs/${state.job.id}/live/clip`, { method: "POST" }); } catch (err) { notice(err.message); $("#liveClip").disabled = false; }
});
$("#liveFolder").addEventListener("click", async () => {
  if (!state.job) return;
  try { await api(`/api/jobs/${state.job.id}/open-saved`, { method: "POST" }); } catch (err) { notice(err.message); }
});
$("#liveStop").addEventListener("click", async () => {
  if (!state.job) return;
  $("#liveStop").disabled = true;
  try { await api(`/api/jobs/${state.job.id}/live/stop`, { method: "POST" }); } catch (err) { notice(err.message); }
});

function showClips(job) {
  $("#done").hidden = false;
  $("#restyleBox").hidden = true;
  $("#liveBar").hidden = true;
  $("#startOver").hidden = false;
  $("#openTranscript").hidden = $("#openChatHelp").hidden = false;
  state.painted = `${job.id}:${job.clips.length}:done`;
  if (window.ClipLong) window.ClipLong.paint(job);
  $("#aiNote").textContent = job.ai_note || "";
  $("#aiNote").hidden = !job.ai_note;
  $("#makeBox").hidden = true;
  $("#doneTitle").textContent = `${job.clips.length} clip${job.clips.length === 1 ? "" : "s"} ready 🎉`;
  paintCards(job);
}
function clipShape(job, clip) {
  if (clip.design && clip.design.aspect) return clip.design.aspect;
  return (job.recipe && job.recipe.aspect) || "9:16";
}
function paintCards(job) {
  const live = job.status === "live";
  const mixed = new Set((job.clips || []).map((c) => clipShape(job, c))).size > 1;
  $("#longBox").hidden = live;
  $("#clips").replaceChildren(...(job.clips || []).map((clip) => {
    const shape = clipShape(job, clip);
    const card = el("div", { class: "clip" + (shape === "16:9" ? " landscape" : shape === "1:1" ? " square" : ""), "data-clip-index": clip.index });
    if (!live) card.append(el("button", { class: "clip-x", type: "button", title: "Remove this clip", "aria-label": "Remove this clip", text: "✕",
      onclick: async () => {
        try {
          const j = await api(`/api/jobs/${job.id}/clips/${clip.index}`, { method: "DELETE" });
          state.job = j; showClips(j); toast("Clip removed");
        } catch (err) { notice(err.message); }
      } }));
    const video = el("video", { src: clip.url + `?v=${clip.version || 0}`, controls: true, playsinline: true, preload: "metadata" });
    const title = el("input", { class: "clip-title", value: clip.title || "", maxlength: "140", "aria-label": "Clip title" });
    title.addEventListener("change", () => {
      const design = clip.design;
      saveClip(job.id, clip.index, { start: clip.start, end: clip.end, title: title.value, design });
    });
    card.append(video, el("div", { class: "clip-body" },
      title,
      el("span", { class: "muted", style: "font-size:13px", text: `${mixed ? (shape === "16:9" ? "🖥 YouTube 16:9 · " : "📱 Short 9:16 · ") : ""}${clock(clip.end - clip.start)} long` }),
      live ? "" : stars(job, clip),
      el("div", { class: "clip-posting", "aria-live": "polite" }),
      el("div", { class: "clip-actions" },
        el("button", { class: "ghost small", type: "button", text: "✏️ Edit", disabled: live, title: live ? "You can edit after you press Stop" : "", onclick: () => window.ClipEditor.open(job, clip.index) }),
        el("button", { class: "ghost small manual-post", type: "button", text: "📤 Post", onclick: () => window.ClipPosting.schedule(job.id, [clip.index]) }),
        el("button", { class: "ghost small", type: "button", text: "🖼 Cover", title: "A picture with the title for this clip. Also used as the thumbnail when you post it.",
          onclick: () => window.ClipAuto && window.ClipAuto.cover(job.id, clip.index, clip.title) }),
        el("a", { class: "primary small", href: clip.url, download: `${clip.index + 1} ${clip.title || "clip"}.mp4`, text: "Download" }))));
    return card;
  }));
  if (window.ClipPosting) window.ClipPosting.paintOutput();
  if (!live) paintTaste();
}
// 1-5 stars on each clip: saved on this PC; after 8 rated moments the picker leans to what you like.
function stars(job, clip) {
  const box = el("div", { class: "stars", role: "radiogroup", "aria-label": "Rate this clip" });
  const paint = (n) => [...box.children].forEach((b, i) => { b.classList.toggle("on", i < n); b.setAttribute("aria-checked", String(i + 1 === n)); });
  for (let n = 1; n <= 5; n++) {
    box.append(el("button", { type: "button", role: "radio", class: "star", text: "★", title: `${n} star${n > 1 ? "s" : ""}`, "aria-label": `${n} star${n > 1 ? "s" : ""}`,
      onclick: async () => {
        paint(n);
        try {
          const r = await api(`/api/jobs/${job.id}/clips/${clip.index}/rating`, { method: "POST", body: { stars: n } });
          clip.rating = n;
          paintTaste(r.taste);
        } catch (err) { notice(err.message); }
      } }));
  }
  paint(clip.rating || 0);
  return box;
}
async function paintTaste(info) {
  const line = $("#tasteLine");
  if (!line) return;
  if (!info) { try { info = await api("/api/taste"); } catch { return; } }
  line.hidden = false;
  line.textContent = info.active
    ? `⭐ MasterClip learns what you like (${info.rated} clips rated). ${info.notes.join(" ")}`
    : `⭐ Rate your clips: MasterClip learns what you like. ${info.rated} of ${info.needed} rated so far, then new videos lean to what you rate high.`;
}
async function saveClip(jobId, index, edit) {
  state.phased = false;
  state.started = Date.now();
  try {
    await api(`/api/jobs/${jobId}/clips/${index}`, { method: "POST", body: edit });
    toast(`Updating clip ${index + 1}…`);
    watchJob(jobId);
  } catch (err) { notice(err.message); }
}
window.ClipStudio = { api, saveClip, toast, notice, clock, state, el, watchJob, go, pickUrl, runSearch, setLength };

$("#saveAll").addEventListener("click", async () => {
  try {
    const r = await api(`/api/jobs/${state.job.id}/save`, { method: "POST" });
    toast(`Saved ${r.files.length} clips to ${r.folder}`);
  } catch (err) { notice(err.message); }
});
// ---- Save folder (clips + downloads can live on another drive) ----
const storageUi = {
  info: null,
  paint(info) {
    this.info = info;
    $("#storageDot").hidden = !info.low;
    $("#moreDot").hidden = !info.low; // Save folder lives in the More menu
    $("#storageNow").textContent = `Clips go to ${info.clips_folder}. ${info.free} free on the ${info.drive}.`
      + (info.low ? " That's almost full: pick another drive." : "");
  },
  async refresh() {
    try { this.paint(await api("/api/storage")); } catch { /* engine still starting */ }
  },
  async open() {
    $("#storageMsg").textContent = "";
    await this.refresh();
    $("#storagePath").value = (this.info && this.info.folder) || "";
    $("#storageDlg").hidden = false;
    $("#storagePath").focus();
  },
  close() { $("#storageDlg").hidden = true; },
  async save(folder) {
    $("#storageMsg").textContent = "Checking the folder…";
    try {
      const info = await api("/api/storage", { method: "POST", body: { folder } });
      this.paint(info);
      this.close();
      toast(info.folder ? `Clips will be saved in ${info.folder}` : "Clips will be saved in Videos\MasterClip");
    } catch (err) { $("#storageMsg").textContent = err.message; }
  },
};
$("#openStorage").addEventListener("click", () => storageUi.open());
$("#storageClose").addEventListener("click", () => storageUi.close());
$("#storageCancel").addEventListener("click", () => storageUi.close());
$("#storageDefault").addEventListener("click", () => storageUi.save(null));
$("#storageSave").addEventListener("click", () => storageUi.save($("#storagePath").value.trim() || null));
$("#storagePath").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#storageSave").click(); });
$("#storageBrowse").addEventListener("click", async () => {
  const api_ = window.pywebview && window.pywebview.api;
  if (api_ && api_.pick_folder) {
    const picked = await api_.pick_folder($("#storagePath").value.trim());
    if (picked) $("#storagePath").value = picked;
  } else {
    $("#storageMsg").textContent = "Type the folder path in the box, for example D:\MasterClip.";
  }
});
storageUi.refresh();
window.ClipStorage = storageUi;
$("#openFolder").addEventListener("click", async () => {
  try { const r = await api("/api/open-output", { method: "POST" }); toast(`Opened ${r.folder}`); } catch (err) { notice(err.message); }
});
$("#startOver").addEventListener("click", () => {
  if (window.ClipCampaigns) window.ClipCampaigns.clear();
  state.job = null;
  state.source = null;
  $("#picked").hidden = true;
  $("#done").hidden = true;
  $("#makeBox").hidden = false;
  go(1);
});

/* ---------- boot ---------- */
window.ClipDesigner.onPick = (t) => { if (t && t.length) setLength(t.length); };
async function restoreLast() {
  if (new URLSearchParams(location.search).has("new")) {
    try { sessionStorage.removeItem("clipstudio.lastJob"); } catch { /* private mode */ }
    history.replaceState(null, "", "/");
    return;
  }
  let last = null;
  try { last = sessionStorage.getItem("clipstudio.lastJob") || localStorage.getItem("clipstudio.lastJob"); } catch { /* private mode */ }
  if (!last) return;
  let job;
  try { job = await api(`/api/jobs/${last}`); } catch { return; }
  const active = ["queued", "importing", "transcribing", "analyzing", "rendering", "live"].includes(job.status);
  if (active) {
    // Window was reloaded while clips were being made: go straight back to the progress.
    state.job = job;
    state.phased = !(job.clips && job.clips.length) && job.status !== "live";
    go(3);
    $("#makeBox").hidden = true;
    watchJob(job.id);
    return;
  }
  if (job.status === "complete" && job.clips && job.clips.length) {
    // Only offered: loading it into state.job here hid "Make my clips" for the next video.
    const n = job.clips.length;
    $("#wbTitle").textContent = `Your last ${n} clip${n === 1 ? " is" : "s are"} ready`;
    $("#wbMeta").textContent = job.title ? `From “${job.title}”` : "";
    $("#welcomeBack").hidden = false;
    $("#wbOpen").onclick = () => { state.job = job; state.source = null; go(3); showClips(job); };
  }
}
$("#wbHide").addEventListener("click", () => { $("#welcomeBack").hidden = true; });
(async function boot() {
  try {
    const data = await api("/api/templates");
    state.templates = data.templates;
    state.fonts = data.fonts;
    state.layouts = data.layouts || [];
    state.pcFonts = data.pc_fonts || [];
    state.captionStyles = data.caption_styles || [];
    window.ClipDesigner.init(data);
  } catch (err) {
    notice("MasterClip is still starting. Refresh in a few seconds.");
  }
  restoreLast();
  try { state.speech = await api("/api/speech-model"); } catch { /* optional */ }
})();
