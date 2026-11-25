"use strict";
/* Creators auto-watch, Phone remote, update banner, Report a problem and clip covers. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const C = () => window.ClipStudio;
  const api = (path, opts) => C().api(path, opts);
  const say = (m) => C() && C().toast(m);

  function dialog(id, title, body, wide) {
    const box = document.createElement("div");
    box.id = id;
    box.className = "editor";
    box.hidden = true;
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", id + "Title");
    box.innerHTML = `<div class="editor-shell ${wide ? "auto-shell wide" : "auto-shell"}">
      <header class="editor-top"><strong id="${id}Title"></strong>
      <button class="ghost" type="button" data-close aria-label="Close">✕</button></header>
      <div class="sched-body auto-body">${body}</div></div>`;
    $(`#${id}Title`, box).textContent = title;
    box.addEventListener("click", (e) => { if (e.target === box || e.target.closest("[data-close]")) close(box); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !box.hidden) close(box); });
    document.body.append(box);
    return box;
  }
  function open(box) { box.hidden = false; const f = box.querySelector("input,textarea,button:not([data-close])"); if (f) f.focus(); }
  function close(box) { box.hidden = true; if (box.onclose) box.onclose(); }
  function topButton(text, title, onClick, before) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "ghost"; b.textContent = text; b.title = title;
    b.addEventListener("click", onClick);
    const bar = $(".top-actions");
    if (bar) bar.insertBefore(b, before || null);
    return b;
  }
  function clipName(t) { return (t || "clip").replace(/[^\w\s()'-]+/g, " ").trim().slice(0, 60) || "clip"; }

  /* ---------------- Creators ---------------- */
  const creatorsDlg = dialog("creatorsDlg", "Creators: clip a streamer automatically", `
    <p class="muted">Add a streamer. When they go live, MasterClip records the stream and clips the big chat moments by itself. Keep MasterClip open.</p>
    <form id="crForm" class="auto-form">
      <label class="field"><span>Twitch name or YouTube channel</span>
        <input id="crText" type="text" maxlength="300" autocomplete="off" spellcheck="false" placeholder="e.g. shroud  or  youtube.com/@name"></label>
      <div class="auto-row">
        <label class="field"><span>Clip style</span><select id="crStyle"></select></label>
        <label class="field"><span>Clip length</span><select id="crLen"><option value="15">15 s</option><option value="30" selected>30 s</option><option value="45">45 s</option><option value="60">60 s</option></select></label>
      </div>
      <label class="switch"><input id="crCaps" type="checkbox" checked><span></span> Captions</label>
      <p class="hint">The style is saved now. Changing a template later won't change this creator.</p>
      <button class="primary" type="submit">Add creator</button>
      <p id="crMsg" class="hint auto-err" role="status"></p>
    </form>
    <p id="crNote" class="notice-soft" hidden></p>
    <div id="crList" class="auto-list"></div>
    <p class="hint">One stream at a time; the others wait their turn. A recording stops after 3 hours or when the disk is almost full, and the big recording file is deleted after its clips are made. <b>Stop</b> pauses a creator for 30 minutes.</p>`, true);
  let crTimer = null;

  async function loadStyles() {
    const sel = $("#crStyle");
    if (sel.options.length) return;
    try {
      const t = await api("/api/templates");
      for (const item of t.templates) {
        const o = document.createElement("option");
        o.value = item.id; o.textContent = (item.mine ? "★ " : "") + item.name + (item.kind === "long" ? " (16:9)" : "");
        sel.append(o);
      }
      sel.value = "big_captions";
    } catch { /* engine starting */ }
  }
  function paintCreators(data) {
    $("#crNote").hidden = !data.note;
    $("#crNote").textContent = data.note || "";
    const list = $("#crList");
    list.textContent = "";
    if (!data.creators.length) {
      const p = document.createElement("p"); p.className = "soft-state"; p.textContent = "No creators yet. Add one above.";
      list.append(p); return;
    }
    for (const c of data.creators) {
      const row = C().el("div", { class: "auto-item" + (c.recording ? " rec" : "") });
      const head = C().el("div", { class: "auto-item-head" },
        C().el("span", { class: "badge" + (c.recording ? " live" : ""), text: c.kind === "twitch" ? "Twitch" : "YouTube" }),
        C().el("strong", { text: c.name }),
        C().el("span", { class: "muted", text: c.status }));
      const actions = C().el("div", { class: "row-gap" });
      if (c.recording) actions.append(C().el("button", { class: "danger small", type: "button", text: "■ Stop", title: "Stop this recording and pause this creator for 30 minutes", onclick: () => act(`/api/creators/${c.id}/stop`) }));
      else if (c.paused) actions.append(C().el("button", { class: "ghost small", type: "button", text: "Resume now", onclick: () => act(`/api/creators/${c.id}/switch`, { on: true }) }));
      actions.append(C().el("button", { class: "ghost small", type: "button", text: c.on ? "Turn off" : "Turn on",
        title: c.on ? "Stop checking this creator" : "Check this creator again", onclick: () => act(`/api/creators/${c.id}/switch`, { on: !c.on }) }));
      actions.append(C().el("button", { class: "ghost small", type: "button", text: "Remove", onclick: () => { if (confirm(`Remove ${c.name}? Their clips stay in My clips.`)) act(`/api/creators/${c.id}`, null, "DELETE"); } }));
      row.append(head, C().el("p", { class: "hint", text: `${c.style}${c.length ? " · " + c.length + " s clips" : ""}${c.note ? " · " + c.note : ""}` }), actions);
      if (c.clips.length) {
        const ul = C().el("ul", { class: "auto-clips" });
        for (const k of c.clips) ul.append(C().el("li", {}, C().el("span", { text: k.title }),
          C().el("a", { href: k.url, download: clipName(k.title) + ".mp4", text: "Download" })));
        row.append(ul);
      }
      list.append(row);
    }
  }
  async function act(path, body, method) {
    try { paintCreators(await api(path, { method: method || "POST", body: body || undefined })); }
    catch (e) { $("#crMsg").textContent = e.message; }
  }
  async function refreshCreators() { try { paintCreators(await api("/api/creators")); } catch { /* ignore */ } }
  $("#crForm", creatorsDlg).addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#crMsg").textContent = "";
    const text = $("#crText").value.trim();
    if (!text) { $("#crMsg").textContent = "Type a Twitch name or paste a YouTube channel link."; return; }
    try {
      paintCreators(await api("/api/creators", { method: "POST", body: { text, template: $("#crStyle").value, length: Number($("#crLen").value), captions: $("#crCaps").checked } }));
      $("#crText").value = "";
      say("Added. MasterClip checks every minute.");
    } catch (err) { $("#crMsg").textContent = err.message; }
  });
  creatorsDlg.onclose = () => { clearInterval(crTimer); crTimer = null; };
  async function openCreators() {
    open(creatorsDlg);
    await loadStyles();
    await refreshCreators();
    clearInterval(crTimer);
    crTimer = setInterval(() => { if (!document.hidden) refreshCreators(); }, 5000);
  }

  /* ---------------- Phone remote ---------------- */
  const remoteDlg = dialog("remoteDlg", "Phone remote", `
    <p class="muted">Use your phone as a remote for live clipping: paste a live link, press <b>Clip now</b>, watch the hype meter and download clips. Your phone and this PC must be on the same Wi-Fi.</p>
    <div id="rmOff">
      <button id="rmOn" class="primary big" type="button">Turn on the phone remote</button>
      <p class="hint">Off unless you turn it on (and off again when MasterClip restarts). Only devices on your home network can open it, and they need the PIN. Windows may ask to allow MasterClip on private networks: click <b>Allow</b>.</p>
    </div>
    <div id="rmOnBox" hidden>
      <p class="hint">On your phone, open this address in the browser:</p>
      <p class="auto-big" id="rmUrl"></p>
      <p class="hint">Then type this PIN:</p>
      <p class="auto-big pin" id="rmPin"></p>
      <p id="rmLocked" class="notice-soft" hidden>Locked after 5 wrong PINs. Press <b>New PIN</b> to unlock now.</p>
      <div class="row-between"><button id="rmNew" class="ghost" type="button" title="Signs out every phone and makes a new PIN">New PIN</button><button id="rmOffBtn" class="danger" type="button">Turn off</button></div>
    </div>
    <p id="rmMsg" class="hint auto-err" role="status"></p>`);
  function paintRemote(r) {
    $("#rmOff").hidden = r.on; $("#rmOnBox").hidden = !r.on;
    $("#rmUrl").textContent = r.url || ""; $("#rmPin").textContent = r.pin ? r.pin.replace(/(\d{3})(\d{3})/, "$1 $2") : "";
    $("#rmLocked").hidden = !r.locked;
    if (remoteBtn) remoteBtn.classList.toggle("on", !!r.on);
  }
  async function remoteCall(body, path) {
    $("#rmMsg").textContent = "";
    try { paintRemote(await api(path || "/api/remote", { method: "POST", body })); }
    catch (e) { $("#rmMsg").textContent = e.message; }
  }
  $("#rmOn", remoteDlg).addEventListener("click", () => remoteCall({ on: true }));
  $("#rmOffBtn", remoteDlg).addEventListener("click", () => remoteCall({ on: false }));
  $("#rmNew", remoteDlg).addEventListener("click", () => remoteCall({}, "/api/remote/new-pin"));
  async function openRemote() {
    open(remoteDlg);
    try { paintRemote(await api("/api/remote")); } catch (e) { $("#rmMsg").textContent = e.message; }
  }

  /* ---------------- Report a problem ---------------- */
  const reportDlg = dialog("reportDlg", "Report a problem", `
    <label class="field"><span>What happened?</span><textarea id="rpWhat" rows="3" maxlength="2000" placeholder="e.g. The clips stopped after 10 minutes of a live stream"></textarea></label>
    <label class="field"><span>What were you doing just before? <small class="muted">(optional)</small></span><textarea id="rpDoing" rows="2" maxlength="1000"></textarea></label>
    <label class="switch"><input id="rpLogs" type="checkbox" checked><span></span> Add version, Windows version and the last log lines</label>
    <details class="auto-details"><summary>See what will be shared</summary><pre id="rpDetails"></pre></details>
    <p class="hint">MasterClip doesn't send anything. You copy the report or open an email and send it yourself. Links and keys are removed from the log lines.</p>
    <div class="row-between"><button id="rpCopy" class="ghost" type="button">Copy report</button><button id="rpMail" class="primary" type="button">Open email</button></div>
    <p id="rpMsg" class="hint" role="status"></p>`);
  let reportInfo = null;
  function reportText() {
    const parts = [`What happened:\n${$("#rpWhat").value.trim() || "(not filled in)"}`];
    if ($("#rpDoing").value.trim()) parts.push(`What I was doing:\n${$("#rpDoing").value.trim()}`);
    if ($("#rpLogs").checked && reportInfo) {
      parts.push(`MasterClip ${reportInfo.version}\nWindows: ${reportInfo.os}\nTime: ${new Date().toString()}`);
      parts.push(reportInfo.logs);
    }
    return parts.join("\n\n");
  }
  function paintDetails() { $("#rpDetails").textContent = $("#rpLogs").checked && reportInfo ? `MasterClip ${reportInfo.version}\nWindows: ${reportInfo.os}\n\n${reportInfo.logs}` : "Only what you type above."; }
  $("#rpLogs", reportDlg).addEventListener("change", paintDetails);
  async function copyReport() {
    try { await navigator.clipboard.writeText(reportText()); return true; } catch { return false; }
  }
  $("#rpCopy", reportDlg).addEventListener("click", async () => {
    $("#rpMsg").textContent = (await copyReport()) ? "Copied. Paste it into an email or a message to us." : "Couldn't copy. Select the text under 'See what will be shared'.";
  });
  $("#rpMail", reportDlg).addEventListener("click", async () => {
    if (!reportInfo) return;
    const copied = await copyReport();
    let body = reportText();
    if (body.length > 1800) body = body.slice(0, 1800) + "\n\n[cut short: the full report is on your clipboard, paste it here]";
    const a = document.createElement("a");
    a.href = `mailto:${reportInfo.email}?subject=${encodeURIComponent(reportInfo.subject)}&body=${encodeURIComponent(body)}`;
    a.target = "_blank"; a.rel = "noopener";
    document.body.append(a); a.click(); a.remove();
    $("#rpMsg").textContent = `Your email app should open. No email app? Write to ${reportInfo.email}` + (copied ? " and paste the report (it's copied)." : ".");
  });
  async function openReport() {
    open(reportDlg);
    $("#rpMsg").textContent = "";
    try { reportInfo = await api("/api/report"); } catch { reportInfo = null; }
    paintDetails();
  }

  /* ---------------- Cover ---------------- */
  const coverDlg = dialog("coverDlg", "Cover picture", `
    <p id="cvWait" class="muted">Making the cover… a few seconds.</p>
    <img id="cvImg" class="auto-cover" alt="Cover picture of the clip" hidden>
    <p class="hint">The best frame of the clip with its title. It's used as the thumbnail when you post this clip (YouTube shows it only on phone-verified channels). Change the title on the clip card, then make it again.</p>
    <div class="row-between"><button class="ghost" type="button" data-close>Close</button><a id="cvDown" class="primary" href="#">Download</a></div>`);
  function cover(jobId, index, title) {
    const url = `/api/jobs/${jobId}/clips/${index}/cover?t=${Date.now()}`;
    const img = $("#cvImg");
    img.hidden = true; $("#cvWait").hidden = false; $("#cvWait").textContent = "Making the cover… a few seconds.";
    img.onload = () => { img.hidden = false; $("#cvWait").hidden = true; };
    img.onerror = () => { $("#cvWait").textContent = "Couldn't make a cover for this clip. Try again after it finishes."; };
    img.src = url;
    const down = $("#cvDown");
    down.href = url + "&download=1";
    down.setAttribute("download", clipName(title) + " cover.jpg");
    open(coverDlg);
  }

  /* ---------------- Update banner ---------------- */
  async function checkUpdate() {
    let u;
    try { u = await api("/api/update-check"); } catch { return; }
    if (!u || !u.available) return;
    let hidden = "";
    try { hidden = localStorage.getItem("mc-update-hidden") || ""; } catch { /* private mode */ }
    if (hidden === u.latest) return;
    const bar = document.createElement("div");
    bar.className = "notice-soft update-banner";
    bar.setAttribute("role", "status");
    const text = document.createElement("span");
    text.textContent = `New version ${u.latest} is ready (you have ${u.current}). It doesn't install by itself.`;
    const get = document.createElement("a");
    get.className = "primary small"; get.href = u.url; get.target = "_blank"; get.rel = "noopener"; get.textContent = "Download";
    const later = document.createElement("button");
    later.type = "button"; later.className = "ghost small"; later.textContent = "Later";
    later.addEventListener("click", () => { try { localStorage.setItem("mc-update-hidden", u.latest); } catch { /* ignore */ } bar.remove(); });
    bar.append(text, get, later);
    const page = $("main.page");
    if (page) page.insertBefore(bar, $("#notice") || page.firstChild);
  }

  /* ---------------- top bar ---------------- */
  const help = $('.top-actions a[href="/help"]');
  topButton("📡 Creators", "Clip streamers automatically when they go live", openCreators, help);
  const remoteBtn = topButton("📱 Phone remote", "Control live clipping from your phone", openRemote, help);
  const report = document.createElement("button");
  report.type = "button"; report.className = "ghost small report-fab"; report.textContent = "Report a problem";
  report.title = "Tell us what went wrong (nothing is sent automatically)";
  report.addEventListener("click", openReport);
  document.body.append(report);
  setTimeout(checkUpdate, 1500);

  window.ClipAuto = { cover, openCreators, openRemote, openReport };
})();
