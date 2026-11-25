"use strict";
/* Clip editor: live preview (stage.js, same maths as the renderer) + trim, layout, captions, title. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const COLORS = ["#FFFFFF", "#FFD233", "#7CFFB2", "#5EC8FF", "#FF7AB6", "#FF5A3C", "#111111"];
  const S = () => window.ClipStage;

  const ed = { job: null, index: 0, clip: null, d: null, start: 0, end: 0, title: "", words: [], video: null, raf: 0, playing: false, stage: null };

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
    $("#eUpper").checked = c.uppercase;
    $("#eEmoji").checked = !!c.emoji;
    if (document.activeElement !== $("#eTitle")) $("#eTitle").value = ed.title;
    seg("eTitleStyle", t.style);
    $("#eTitleSize").value = t.size; $("#eTitleSizeVal").textContent = t.size;
    seg("eTitleTime", t.seconds);
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
  onSeg("eTitleTime", (v) => { ed.d.title.seconds = Number(v); });
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
    $("#editor").hidden = false;
    document.body.style.overflow = "hidden";
    ed.words = [];
    try {
      const data = await window.ClipStudio.api(`/api/jobs/${job.id}/captions`);
      for (const seg of data.segments || []) {
        if (seg.words && seg.words.length) seg.words.forEach((w) => ed.words.push({ start: w.start, end: w.end, text: w.text }));
        else {
          const toks = String(seg.text || "").split(/\s+/).filter(Boolean), step = (seg.end - seg.start) / Math.max(1, toks.length);
          toks.forEach((tk, i) => ed.words.push({ start: seg.start + i * step, end: seg.start + (i + 1) * step, text: tk }));
        }
      }
    } catch { /* captions optional */ }
    const st = stage();
    st.setWords(ed.words);
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
    const edit = { start: ed.start, end: ed.end, title: ed.title, design: ed.d, framing_all: !!(framing && framing.allClips) };
    const job = ed.job, index = ed.index;
    close();
    await window.ClipStudio.saveClip(job.id, index, edit);
  });

  window.ClipEditor = { open };
})();
