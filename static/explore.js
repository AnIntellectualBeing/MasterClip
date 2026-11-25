"use strict";
/* Transcript explorer (read everything that was said, click first + last word -> a new clip)
   and the free chatbot helper (prompts per ~25 min part, paste answers back, tick, make clips). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const CS = () => window.ClipStudio;
  const X = { words: [], first: -1, last: -1, moments: [] };

  function job() { return CS().state.job; }
  function open(id) { $(id).hidden = false; document.body.style.overflow = "hidden"; }
  function close(id) { $(id).hidden = true; document.body.style.overflow = ""; }
  async function makeClips(clips) {
    const j = job();
    CS().state.phased = false;
    CS().state.started = Date.now();
    try {
      await CS().api(`/api/jobs/${j.id}/add-clips`, { method: "POST", body: { clips } });
      close("#transcriptDlg"); close("#chatHelpDlg");
      CS().toast(clips.length > 1 ? `Making ${clips.length} clips…` : "Making your clip…");
      CS().watchJob(j.id);
    } catch (err) { CS().notice(err.message); }
  }

  /* ---- transcript explorer ---- */
  function words(segments) {
    const out = [];
    for (const seg of segments) {
      if (seg.words && seg.words.length) seg.words.forEach((w) => out.push({ start: w.start, end: w.end, text: w.text }));
      else {
        const toks = String(seg.text || "").split(/\s+/).filter(Boolean), step = (seg.end - seg.start) / Math.max(1, toks.length);
        toks.forEach((t, i) => out.push({ start: seg.start + i * step, end: seg.start + (i + 1) * step, text: t }));
      }
    }
    return out.sort((a, b) => a.start - b.start);
  }
  function paintText() {
    const box = $("#trText");
    const frag = document.createDocumentFragment();
    let lastStamp = -999;
    X.words.forEach((w, i) => {
      if (w.start - lastStamp >= 30) {
        lastStamp = w.start;
        frag.append(Object.assign(document.createElement("span"), { className: "tr-stamp", textContent: CS().clock(w.start) }));
      }
      const s = Object.assign(document.createElement("span"), { className: "tr-word", textContent: w.text + " " });
      s.dataset.i = i;
      frag.append(s);
    });
    box.replaceChildren(frag);
    if (!X.words.length) box.textContent = "This video has no captions yet.";
  }
  function paintPick() {
    const lo = Math.min(X.first, X.last < 0 ? X.first : X.last), hi = Math.max(X.first, X.last);
    document.querySelectorAll("#trText .tr-word").forEach((n) => {
      const i = Number(n.dataset.i);
      n.classList.toggle("tr-on", X.first >= 0 && i >= lo && i <= (X.last < 0 ? X.first : hi));
    });
    if (X.first < 0) { $("#trPick").textContent = "Nothing picked yet."; $("#trMake").disabled = true; return; }
    if (X.last < 0) { $("#trPick").textContent = `Starts at ${CS().clock(X.words[X.first].start)}. Now click the last word.`; $("#trMake").disabled = true; return; }
    const s = X.words[lo].start, e = X.words[hi].end + 0.4, len = e - s;
    $("#trPick").textContent = `${CS().clock(s)} → ${CS().clock(e)} · ${len.toFixed(1)} s` + (len < 3 ? " (pick at least 3 s)" : len > 180 ? " (keep it under 3 min)" : "");
    $("#trMake").disabled = len < 3 || len > 180;
  }
  $("#trText").addEventListener("click", (e) => {
    const n = e.target.closest(".tr-word");
    if (!n) return;
    const i = Number(n.dataset.i);
    if (X.first < 0 || X.last >= 0) { X.first = i; X.last = -1; } else X.last = i;
    paintPick();
  });
  $("#trSearch").addEventListener("input", () => {
    const q = $("#trSearch").value.trim().toLowerCase();
    let first = null, count = 0;
    document.querySelectorAll("#trText .tr-word").forEach((n) => {
      const hit = q.length >= 2 && n.textContent.toLowerCase().includes(q);
      n.classList.toggle("tr-hit", hit);
      if (hit) { count += 1; first = first || n; }
    });
    $("#trFound").textContent = q.length < 2 ? "" : count ? `${count} found` : "Not found";
    if (first) first.scrollIntoView({ block: "center" });
  });
  $("#trClear").addEventListener("click", () => { X.first = X.last = -1; paintPick(); });
  $("#trMake").addEventListener("click", () => {
    const lo = Math.min(X.first, X.last), hi = Math.max(X.first, X.last);
    makeClips([{ start: Math.max(0, X.words[lo].start - 0.2), end: X.words[hi].end + 0.4 }]);
  });
  $("#trClose").addEventListener("click", () => close("#transcriptDlg"));
  $("#openTranscript").addEventListener("click", async () => {
    if (!job()) return;
    try {
      const data = await CS().api(`/api/jobs/${job().id}/captions`);
      X.words = words(data.segments || []);
      X.first = X.last = -1;
      $("#trSearch").value = ""; $("#trFound").textContent = "";
      paintText(); paintPick();
      open("#transcriptDlg");
    } catch (err) { CS().notice(err.message); }
  });

  /* ---- free chatbot helper ---- */
  async function loadParts() {
    const data = await CS().api(`/api/jobs/${job().id}/chat-helper?minutes=${$("#chMinutes").value}`);
    $("#chParts").replaceChildren(...data.parts.map((p, i) => {
      const row = document.createElement("div");
      row.className = "ch-part";
      const copy = Object.assign(document.createElement("button"), { type: "button", className: "ghost small", textContent: "📋 Copy" });
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(p.prompt); }
        catch {
          const t = Object.assign(document.createElement("textarea"), { value: p.prompt });
          document.body.append(t); t.select(); document.execCommand("copy"); t.remove();
        }
        copy.textContent = "✓ Copied"; row.classList.add("ch-copied");
      });
      row.append(Object.assign(document.createElement("span"), { textContent: `Part ${i + 1} · ${CS().clock(p.start)} – ${CS().clock(p.end)}` }), copy);
      return row;
    }));
  }
  function paintMoments() {
    $("#chMoments").replaceChildren(...X.moments.map((m) => {
      const label = document.createElement("label");
      label.className = "ch-moment";
      const box = Object.assign(document.createElement("input"), { type: "checkbox", checked: m.on });
      box.addEventListener("change", () => { m.on = box.checked; $("#chMake").disabled = !X.moments.some((x) => x.on); });
      label.append(box, ` ${CS().clock(m.start)} – ${CS().clock(m.end)} `, Object.assign(document.createElement("b"), { textContent: m.title || "(no title)" }));
      return label;
    }));
    $("#chMake").disabled = !X.moments.some((x) => x.on);
  }
  $("#chRead").addEventListener("click", async () => {
    try {
      const r = await CS().api(`/api/jobs/${job().id}/chat-helper/parse`, { method: "POST", body: { text: $("#chAnswer").value } });
      X.moments = r.moments.map((m) => ({ ...m, on: true }));
      paintMoments();
      CS().toast(`${X.moments.length} moment${X.moments.length === 1 ? "" : "s"} found`);
    } catch (err) { CS().notice(err.message); }
  });
  $("#chMake").addEventListener("click", () => {
    const picked = X.moments.filter((m) => m.on).slice(0, 30).map((m) => ({ start: m.start, end: m.end, title: m.title }));
    if (picked.length) makeClips(picked);
  });
  $("#chMinutes").addEventListener("change", () => loadParts().catch((err) => CS().notice(err.message)));
  $("#chClose").addEventListener("click", () => close("#chatHelpDlg"));
  $("#openChatHelp").addEventListener("click", async () => {
    if (!job()) return;
    try {
      await loadParts();
      X.moments = []; paintMoments();
      open("#chatHelpDlg");
    } catch (err) { CS().notice(err.message); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("#transcriptDlg").hidden) close("#transcriptDlg");
    if (!$("#chatHelpDlg").hidden) close("#chatHelpDlg");
  });
})();
