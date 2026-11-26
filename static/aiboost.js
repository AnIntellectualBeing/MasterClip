"use strict";
/* AI boost picker on the template step: Off / Claude app / Codex app / Gemini app / Claude key / Gemini key.
   "App" = the program is installed on this PC (the server checks the PATH, like a terminal does). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const CS = () => window.ClipStudio;
  const APPS = { claude: "claude", codex: "codex", gemini: "gemini", local: "local" };
  const KEYS = {
    api: { has: "has_key", hint: "key_hint", placeholder: "sk-ant-… (from console.anthropic.com)", name: "Claude" },
    gemini_api: { has: "has_gemini_key", hint: "gemini_hint", placeholder: "AIza… or AQ… (free from aistudio.google.com/app/apikey)", name: "Gemini" },
  };
  const INSTALL = {
    claude: "Claude wasn't found automatically. Install Claude Code and sign in once, or paste where it is below.",
    codex: "Codex wasn't found automatically. Install Codex and sign in once, or paste where it is below.",
    gemini: "Not found on this PC. Install the Gemini CLI (npm install -g @google/gemini-cli), run “gemini” once and sign in with Google. Antigravity can't be called by other apps, but the same Google account works here.",
    local: "Ollama not found. Install it free from ollama.com, then run: ollama pull llama3.2",
  };
  let status = null;
  let wanted = null; // key mode picked but no key saved yet

  function paint(note, kind) {
    const s = status;
    if (!s) return;
    const shown = wanted || s.mode;
    $$("#aiMode button").forEach((b) => {
      b.classList.toggle("on", b.dataset.v === shown);
      const app = APPS[b.dataset.v];
      const missing = app && !s.installed[app];
      b.classList.toggle("missing", !!missing);
      b.title = missing ? INSTALL[app] : "";
      b.style.opacity = missing ? 0.5 : 1;
    });
    const keyMode = KEYS[shown];
    $("#aiKeyForm").hidden = !(keyMode && !s[keyMode.has]);
    if (keyMode) $("#aiKey").placeholder = keyMode.placeholder;
    $("#aiTest").hidden = s.mode === "off";
    const text = {
      off: "Off: everything stays on this PC. Moments are found from the sound, action and words.",
      claude: "Uses the Claude app on this PC (your own Claude plan). Only what was said is sent, never the video.",
      codex: "Uses the Codex app on this PC (your own ChatGPT plan). Only what was said is sent, never the video.",
      gemini: "Uses the Gemini CLI on this PC (free with a Google account). Only what was said is sent, never the video.",
      local: "Uses Ollama on this PC: free, private, no key, nothing leaves your PC. Fast with a graphics card. Get it at ollama.com, then run: ollama pull llama3.2",
      api: s.has_key ? `Uses your Claude API key (ending ${s.key_hint}). Each video costs a few cents.` : "Paste your Claude API key.",
      gemini_api: s.has_gemini_key ? `Uses your Gemini API key (ending ${s.gemini_hint}). Google's free tier covers normal use.` : "Paste your Gemini API key (free at aistudio.google.com/app/apikey: it starts with AIza or AQ).",
    }[shown];
    const state = $("#aiState");
    state.textContent = note || text;
    state.className = kind || "muted";
  }

  async function load() {
    try { status = await CS().api("/api/ai"); paint(); }
    catch { setTimeout(load, 3000); /* server starting: try again */ }
  }

  $$("#aiMode button").forEach((b) => b.addEventListener("click", async () => {
    const mode = b.dataset.v;
    if (!status) { try { status = await CS().api("/api/ai"); } catch (err) { $("#aiState").textContent = "AI settings couldn't load: " + err.message; return; } }
    const app = APPS[mode];
    wanted = null; // a half-picked key mode must not stay selected when another button is clicked
    $("#aiKeyForm").hidden = true;
    if (mode !== "local") $("#aiLocalBox").hidden = true;
    if (mode === "local") { hidePath(); await openLocal(); return; }
    if (app && status && !status.installed[app]) { wanted = null; askPath(app); paint(INSTALL[app], "bad"); return; }
    hidePath();
    const keyMode = KEYS[mode];
    if (keyMode && !status[keyMode.has]) {
      wanted = mode;
      paint(`Paste your ${keyMode.name} API key. It's kept in Windows Credential Manager.`);
      $("#aiKey").focus();
      return;
    }
    wanted = null;
    try { status = await CS().api("/api/ai", { method: "POST", body: { mode } }); paint(); } catch (err) { paint(err.message, "bad"); }
  }));

  $("#aiKeyForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = wanted || (KEYS[status.mode] ? status.mode : "api");
    try {
      status = await CS().api("/api/ai", { method: "POST", body: { mode, key: $("#aiKey").value } });
      $("#aiKey").value = "";
      wanted = null;
      paint();
      test();
    } catch (err) { paint(err.message, "bad"); }
  });

  // Local AI (Ollama): install -> get a model -> on. Each step is one button.
  async function openLocal() {
    try { status = await CS().api("/api/ai"); } catch { /* keep old */ }
    const box = $("#aiLocalBox");
    box.hidden = false;
    const installed = status && status.installed.local;
    const models = (status && status.local_models) || [];
    $("#aiOllamaGet").hidden = installed;
    $("#aiModelGet").hidden = !installed || models.length > 0;
    if (!installed) { paint("Local AI is free and private. Step 1: download and install Ollama, then press Local AI again.", "muted"); $("#aiLocalNote").textContent = ""; return; }
    if (!models.length) { paint("Ollama is installed. Step 2: get the free AI model (one time).", "muted"); return; }
    box.hidden = true;
    try { status = await CS().api("/api/ai", { method: "POST", body: { mode: "local" } }); paint(); test(); } catch (err) { paint(err.message, "bad"); }
  }
  $("#aiModelGet").addEventListener("click", async () => {
    const btn = $("#aiModelGet");
    btn.disabled = true;
    try {
      await CS().api("/api/ai/local-model", { method: "POST" });
      const tick = setInterval(async () => {
        const st = await CS().api("/api/ai/local-model");
        if (st.state === "pulling") { $("#aiLocalNote").textContent = "Downloading the AI model… you can keep using the app."; return; }
        clearInterval(tick);
        btn.disabled = false;
        if (st.state === "ready") { $("#aiLocalNote").textContent = "✓ Model ready."; openLocal(); }
        else $("#aiLocalNote").textContent = "The download stopped: " + (st.error || "try again");
      }, 3000);
    } catch (err) { btn.disabled = false; paint(err.message, "bad"); }
  });

  // Can't find the app by ourselves: let the buyer paste where it is (remembered for next time).
  let pathFor = null;
  function askPath(app) {
    pathFor = app;
    $("#aiPathForm").hidden = false;
    $("#aiPathHelp").hidden = false;
    $("#aiPathHelp").innerHTML = `Already installed? Open <b>Command Prompt</b>, type <b>where ${app}</b>, and paste the first line it shows here.`;
    $("#aiPath").placeholder = `C:\\Users\\you\\...\\${app}.exe`;
    $("#aiPath").focus();
  }
  function hidePath() { pathFor = null; $("#aiPathForm").hidden = true; $("#aiPathHelp").hidden = true; }
  $("#aiPathForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!pathFor) return;
    const app = pathFor;
    try {
      status = await CS().api("/api/ai/path", { method: "POST", body: { name: app, path: $("#aiPath").value } });
      if (!status.installed[app]) throw new Error("Still not found there. Paste the full path to the .exe or .cmd file.");
      $("#aiPath").value = "";
      hidePath();
      const mode = Object.keys(APPS).find((m) => APPS[m] === app);
      status = await CS().api("/api/ai", { method: "POST", body: { mode } });
      paint();
      test();
    } catch (err) { paint(err.message, "bad"); }
  });

  async function test() {
    const btn = $("#aiTest");
    btn.disabled = true;
    paint("Asking the AI for a test title…", "muted");
    try {
      const r = await CS().api("/api/ai/test", { method: "POST" });
      paint(`✓ Works. Test title: “${r.title}”`, "good");
    } catch (err) { paint(err.message, "bad"); }
    btn.disabled = false;
  }
  $("#aiTest").addEventListener("click", test);

  load();
})();
