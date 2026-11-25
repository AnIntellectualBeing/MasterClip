"use strict";
/* Clip editor: live preview (stage.js, same maths as the renderer) + trim, layout, captions, title. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const COLORS = ["#FFFFFF", "#FFD233", "#7CFFB2", "#5EC8FF", "#FF7AB6", "#FF5A3C", "#111111"];
  const S = () => window.ClipStage;

  const ed = { job: null, index: 0, clip: null, d: null, start: 0, end: 0, title: "", words: [], video: null, raf: 0, playing: false, stage: null,
    all: [], cues: [], cuesDirty: false, importedCaptions: false };

  let framing = null;
  function framingPanel() {
    if (!framing) {
      framing = window.ClipFraming.mount($("#eFraming"), {
        context: "clip",
        get: () => ed.d,
        changed: () => { stage().setDesign(ed.d); syncControls(); },
        media: () => ed.video,
        autoCam: () => (ed.job && ed.job.cam_roi) || null,
        stage: () => stage(),
        layouts: () => window.ClipStudio.state.layouts || [],
      });
    }
    return framing;
  }
  function stage() {
    if (!ed.stage) {
      ed.stage = S().create($("#stageHost"), { resolution: 0.5, onChange: () => syncControls() });
    }
    return ed.stage;
  }
  function draw() {
    const v = ed.video;
    if (!v || !ed.d) return;
    const st = stage();
    st.setTime(v.currentTime);
    st.paint();
    if (framing && framing.open) framing.paintSource();
    $("#timeLabel").textContent = window.ClipStudio.clock(Math.max(0, v.currentTime - ed.start)) + " / " + window.ClipStudio.clock(ed.end - ed.start);
  }
  function loop() {
    const v = ed.video;
    if (v && ed.playing && v.currentTime >= ed.end) v.currentTime = ed.start;
    draw();
    ed.raf = requestAnimationFrame(loop);
  }
  function refresh() {
    const st = stage();
    st.setTitle(ed.title);
    st.setDesign(ed.d);
    syncControls();
  }

  /* ---- controls ---- */
  function seg(id, value) { $$(`#${id} button`).forEach((b) => b.classList.toggle("on", b.dataset.v === String(value))); }
  function syncControls() {
    const d = ed.d, c = d.caption, t = d.title;
    $("#eTemplate").value = d.template;
    seg("eFill", d.fill);
    const l = S().effectiveLayout(d);
    seg("eCamPos", !d.cam_roi ? "" : l === "stack" ? d.cam_side : l === "corner" ? "corner" : d.hide_cam ? "hidden" : "keep");
    $("#camHint").textContent = !d.cam_roi ? "No face cam found yet. Draw a box over the streamer's cam to use it."
      : l === "corner" ? "Drag the cam box anywhere on the preview, or pull a corner to resize it."
      : d.hide_cam ? "The streamer's cam is blurred out of the picture."
      : "We found the streamer's cam. Move it, put it in a box, or remove it.";
    $$("#eCamPos button").forEach((b) => { b.disabled = !d.cam_roi; b.style.opacity = d.cam_roi ? 1 : 0.45; });
    $("#eSplitField").hidden = l !== "stack" || !!d.cam_rect;
    $("#eSplit").value = d.split; $("#eSplitVal").textContent = Math.round(d.split * 100) + "%";
    $("#eFocusField").hidden = d.fill !== "zoom" || l === "split";
    $("#eFocus").value = d.focus_x;
    seg("eCapStyle", c.style);
    $$("#eFonts button").forEach((b) => b.classList.toggle("on", b.dataset.font === c.font));
    $("#eCapSize").value = c.size; $("#eCapSizeVal").textContent = c.size;
    $$("#eCapColor button").forEach((b) => b.classList.toggle("on", b.dataset.c === c.color));
    $$("#eCapHi button").forEach((b) => b.classList.toggle("on", b.dataset.c === c.highlight));
    $("#eOutline").value = c.outline; $("#eOutlineVal").textContent = c.outline;
    $$("#eCapEdge button").forEach((b) => b.classList.toggle("on", b.dataset.c === c.outline_color));
    $("#ePcFont").value = $(`#ePcFont option[value="${CSS.escape(c.font)}"]`) ? c.font : "";
    const own = ["punch", "twotone", "none"].includes(c.style);
    $("#eAnimField").hidden = c.style === "none";
    $$("#eAnim button").forEach((b) => { b.disabled = own; b.classList.toggle("on", !own && b.dataset.v === (c.animation || "auto")); });
    $("#eAnimHint").hidden = !own;
    $("#eWordsRow").hidden = c.style === "none" || c.style === "classic";
    $("#eWords").textContent = c.words;
    $("#eUpper").checked = c.uppercase;
    $("#eEmoji").checked = !!c.emoji;
    if (document.activeElement !== $("#eTitle")) $("#eTitle").value = ed.title;
    seg("eTitleStyle", t.style);
    $("#eTitleSize").value = t.size; $("#eTitleSizeVal").textContent = t.size;
    const whole = !(Number(t.seconds) > 0);
    $("#eTitleWhole").checked = whole;
    $("#eTitleSecsField").hidden = whole;
    $("#eTitleSecs").value = whole ? 3 : Math.min(60, Math.max(2, t.seconds));
    $("#eTitleSecsVal").textContent = $("#eTitleSecs").value;
    $("#eTitleShow").checked = t.show !== false;
    $("#eCredit").checked = d.credit_on;
    $("#eCreditName").textContent = d.credit ? "@" + d.credit.replace(/^@/, "") : "(no streamer name)";
    const span = ed.end - ed.start;
    $("#trimLabel").textContent = `${window.ClipStudio.clock(ed.start)} → ${window.ClipStudio.clock(ed.end)} · ${span.toFixed(1)} s`;
    framingPanel().sync();
  }
  function onSeg(id, fn) {
    $$(`#${id} button`).forEach((b) => b.addEventListener("click", () => { fn(b.dataset.v); refresh(); }));
  }
  function swatches(id, key) {
    const box = $(`#${id}`);
    COLORS.forEach((c) => box.append(Object.assign(document.createElement("button"), { type: "button", title: c, style: `background:${c}`, onclick: () => { ed.d.caption[key] = c; refresh(); } })));
    $$("button", box).forEach((b, i) => { b.dataset.c = COLORS[i]; });
    const pick = Object.assign(document.createElement("input"), { type: "color", title: "Any colour" });
    pick.addEventListener("input", () => { ed.d.caption[key] = pick.value.toUpperCase(); refresh(); });
    box.append(pick);
  }
  swatches("eCapColor", "color");
  swatches("eCapHi", "highlight");
  swatches("eCapEdge", "outline_color");
  onSeg("eAnim", (v) => { ed.d.caption.animation = v; });
  $("#eWordsMinus").addEventListener("click", () => { ed.d.caption.words = Math.max(1, ed.d.caption.words - 1); refresh(); });
  $("#eWordsPlus").addEventListener("click", () => { ed.d.caption.words = Math.min(10, ed.d.caption.words + 1); refresh(); });
  $("#ePcFont").addEventListener("change", (e) => { if (e.target.value) { ed.d.caption.font = e.target.value; refresh(); } });
  $("#eTitleWhole").addEventListener("change", (e) => { ed.d.title.seconds = e.target.checked ? 0 : Number($("#eTitleSecs").value) || 3; refresh(); });
  $("#eTitleSecs").addEventListener("input", (e) => { ed.d.title.seconds = Number(e.target.value); refresh(); });

  /* my caption styles */
  function paintMyStyles() {
    const list = window.ClipStudio.state.captionStyles || [];
    $("#eMyStyles").replaceChildren(...list.map((s) => {
      const chip = Object.assign(document.createElement("span"), { className: "chip my-style" });
      chip.append(
        Object.assign(document.createElement("button"), { type: "button", textContent: s.name, title: "Use this caption style",
          onclick: () => { Object.assign(ed.d.caption, s.caption); refresh(); window.ClipStudio.toast(`Using “${s.name}”`); } }),
        Object.assign(document.createElement("button"), { type: "button", textContent: "✕", className: "x", title: "Delete this style", ariaLabel: `Delete ${s.name}`,
          onclick: async () => {
            try {
              await window.ClipStudio.api(`/api/caption-styles/${s.id}`, { method: "DELETE" });
              window.ClipStudio.state.captionStyles = list.filter((x) => x.id !== s.id);
              paintMyStyles();
            } catch (err) { window.ClipStudio.notice(err.message); }
          } }));
      return chip;
    }));
    if (!list.length) $("#eMyStyles").append(Object.assign(document.createElement("span"), { className: "muted", textContent: "None yet." }));
  }
  $("#eSaveStyle").addEventListener("click", () => { $("#eStyleNameRow").hidden = false; $("#eStyleName").focus(); });
  async function saveStyle() {
    const name = $("#eStyleName").value.trim();
    if (!name) { window.ClipStudio.toast("Type a name for the style first."); return; }
    try {
      const item = await window.ClipStudio.api("/api/caption-styles", { method: "POST", body: { name, caption: ed.d.caption } });
      const S_ = window.ClipStudio.state;
      S_.captionStyles = [...(S_.captionStyles || []).filter((x) => x.name.toLowerCase() !== name.toLowerCase()), item];
      $("#eStyleNameRow").hidden = true; $("#eStyleName").value = "";
      paintMyStyles();
      window.ClipStudio.toast(`Saved “${name}”`);
    } catch (err) { window.ClipStudio.notice(err.message); }
  }
  $("#eStyleNameOk").addEventListener("click", saveStyle);
  $("#eStyleName").addEventListener("keydown", (e) => { if (e.key === "Enter") saveStyle(); });

  /* improve heading */
  $("#eImprove").addEventListener("click", async () => {
    const btn = $("#eImprove");
    btn.disabled = true; btn.textContent = "✨ Thinking…";
    try {
      const r = await window.ClipStudio.api(`/api/jobs/${ed.job.id}/clips/${ed.index}/title-ideas`, { method: "POST" });
      $("#eImproveNote").textContent = r.note;
      $("#eIdeas").replaceChildren(...r.titles.map((s) => Object.assign(document.createElement("button"), {
        type: "button", className: "chip", textContent: s, onclick: () => { ed.title = s; refresh(); },
      })));
    } catch (err) { window.ClipStudio.notice(err.message); }
    btn.disabled = false; btn.textContent = "✨ Improve heading";
  });

  /* ---- Words tab: fix the caption text and timing of this clip ---- */
  const r1 = (v) => Math.round(v * 100) / 100;
  function cueWords(cue) {
    const toks = String(cue.text || "").split(/\s+/).filter(Boolean);
    if (cue.words && cue.words.length === toks.length) return cue.words.map((w, i) => ({ start: w.start, end: w.end, text: toks[i] }));
    const step = (cue.end - cue.start) / Math.max(1, toks.length);
    return toks.map((t, i) => ({ start: r1(cue.start + i * step), end: r1(cue.start + (i + 1) * step), text: t }));
  }
  function inClip(c) { return c.end > ed.clip.start - 0.01 && c.start < ed.clip.end + 0.01; }
  function rebuildWords() {
    const all = [...ed.all.filter((c) => !inClip(c)), ...ed.cues].sort((a, b) => a.start - b.start);
    ed.words = [];
    for (const c of all) ed.words.push(...cueWords(c));
    stage().setWords(ed.words);
  }
  function wordsChanged() { ed.cuesDirty = true; rebuildWords(); paintCues(); draw(); }
  function retime(cue, start, end) {
    const old = cue.end - cue.start || 1, ws = cueWords(cue);
    cue.words = ws.map((w) => ({ text: w.text, start: r1(start + ((w.start - cue.start) / old) * (end - start)), end: r1(start + ((w.end - cue.start) / old) * (end - start)) }));
    cue.start = r1(start); cue.end = r1(end);
  }
  function paintCues() {
    const list = $("#wList");
    $("#wEmpty").hidden = ed.cues.length > 0;
    list.replaceChildren(...ed.cues.map((cue, i) => {
      const row = document.createElement("div");
      row.className = "word-row";
      const num = (val, label, fn) => {
        const inp = Object.assign(document.createElement("input"), { type: "number", step: "0.1", value: r1(val - ed.start).toFixed(1), className: "num", title: label, ariaLabel: label });
        inp.addEventListener("change", () => { fn(ed.start + Number(inp.value)); wordsChanged(); });
        return inp;
      };
      const text = Object.assign(document.createElement("input"), { type: "text", value: cue.text, className: "word-text", ariaLabel: `Caption line ${i + 1}` });
      text.addEventListener("focus", () => { if (ed.video) ed.video.currentTime = Math.max(ed.start, cue.start); });
      text.addEventListener("input", () => {
        const ws = cue.words, toks = text.value.split(/\s+/).filter(Boolean);
        cue.text = text.value;
        if (ws && ws.length === toks.length) ws.forEach((w, k) => { w.text = toks[k]; });
        ed.cuesDirty = true; rebuildWords(); draw();
      });
      const btn = (label, title, fn, disabled) => Object.assign(document.createElement("button"), { type: "button", className: "ghost tiny", textContent: label, title, disabled: !!disabled, onclick: fn });
      const toks = String(cue.text).split(/\s+/).filter(Boolean);
      row.append(text, Object.assign(document.createElement("div"), { className: "word-ctrl" }));
      row.lastChild.append(
        num(cue.start, "Starts at (seconds into the clip)", (v) => retime(cue, Math.max(0, Math.min(v, cue.end - 0.1)), cue.end)),
        Object.assign(document.createElement("span"), { className: "muted", textContent: "→" }),
        num(cue.end, "Ends at (seconds into the clip)", (v) => retime(cue, cue.start, Math.max(v, cue.start + 0.1))),
        btn("✂ Split", "Cut this line in two", () => {
          const ws = cueWords(cue), cut = Math.ceil(ws.length / 2);
          const a = { start: cue.start, end: ws[cut - 1].end, text: ws.slice(0, cut).map((w) => w.text).join(" "), words: ws.slice(0, cut) };
          const b = { start: ws[cut].start, end: cue.end, text: ws.slice(cut).map((w) => w.text).join(" "), words: ws.slice(cut) };
          ed.cues.splice(i, 1, a, b); wordsChanged();
        }, toks.length < 2),
        btn("⤵ Join", "Join with the next line", () => {
          const b = ed.cues[i + 1];
          ed.cues.splice(i, 2, { start: cue.start, end: b.end, text: `${cue.text} ${b.text}`.trim(), words: [...cueWords(cue), ...cueWords(b)] });
          wordsChanged();
        }, i + 1 >= ed.cues.length),
        btn("✕", "Delete this line", () => { ed.cues.splice(i, 1); wordsChanged(); }));
      return row;
    }));
    const base = `/api/jobs/${ed.job.id}/captions/export?start=${ed.start}&end=${ed.end}&name=${encodeURIComponent(`clip ${ed.index + 1}`)}`;
    $("#wExportSrt").href = base + "&format=srt";
    $("#wExportVtt").href = base + "&format=vtt";
  }
  function shiftAll(d) {
    ed.cues.forEach((c) => {
      const s = Math.max(0, c.start + d);
      retime(c, s, s + (c.end - c.start));
    });
    wordsChanged();
  }
  $("#wEarlier").addEventListener("click", () => shiftAll(-0.1));
  $("#wLater").addEventListener("click", () => shiftAll(0.1));
  function loadCues(segments) {
    ed.all = segments || [];
    ed.cues = ed.all.filter(inClip).map((c) => JSON.parse(JSON.stringify(c)));
    ed.cuesDirty = false;
    rebuildWords();
    paintCues();
  }
  $("#wImport").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("for_clip", String($("#wScope").value === "clip"));
    form.append("start", String(ed.start)); form.append("end", String(ed.end));
    try {
      const r = await window.ClipStudio.api(`/api/jobs/${ed.job.id}/captions/import`, { method: "POST", body: form });
      loadCues(r.segments);
      draw();
      window.ClipStudio.toast(`${r.imported} caption lines imported. Press Save changes to put them on the clip.`);
      ed.importedCaptions = true;
    } catch (err) { window.ClipStudio.notice(err.message); }
  });
  onSeg("eFill", (v) => { ed.d.fill = v; });
  onSeg("eCamPos", (v) => {
    ed.d.cam_rect = null; ed.d.game_rect = null; ed.d.extra_rect = null; // the buttons put things back in the standard places
    ed.d.bg_blur = false; ed.d.cam_shape = "frame";
    ed.d.hide_cam = v === "hidden";
    if (v === "hidden" || v === "keep") ed.d.layout = "full";
    else if (v === "corner") ed.d.layout = "corner";
    else { ed.d.layout = "stack"; ed.d.cam_side = v; }
  });
  onSeg("eCapStyle", (v) => {
    const c = ed.d.caption;
    c.style = v;
    if (v === "pop" && c.words > 3) c.words = 2;
    if (v === "karaoke" && c.words < 3) c.words = 4;
    if (v === "twoline" && c.words < 6) c.words = 8;
  });
  onSeg("eCapPos", (v) => { ed.d.caption.y = Number(v); ed.d.caption.x = 0.5; });
  onSeg("eTitleStyle", (v) => { ed.d.title.style = v; });
  const bind = (id, fn) => $(id).addEventListener("input", (e) => { fn(e.target); refresh(); });
  bind("#eSplit", (t) => { ed.d.split = Number(t.value); });
  bind("#eFocus", (t) => { ed.d.focus_x = Number(t.value); });
  bind("#eCapSize", (t) => { ed.d.caption.size = Number(t.value); });
  bind("#eOutline", (t) => { ed.d.caption.outline = Number(t.value); });
  bind("#eTitleSize", (t) => { ed.d.title.size = Number(t.value); });
  bind("#eTitle", (t) => { ed.title = t.value; });
  $("#eUpper").addEventListener("change", (e) => { ed.d.caption.uppercase = e.target.checked; refresh(); });
  $("#eEmoji").addEventListener("change", (e) => { ed.d.caption.emoji = e.target.checked; refresh(); });
  $("#eTitleShow").addEventListener("change", (e) => { ed.d.title.show = e.target.checked; refresh(); });
  $("#eCredit").addEventListener("change", (e) => { ed.d.credit_on = e.target.checked; refresh(); });
  $("#eTemplate").addEventListener("change", (e) => {
    const t = window.ClipStudio.state.templates.find((x) => x.id === e.target.value);
    if (!t) return;
    const td = t.design;
    const keep = { cam_roi: ed.d.cam_roi, game_roi: ed.d.game_roi, extra_roi: ed.d.extra_roi, credit: ed.d.credit, aspect: ed.d.aspect };
    Object.assign(ed.d, { ...td, caption: { ...ed.d.caption, ...(td.caption || {}) }, title: { ...ed.d.title, ...(td.title || {}) }, template: t.id }, keep);
    if (!td.cam_rect) ed.d.cam_rect = null;
    if (!td.game_rect) ed.d.game_rect = null;
    if (!td.extra_rect) ed.d.extra_rect = null;
    if (!td.meme_bar) ed.d.meme_bar = 0.24;
    refresh();
  });
  $$(".tab[data-etab]").forEach((tab) => tab.addEventListener("click", () => {
    $$(".tab[data-etab]").forEach((t) => t.classList.toggle("active", t === tab));
    $$(".etab").forEach((p) => { p.hidden = p.id !== "etab-" + tab.dataset.etab; });
  }));

  /* trim */
  function setupTrim() {
    const dur = ed.job.duration;
    const lo = Math.max(0, ed.clip.start - 30), hi = Math.min(dur, ed.clip.end + 30);
    for (const id of ["#trimStart", "#trimEnd"]) { $(id).min = lo; $(id).max = hi; }
    $("#trimStart").value = ed.start; $("#trimEnd").value = ed.end;
    paintTrim();
  }
  function paintTrim() {
    const lo = Number($("#trimStart").min), hi = Number($("#trimStart").max);
    $("#trimFill").style.left = ((ed.start - lo) / (hi - lo)) * 100 + "%";
    $("#trimFill").style.width = ((ed.end - ed.start) / (hi - lo)) * 100 + "%";
  }
  ["#trimStart", "#trimEnd"].forEach((id) => $(id).addEventListener("input", () => {
    let s = Number($("#trimStart").value), e = Number($("#trimEnd").value);
    if (e - s < 3) { if (id === "#trimStart") s = e - 3; else e = s + 3; }
    if (e - s > 180) { if (id === "#trimStart") e = s + 180; else s = e - 180; }
    ed.start = Math.round(s * 10) / 10; ed.end = Math.round(e * 10) / 10;
    $("#trimStart").value = ed.start; $("#trimEnd").value = ed.end;
    if (ed.video) ed.video.currentTime = id === "#trimStart" ? ed.start : Math.max(ed.start, ed.end - 1.5);
    paintTrim();
    syncControls();
  }));

  /* play */
  $("#playBtn").addEventListener("click", () => {
    const v = ed.video;
    if (!v) return;
    if (ed.playing) { v.pause(); ed.playing = false; $("#playBtn").textContent = "▶"; return; }
    if (v.currentTime < ed.start || v.currentTime >= ed.end) v.currentTime = ed.start;
    v.play().then(() => { ed.playing = true; $("#playBtn").textContent = "❚❚"; }).catch(() => {});
  });

  /* draw the cam box on the source */
  let pick = null;
  $("#drawCam").addEventListener("click", () => {
    const v = ed.video;
    if (!v || !v.videoWidth) return;
    $("#camPicker").hidden = false;
    const c = $("#camCanvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    $(".cam-picker-stage").style.aspectRatio = `${v.videoWidth} / ${v.videoHeight}`;
    c.getContext("2d").drawImage(v, 0, 0);
    pick = ed.d.cam_roi ? [...ed.d.cam_roi] : null;
    paintPick();
  });
  function paintPick() {
    const box = $("#camDraw");
    box.hidden = !pick;
    if (pick) Object.assign(box.style, { left: pick[0] * 100 + "%", top: pick[1] * 100 + "%", width: pick[2] * 100 + "%", height: pick[3] * 100 + "%" });
  }
  $(".cam-picker-stage").addEventListener("pointerdown", (e) => {
    const area = $(".cam-picker-stage"), rect = area.getBoundingClientRect();
    area.setPointerCapture(e.pointerId);
    const x0 = (e.clientX - rect.left) / rect.width, y0 = (e.clientY - rect.top) / rect.height;
    const move = (ev) => {
      const x1 = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
      const y1 = Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height));
      pick = [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)].map((v) => Math.round(v * 10000) / 10000);
      paintPick();
    };
    const up = () => { area.removeEventListener("pointermove", move); area.removeEventListener("pointerup", up); };
    area.addEventListener("pointermove", move);
    area.addEventListener("pointerup", up);
  });
  $("#camCancel").addEventListener("click", () => { $("#camPicker").hidden = true; });
  $("#camDone").addEventListener("click", () => {
    if (!pick || pick[2] < 0.04 || pick[3] < 0.04) { window.ClipStudio.toast("Drag a bigger box over the cam."); return; }
    ed.d.cam_roi = pick;
    if (ed.d.layout === "full") { ed.d.layout = "stack"; ed.d.cam_side = "top"; }
    $("#camPicker").hidden = true;
    refresh();
  });

  /* open / close / save */
  async function open(job, index) {
    const clip = job.clips[index];
    ed.job = job; ed.index = index; ed.clip = clip;
    ed.d = JSON.parse(JSON.stringify(clip.design));
    ed.start = clip.start; ed.end = clip.end; ed.title = clip.title || "";
    $("#editorTitle").textContent = `Edit clip ${index + 1}`;
    framingPanel().reset();
    const tplSel = $("#eTemplate");
    const kind = ed.d.aspect === "16:9" ? "long" : "short";
    const fits = window.ClipStudio.state.templates.filter((t) => (t.kind || (t.design.aspect === "16:9" ? "long" : "short")) === kind);
    tplSel.replaceChildren(...fits.map((t) => new Option(t.mine ? `${t.name} (mine)` : t.name, t.id)));
    const fonts = $("#eFonts");
    fonts.replaceChildren(...window.ClipStudio.state.fonts.map((f) => {
      const b = Object.assign(document.createElement("button"), { type: "button", textContent: f, style: `font-family:"${f}"` });
      b.dataset.font = f;
      b.addEventListener("click", () => { ed.d.caption.font = f; refresh(); });
      return b;
    }));
    const sug = $("#eSuggest");
    sug.replaceChildren(...(clip.suggestions || []).map((s) => Object.assign(document.createElement("button"), {
      type: "button", className: "chip", textContent: s, onclick: () => { ed.title = s; refresh(); },
    })));
    const pc = $("#ePcFont");
    pc.replaceChildren(new Option("Pick a font…", ""), ...(window.ClipStudio.state.pcFonts || []).map((f) => new Option(f, f)));
    $("#eIdeas").replaceChildren();
    $("#eImproveNote").textContent = "Gives you hook questions built from what is said in this clip.";
    $("#eStyleNameRow").hidden = true;
    $("#eApplyAll").checked = false;
    $("#eApplyAll").closest("label").hidden = (job.clips || []).filter((c) => (c.kind || "short") === (clip.kind || "short")).length < 2;
    paintMyStyles();
    $("#editor").hidden = false;
    document.body.style.overflow = "hidden";
    ed.words = [];
    ed.importedCaptions = false;
    let segments = [];
    try { segments = (await window.ClipStudio.api(`/api/jobs/${job.id}/captions`)).segments || []; } catch { /* captions optional */ }
    const st = stage();
    loadCues(segments);
    const v = document.createElement("video");
    v.muted = false; v.playsInline = true; v.preload = "auto"; v.crossOrigin = "anonymous";
    v.src = `/api/jobs/${job.id}/source`;
    v.addEventListener("loadeddata", () => { v.currentTime = ed.start; });
    v.addEventListener("seeked", draw);
    ed.video = v;
    st.setMedia(v);
    setupTrim();
    refresh();
    cancelAnimationFrame(ed.raf);
    ed.raf = requestAnimationFrame(loop);
  }
  function close() {
    cancelAnimationFrame(ed.raf);
    if (ed.video) { ed.video.pause(); ed.video.removeAttribute("src"); ed.video.load(); }
    ed.video = null; ed.playing = false; $("#playBtn").textContent = "▶";
    if (ed.stage) { ed.stage.setMedia(null); ed.stage.setSafe(false); }
    $("#editor").hidden = true;
    document.body.style.overflow = "";
  }
  $("#editorCancel").addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#editor").hidden) { if (!$("#camPicker").hidden) $("#camPicker").hidden = true; else close(); } });
  $("#editorSave").addEventListener("click", async () => {
    const edit = { start: ed.start, end: ed.end, title: ed.title, design: ed.d, framing_all: !!(framing && framing.allClips), apply_all: $("#eApplyAll").checked };
    const job = ed.job, index = ed.index;
    if (ed.cuesDirty) {
      const segments = [...ed.all.filter((c) => !inClip(c)), ...ed.cues.filter((c) => String(c.text).trim() && c.end > c.start)]
        .sort((a, b) => a.start - b.start);
      try {
        await window.ClipStudio.api(`/api/jobs/${job.id}/captions`, { method: "PUT", body: { segments } });
      } catch (err) { window.ClipStudio.notice(`Your caption changes weren't saved: ${err.message}`); return; }
    }
    close();
    await window.ClipStudio.saveClip(job.id, index, edit);
  });

  window.ClipEditor = { open };
})();
