"use strict";
/* Step 2: pick templates for the chosen video type (Short 9:16 / Long 16:9 / Both), drag and resize
   everything on the live preview, set captions, and keep your own templates. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const CS = () => window.ClipStudio;
  const KINDS = {
    short: { aspect: "9:16", label: "📱 Short video · 9:16", rowTitle: "Short templates", hint: "Shorts, Reels, TikTok", first: "streamer_top" },
    long: { aspect: "16:9", label: "🖥 Long video · 16:9", rowTitle: "Long templates", hint: "YouTube", first: "yt_full" },
  };
  const COLORS = ["#FFFFFF", "#FFD233", "#FFE600", "#7CFFB2", "#3BFF5C", "#5EC8FF", "#FF7AB6", "#FF5A3C", "#FF2D2D", "#111111"];
  const PRESETS = {
    pop1: { style: "pop", words: 1 },
    karaoke: { style: "karaoke", words: 4 },
    twoline: { style: "twoline", words: 8 },
    boxed: { style: "boxed", words: 5 },
    punch: { style: "punch", words: 4, highlight: "#FF2D2D", outline: 4 },
    twotone: { style: "twotone", words: 6, highlight: "#FF2D2D", second: "#3BFF5C", outline: 4 },
    off: { style: "none" },
  };
  const POSITION = { top: { short: 0.2, long: 0.16 }, middle: { short: 0.5, long: 0.5 }, bottom: { short: 0.74, long: 0.85 } };
  const SAMPLE_TITLE = "When the cops show up";

  const D = {
    kind: "short", focus: "short", formats: {}, stages: {}, defaults: null, templates: [], fonts: [],
    media: null, credit: "", raf: 0, words: [], loop: 8, onPick: null, extras: null,
  };
  // Cards / My brand / Outro & music stay as the buyer set them when another built-in template is picked,
  // and come back next time (kept in this browser). The buyer's own templates bring their own.
  const EXTRAS = ["card", "brand", "outro", "music", "credit_style"];
  function saveExtras(d) {
    D.extras = {};
    for (const k of EXTRAS) D.extras[k] = clone(d[k]);
    try { localStorage.setItem("clipstudio.extras", JSON.stringify(D.extras)); } catch { /* private mode */ }
  }
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const kindOf = (t) => t.kind || (t.design && t.design.aspect === "16:9" ? "long" : "short");
  const kinds = () => (D.kind === "both" ? ["short", "long"] : [D.kind]);
  const tpl = (id) => D.templates.find((t) => t.id === id);

  function designFrom(t, kind) {
    const base = clone(D.defaults);
    const td = clone(t.design || {});
    const d = { ...base, ...td, caption: { ...base.caption, ...(td.caption || {}) }, title: { ...base.title, ...(td.title || {}) } };
    for (const k of ["card", "brand", "outro", "music"]) d[k] = { ...clone(base[k] || {}), ...(td[k] || {}) };
    if (!t.mine && D.extras) for (const k of EXTRAS) if (D.extras[k] !== undefined) d[k] = clone(D.extras[k]);
    d.template = t.id;
    d.aspect = KINDS[kind].aspect;
    if (!td.keep_boxes) { d.cam_roi = null; d.game_roi = null; d.extra_roi = null; } // boxes belong to one video
    d.credit = "";
    return d;
  }
  function pick(kind, t, keepCaptions) {
    const old = D.formats[kind];
    const d = designFrom(t, kind);
    if (keepCaptions && old) d.caption = { ...d.caption };
    D.formats[kind] = { template: t.id, design: d, dirty: false };
    if (t.mine) saveExtras(d);
    if (D.stages[kind]) D.stages[kind].setDesign(d);
    if (D.onPick && D.kind !== "both") D.onPick(t);
  }

  /* ---------- template cards ---------- */
  function mini(t, kind) {
    const d = designFrom(t, kind);
    const box = document.createElement("div");
    box.className = "mini " + (kind === "long" ? "tv" : "phone");
    const layout = d.layout === "split" && kind === "long" ? "full" : d.layout;
    const add = (cls, r, extra = "") => {
      const i = document.createElement("i");
      i.className = cls;
      i.style.cssText = `left:${r[0] * 100}%;top:${r[1] * 100}%;width:${r[2] * 100}%;height:${r[3] * 100}%;${extra}`;
      box.append(i);
    };
    if (layout === "meme") box.style.background = "#fff";
    const view = { ...d, cam_roi: layout === "stack" || layout === "corner" ? [0, 0.6, 0.25, 0.35] : null, game_roi: [0, 0, 1, 1] };
    if (layout === "split") { add("game", [0, 0, 1, 0.5]); add("game alt", [0, 0.5, 1, 0.5]); }
    else {
      add("game", window.ClipStage.gameArea(view));
      const cr = window.ClipStage.camOutputRect(view);
      if (cr) add("cam" + (layout === "corner" ? " framed" : ""), cr);
    }
    const c = d.caption;
    if (c.style !== "none") {
      const w = c.style === "pop" ? 0.34 : c.style === "twoline" ? 0.6 : 0.56;
      const h = (c.style === "twoline" ? 0.09 : 0.05) * (kind === "long" ? 1.6 : 1);
      add("cap" + (c.style === "boxed" ? " boxed" : ""), [c.x - w / 2, c.y - h / 2, w, h], `background:${c.style === "boxed" ? "rgba(0,0,0,.55)" : c.color === "#FFFFFF" ? "#fff" : c.color}`);
    }
    if (d.title.show !== false) {
      const look = { yellow: "#FFD233", red_box: "#E5322D", white_box: "#fff", outline: "#fff", meme: "#111" }[d.title.style] || "#fff";
      add("ttl", [d.title.x - 0.28, (layout === "meme" ? d.meme_bar / 2 : d.title.y) - 0.025, 0.56, kind === "long" ? 0.07 : 0.04], `background:${look}`);
    }
    return box;
  }
  function card(t, kind) {
    const f = D.formats[kind];
    const on = f && f.template === t.id;
    const node = document.createElement("div");
    node.className = "tpl" + (on ? " on" : "") + (t.mine ? " mine" : "");
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.dataset.id = t.id;
    node.title = t.mine ? "Your template" : t.blurb || "";
    const name = document.createElement("strong");
    name.textContent = t.name;
    const blurb = document.createElement("small");
    blurb.textContent = t.mine ? "Your template" : t.blurb;
    node.append(mini(t, kind), name, blurb);
    if (t.mine) {
      const tools = document.createElement("div");
      tools.className = "tpl-tools";
      const rename = Object.assign(document.createElement("button"), { type: "button", className: "icon", title: "Rename", textContent: "✎" });
      const del = Object.assign(document.createElement("button"), { type: "button", className: "icon", title: "Delete", textContent: "🗑" });
      rename.addEventListener("click", (e) => { e.stopPropagation(); renameInline(node, t); });
      del.addEventListener("click", (e) => { e.stopPropagation(); confirmDelete(node, t); });
      tools.append(rename, del);
      node.append(tools);
    }
    const choose = () => {
      if (node.querySelector("input")) return;
      pick(kind, t);
      D.focus = kind;
      renderRows();
      paintToolbar(kind);
      syncCaptions();
    };
    node.addEventListener("click", choose);
    node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } });
    return node;
  }
  function renderRows() {
    const host = $("#tplRows");
    host.replaceChildren(...kinds().map((kind) => {
      const row = document.createElement("div");
      row.className = "tpl-row";
      const head = document.createElement("div");
      head.className = "tpl-row-head";
      head.innerHTML = `<strong>${KINDS[kind].rowTitle}</strong><span class="muted">${KINDS[kind].aspect} · ${KINDS[kind].hint}</span>`;
      const list = document.createElement("div");
      list.className = "templates " + kind;
      const mine = D.templates.filter((t) => t.mine && kindOf(t) === kind);
      list.append(...D.templates.filter((t) => !t.mine && kindOf(t) === kind).map((t) => card(t, kind)));
      if (mine.length) {
        const sep = document.createElement("div");
        sep.className = "tpl-sep";
        sep.textContent = "My templates";
        list.append(sep, ...mine.map((t) => card(t, kind)));
      }
      row.append(head, list);
      return row;
    }));
  }
  function renameInline(node, t) {
    const name = node.querySelector("strong");
    const input = Object.assign(document.createElement("input"), { type: "text", value: t.name, maxLength: 40, className: "tpl-rename", "aria-label": "Template name" });
    name.replaceWith(input);
    input.focus(); input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const value = input.value.trim();
      if (save && value && value !== t.name) {
        try {
          const item = await CS().api(`/api/my-templates/${t.id}`, { method: "PUT", body: { name: value } });
          Object.assign(t, item);
          CS().toast("Renamed");
        } catch (err) { CS().notice(err.message); }
      }
      renderRows();
      kinds().forEach(paintToolbar);
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("click", (e) => e.stopPropagation());
  }
  function confirmDelete(node, t) {
    const bar = document.createElement("div");
    bar.className = "tpl-confirm";
    bar.innerHTML = `<span>Delete “${t.name.replace(/[<>&]/g, "")}”?</span>`;
    const yes = Object.assign(document.createElement("button"), { type: "button", className: "danger small", textContent: "Delete" });
    const no = Object.assign(document.createElement("button"), { type: "button", className: "ghost small", textContent: "Keep" });
    bar.append(yes, no);
    bar.addEventListener("click", (e) => e.stopPropagation());
    no.addEventListener("click", () => bar.remove());
    yes.addEventListener("click", async () => {
      try {
        await CS().api(`/api/my-templates/${t.id}`, { method: "DELETE" });
        const i = D.templates.indexOf(t);
        if (i >= 0) D.templates.splice(i, 1);
        for (const kind of Object.keys(D.formats)) {
          if (D.formats[kind].template === t.id) pick(kind, tpl(t.base) && kindOf(tpl(t.base)) === kind ? tpl(t.base) : tpl(KINDS[kind].first));
        }
        renderRows();
        kinds().forEach(paintToolbar);
        CS().toast("Template deleted");
      } catch (err) { CS().notice(err.message); }
    });
    node.append(bar);
  }

  /* ---------- previews ---------- */
  function renderStages() {
    const host = $("#stages");
    for (const st of Object.values(D.stages)) st.destroy();
    D.stages = {};
    host.classList.toggle("both", D.kind === "both");
    host.replaceChildren(...kinds().map((kind) => {
      const box = document.createElement("div");
      box.className = "stage-card " + kind;
      box.dataset.kind = kind;
      box.innerHTML = `
        <div class="stage-card-head"><strong>${KINDS[kind].label}</strong><span class="muted stage-state"></span></div>
        <div class="stage-host"></div>
        <div class="stage-tools">
          <div class="seg small fill-seg" role="group" aria-label="Empty space">
            <button type="button" data-fill="zoom">Zoom in</button><button type="button" data-fill="blur">Blurred</button><button type="button" data-fill="bars">Bars</button>
          </div>
          <div class="seg small cam-seg" role="group" aria-label="Streamer cam">
            <span class="seg-label">Streamer cam</span><button type="button" data-cam="keep" title="Leave it where it is in the stream">Keep</button><button type="button" data-cam="hide" title="Blur the streamer's cam out of the picture">Remove</button><button type="button" data-cam="move" title="Put the cam in a box you can drag">Move it</button>
          </div>
          <button type="button" class="ghost small" data-act="reset" title="Back to how the template looked">↺ Reset to default</button>
          <button type="button" class="ghost small" data-act="update" hidden></button>
          <button type="button" class="primary small" data-act="saveas">💾 Save as my template</button>
        </div>
        <form class="save-as" hidden>
          <input type="text" maxlength="40" placeholder="Name, e.g. My GTA style" aria-label="Template name">
          <button class="primary small" type="submit">Save</button>
          <button class="ghost small" type="button" data-act="cancel">Cancel</button>
        </form>`;
      const st = window.ClipStage.create($(".stage-host", box), {
        placeholderCam: true,
        onChange: (d, part, done) => {
          D.formats[kind].dirty = true;
          D.focus = kind;
          if (part === "caption") syncCaptions();
          if (done) { paintToolbar(kind); if (D.framing) D.framing.sync(); }
        },
      });
      st.creditFallback = D.credit || "streamer";
      st.setTitle(SAMPLE_TITLE);
      st.setWords(D.words);
      st.setMedia(D.media);
      st.setDesign(D.formats[kind].design);
      D.stages[kind] = st;
      box.addEventListener("pointerdown", () => { if (D.focus !== kind) { D.focus = kind; syncCaptions(); } });
      $$(".fill-seg button", box).forEach((b) => b.addEventListener("click", () => {
        D.formats[kind].design.fill = b.dataset.fill;
        D.formats[kind].dirty = true;
        st.paint();
        paintToolbar(kind);
      }));
      $$(".cam-seg button", box).forEach((b) => b.addEventListener("click", () => {
        const d = D.formats[kind].design, mode = b.dataset.cam;
        const base = (tpl(D.formats[kind].template) || {}).design || {};
        d.hide_cam = mode !== "keep";
        if (mode === "move") {
          d.layout = "corner";
          d.cam_rect = d.cam_rect || (kind === "long" ? [0.69, 0.05, 0.28, 0.3] : [0.05, 0.05, 0.44, 0.2]);
        } else {
          d.layout = base.layout && base.layout !== "corner" && base.layout !== "stack" ? base.layout : "full";
          d.cam_rect = null;
        }
        D.formats[kind].dirty = true;
        st.setDesign(d);
        paintToolbar(kind);
        CS().toast(mode === "hide" ? "The streamer's cam will be blurred out" : mode === "move" ? "Drag the cam box where you want it" : "The cam stays as it is in the stream");
      }));
      $('[data-act="reset"]', box).addEventListener("click", () => {
        const t = tpl(D.formats[kind].template) || tpl(KINDS[kind].first);
        pick(kind, t);
        paintToolbar(kind);
        syncCaptions();
        CS().toast("Back to the template's look");
      });
      $('[data-act="update"]', box).addEventListener("click", () => updateMine(kind));
      const form = $(".save-as", box);
      $('[data-act="saveas"]', box).addEventListener("click", () => {
        form.hidden = false;
        const t = tpl(D.formats[kind].template);
        const input = $("input", form);
        input.value = t && t.mine ? t.name + " 2" : "";
        input.focus();
      });
      $('[data-act="cancel"]', form).addEventListener("click", () => { form.hidden = true; });
      form.addEventListener("submit", (e) => { e.preventDefault(); saveAs(kind, $("input", form).value, form); });
      return box;
    }));
    kinds().forEach(paintToolbar);
  }
  function paintToolbar(kind) {
    const box = $(`.stage-card[data-kind="${kind}"]`);
    if (!box) return;
    const f = D.formats[kind], t = tpl(f.template);
    $$(".fill-seg button", box).forEach((b) => b.classList.toggle("on", b.dataset.fill === f.design.fill));
    const cl = f.design.layout, cam = window.ClipStage.CAM_LAYOUTS.includes(cl) ? "move" : f.design.hide_cam ? "hide" : "keep";
    $$(".cam-seg button", box).forEach((b) => b.classList.toggle("on", b.dataset.cam === cam));
    const upd = $('[data-act="update"]', box);
    upd.hidden = !(t && t.mine && f.dirty);
    if (t && t.mine) upd.textContent = `✔ Save changes to “${t.name}”`;
    $(".stage-state", box).textContent = t ? (f.dirty ? `${t.name} · changed` : t.name) : "";
  }
  function clean(d) {
    const out = clone(d);
    if (!(out.keep_boxes && out.cam_roi)) { delete out.cam_roi; delete out.game_roi; delete out.extra_roi; }
    delete out.credit;
    if (out.title) delete out.title.text;
    return out;
  }
  async function saveAs(kind, name, form) {
    name = String(name || "").trim();
    if (!name) { CS().toast("Give your template a name."); return; }
    const f = D.formats[kind];
    try {
      const item = await CS().api("/api/my-templates", {
        method: "POST",
        body: { name, design: clean(f.design), base: (tpl(f.template) || {}).mine ? (tpl(f.template).base || "") : f.template, length: CS().state.recipe.length },
      });
      D.templates.push(item);
      f.template = item.id; f.design.template = item.id; f.dirty = false;
      form.hidden = true;
      renderRows();
      paintToolbar(kind);
      CS().toast(`Saved “${item.name}”. It's in My templates.`);
    } catch (err) { CS().notice(err.message); }
  }
  async function updateMine(kind) {
    const f = D.formats[kind], t = tpl(f.template);
    if (!t || !t.mine) return;
    try {
      const item = await CS().api(`/api/my-templates/${t.id}`, { method: "PUT", body: { design: clean(f.design) } });
      Object.assign(t, item);
      f.dirty = false;
      renderRows();
      paintToolbar(kind);
      CS().toast(`Saved changes to “${t.name}”`);
    } catch (err) { CS().notice(err.message); }
  }

  /* ---------- captions panel ---------- */
  function cap() { return D.formats[D.focus] ? D.formats[D.focus].design.caption : D.defaults.caption; }
  function capSet(patch, perKind) {
    for (const kind of kinds()) {
      const c = D.formats[kind].design.caption;
      Object.assign(c, typeof perKind === "function" ? perKind(kind) : patch);
      D.formats[kind].dirty = true;
      D.stages[kind] && D.stages[kind].paint();
      paintToolbar(kind);
    }
    syncCaptions();
  }
  function presetOf(c) {
    if (c.style === "none") return "off";
    if (c.style === "pop") return "pop1";
    if (c.style === "classic") return "twoline";
    return c.style;
  }
  function syncCaptions() {
    const c = cap();
    if (!c) return;
    const preset = presetOf(c);
    $$("#capPresets button").forEach((b) => b.classList.toggle("on", b.dataset.preset === preset));
    const off = c.style === "none";
    $("#capFields").classList.toggle("dim", off);
    $("#capWordsRow").hidden = !["pop", "karaoke", "boxed", "punch", "twotone"].includes(c.style);
    $("#capWords").textContent = c.words;
    $("#capAnimRow").hidden = ["punch", "twotone", "none"].includes(c.style);
    $$("#capAnim button").forEach((b) => b.classList.toggle("on", b.dataset.v === (c.animation || "auto")));
    $$("#capFonts button").forEach((b) => b.classList.toggle("on", b.dataset.font === c.font));
    $("#capSize").value = c.size; $("#capSizeVal").textContent = c.size;
    $$("#capColor button").forEach((b) => b.classList.toggle("on", b.dataset.c === c.color));
    $$("#capHi button").forEach((b) => b.classList.toggle("on", b.dataset.c === c.highlight));
    $("#capHiRow").hidden = !["pop", "karaoke", "punch", "twotone"].includes(c.style);
    $("#capHiLabel").textContent = ["punch", "twotone"].includes(c.style) ? "Key word colour" : "Spoken word colour";
    $("#capSecondRow").hidden = c.style !== "twotone";
    $$("#capSecond button").forEach((b) => b.classList.toggle("on", b.dataset.c === c.second));
    syncEffects();
    $("#capOutline").value = c.outline; $("#capOutlineVal").textContent = c.outline;
    const pos = c.y < 0.33 ? "top" : c.y < 0.62 ? "middle" : "bottom";
    $$("#capPos button").forEach((b) => b.classList.toggle("on", b.dataset.pos === pos));
    $("#capUpper").checked = !!c.uppercase;
    $("#capEmoji").checked = !!c.emoji;
    $("#capFor").textContent = D.kind === "both" ? "Applies to both videos" : "";
    syncFraming();
    if (window.ClipOverlays) window.ClipOverlays.sync();
  }
  /* Cards / My brand / Outro & music tabs (overlays.js) change every video type at once, like captions. */
  function overlaySet(fn, quiet) {
    for (const kind of kinds()) {
      fn(D.formats[kind].design, kind);
      D.formats[kind].dirty = true;
      D.stages[kind] && D.stages[kind].paint();
      paintToolbar(kind);
    }
    saveExtras(design());
    if (!quiet && window.ClipOverlays) window.ClipOverlays.sync();
  }
  function setupTabs() {
    $$("[data-dtab]").forEach((tab) => tab.addEventListener("click", () => {
      $$("[data-dtab]").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".dtab").forEach((panel) => { panel.hidden = panel.id !== "dtab-" + tab.dataset.dtab; });
      if (window.ClipOverlays) window.ClipOverlays.show(tab.dataset.dtab);
    }));
  }

  /* ---------- "Set it manually" (layouts + boxes) for the focused video type ---------- */
  function syncFraming() {
    if (!D.framing && $("#dFraming") && window.ClipFraming) {
      D.framing = window.ClipFraming.mount($("#dFraming"), {
        context: "template",
        get: () => D.formats[D.focus] && D.formats[D.focus].design,
        changed: () => {
          const kind = D.focus;
          D.formats[kind].dirty = true;
          if (D.stages[kind]) D.stages[kind].setDesign(D.formats[kind].design);
          paintToolbar(kind);
        },
        media: () => D.media,
        stage: () => D.stages[D.focus],
        layouts: () => CS().state.layouts || [],
      });
    }
    if (D.framing) {
      D.framing.sync();
      const label = $("#dFramingFor");
      if (label) label.textContent = D.kind === "both" ? `For the ${D.focus === "long" ? "16:9" : "Short"} video. Click the other preview to change that one.` : "";
    }
  }
  function swatches(id, key) {
    const box = $(id);
    box.replaceChildren(...COLORS.map((c) => {
      const b = Object.assign(document.createElement("button"), { type: "button", title: c, ariaLabel: `Colour ${c}` });
      b.style.background = c;
      b.dataset.c = c;
      b.addEventListener("click", () => capSet({ [key]: c }));
      return b;
    }));
    const any = Object.assign(document.createElement("input"), { type: "color", title: "Any colour" });
    any.addEventListener("input", () => capSet({ [key]: any.value.toUpperCase() }));
    box.append(any);
  }
  function setupCaptions() {
    $$("#capPresets button").forEach((b) => b.addEventListener("click", () => {
      const p = PRESETS[b.dataset.preset];
      capSet(p, (kind) => {
        const c = D.formats[kind].design.caption;
        const patch = { ...p };
        // Two lines read best a bit smaller; one-word pop a bit bigger.
        if (p.style === "twoline" && c.size > 90) patch.size = kind === "long" ? 60 : 72;
        if (p.style === "pop" && p.words === 1 && c.size < 80) patch.size = kind === "long" ? 92 : 104;
        return patch;
      });
    }));
    $("#capWordsMinus").addEventListener("click", () => capSet({ words: Math.max(1, cap().words - 1) }));
    $("#capWordsPlus").addEventListener("click", () => capSet({ words: Math.min(8, cap().words + 1) }));
    $("#capFonts").replaceChildren(...D.fonts.map((f) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.font = f;
      b.innerHTML = `<span class="font-sample" style="font-family:'${f}'">Insane clip!</span><small>${f}</small>`;
      b.addEventListener("click", () => capSet({ font: f }));
      return b;
    }));
    $("#capSize").addEventListener("input", (e) => capSet({ size: Number(e.target.value) }));
    $$("#capAnim button").forEach((b) => b.addEventListener("click", () => capSet({ animation: b.dataset.v })));
    $("#capOutline").addEventListener("input", (e) => capSet({ outline: Number(e.target.value) }));
    swatches("#capColor", "color");
    swatches("#capHi", "highlight");
    swatches("#capSecond", "second");
    setupEffects();
    $$("#capPos button").forEach((b) => b.addEventListener("click", () => capSet(null, (kind) => ({ x: 0.5, y: POSITION[b.dataset.pos][kind] }))));
    $("#capUpper").addEventListener("change", (e) => capSet({ uppercase: e.target.checked }));
    $("#capEmoji").addEventListener("change", (e) => capSet({ emoji: e.target.checked }));
  }

  /* ---------- title, @name, shout effect (whole design, every video type) ---------- */
  function design() { return D.formats[D.focus] ? D.formats[D.focus].design : D.defaults; }
  function designSet(fn) {
    for (const kind of kinds()) {
      fn(D.formats[kind].design, kind);
      D.formats[kind].dirty = true;
      D.stages[kind] && D.stages[kind].paint();
      paintToolbar(kind);
    }
    syncEffects();
  }
  function syncEffects() {
    const d = design();
    if (!d || !d.title) return;
    const t = d.title.show === false ? "off" : Number(d.title.seconds) === 0 ? "0" : "3";
    $$("#fxTitle button").forEach((b) => b.classList.toggle("on", b.dataset.v === t));
    $("#fxCredit").checked = !!d.credit_on;
    $("#fxShout").checked = !!d.shout;
    $("#fxSfx").checked = d.sfx !== false;
    $("#fxSfx").disabled = !d.shout;
  }
  function setupEffects() {
    $$("#fxTitle button").forEach((b) => b.addEventListener("click", () => designSet((d) => {
      if (b.dataset.v === "off") d.title.show = false;
      else { d.title.show = true; d.title.seconds = Number(b.dataset.v); }
    })));
    $("#fxCredit").addEventListener("change", (e) => designSet((d) => { d.credit_on = e.target.checked; }));
    $("#fxShout").addEventListener("change", (e) => designSet((d) => { d.shout = e.target.checked; }));
    $("#fxSfx").addEventListener("change", (e) => designSet((d) => { d.sfx = e.target.checked; }));
  }

  /* ---------- background picture + sample captions ---------- */
  function setSource(src) {
    D.media = null;
    D.credit = "";
    if (src && src.kind === "url" && src.info) {
      D.credit = src.info.channel || "";
      if (src.info.thumbnail) {
        const img = new Image();
        img.referrerPolicy = "no-referrer";
        img.onload = () => { D.media = img; Object.values(D.stages).forEach((s) => s.setMedia(img)); };
        img.src = src.info.thumbnail;
      }
    } else if (src && src.kind === "file" && src.file) {
      const v = document.createElement("video");
      v.muted = true; v.preload = "auto"; v.playsInline = true;
      v.src = URL.createObjectURL(src.file);
      v.addEventListener("loadedmetadata", () => { v.currentTime = Math.min(30, (v.duration || 10) * 0.2); });
      v.addEventListener("seeked", () => { D.media = v; Object.values(D.stages).forEach((s) => s.setMedia(v)); }, { once: true });
    }
    Object.values(D.stages).forEach((s) => { s.creditFallback = D.credit || "streamer"; s.setMedia(D.media); });
  }
  function animate() {
    cancelAnimationFrame(D.raf);
    const tick = () => {
      if ($("#step2").hidden) return;
      const t = (performance.now() / 1000) % D.loop;
      for (const st of Object.values(D.stages)) st.tick(t);
      D.raf = requestAnimationFrame(tick);
    };
    D.raf = requestAnimationFrame(tick);
  }

  /* ---------- video type ---------- */
  function setKind(kind, quiet) {
    if (!KINDS[kind] && kind !== "both") kind = "short";
    D.kind = kind;
    if (!kinds().includes(D.focus)) D.focus = kinds()[0];
    try { localStorage.setItem("clipstudio.kind", kind); } catch { /* private mode */ }
    $$("[data-kind-pick]").forEach((b) => {
      const on = b.dataset.kindPick === kind;
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", String(on));
    });
    if (quiet || !D.defaults) return;
    renderRows();
    renderStages();
    syncCaptions();
    if (D.kind !== "both" && D.onPick) D.onPick(tpl(D.formats[D.kind].template) || {});
  }

  function init(data) {
    D.defaults = data.defaults;
    D.templates = data.templates;
    D.fonts = data.fonts || [];
    window.ClipStage.setEmoji(data.emoji);
    D.words = window.ClipStage.sampleWords();
    D.loop = D.words[D.words.length - 1].end + 1.2;
    try { D.extras = JSON.parse(localStorage.getItem("clipstudio.extras") || "null"); } catch { D.extras = null; }
    if (D.extras) {
      // drop anything the engine no longer accepts (keys only; values are checked when clips are made)
      for (const k of Object.keys(D.extras)) if (!EXTRAS.includes(k)) delete D.extras[k];
    }
    for (const kind of Object.keys(KINDS)) pick(kind, tpl(KINDS[kind].first) || D.templates.find((t) => kindOf(t) === kind));
    setupCaptions();
    setupTabs();
    if (window.ClipOverlays) window.ClipOverlays.init();
    let saved = "short";
    try { saved = localStorage.getItem("clipstudio.kind") || "short"; } catch { /* private mode */ }
    setKind(saved, true);
  }
  function enter(source) {
    setSource(source);
    renderRows();
    renderStages();
    syncCaptions();
    animate();
  }
  function payload() {
    const list = kinds().map((kind) => ({ kind, template: D.formats[kind].template, design: clean(D.formats[kind].design) }));
    return {
      kind: D.kind,
      template: list[0].template,
      aspect: KINDS[list[0].kind].aspect,
      design: list[0].design,
      formats: list,
    };
  }
  function captionsOn() { return kinds().some((k) => D.formats[k].design.caption.style !== "none"); }
  function summary(len) {
    const names = kinds().map((k) => `“${(tpl(D.formats[k].template) || {}).name || "your template"}”`).join(" and ");
    if (D.kind === "both") return `turn each one into a ${len}-second Short (9:16) and a ${len}-second 16:9 video, with ${names}`;
    if (D.kind === "long") return `make ${len}-second 16:9 videos for YouTube with ${names}`;
    return `make ${len}-second Shorts (9:16) with ${names}`;
  }

  function ensureCaptions() { if (!captionsOn()) capSet({ style: "pop" }); }
  window.ClipDesigner = {
    init, enter, ensureCaptions, setKind, payload, summary, captionsOn,
    current: () => design(), update: overlaySet, fonts: () => D.fonts, credit: () => D.credit,
    get kind() { return D.kind; }, set onPick(fn) { D.onPick = fn; },
  };
})();
