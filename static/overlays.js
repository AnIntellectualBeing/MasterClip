"use strict";
/* Design step tabs "Cards", "My brand" and "Outro & music". Every control has a short label and a
   one-line hint; the card, handles bar and logo also drag on the live preview (stage.js).
   Values live in the design (design.py: CardStyle, BrandStyle, OutroStyle, MusicStyle). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const CS = () => window.ClipStudio;
  const DZ = () => window.ClipDesigner;
  const CARDS = [
    { id: "none", name: "No card", tag: "all" },
    { id: "x_post", name: "X post", tag: "x" },
    { id: "ig_comment", name: "Instagram comment", tag: "instagram" },
    { id: "ig_question", name: "Instagram question", tag: "instagram" },
    { id: "ig_poll", name: "Instagram poll", tag: "instagram" },
    { id: "yt_comment", name: "YouTube comment", tag: "youtube" },
    { id: "tiktok_hook", name: "TikTok hook", tag: "tiktok" },
    { id: "news", name: "Breaking news", tag: "news" },
  ];
  const FILTERS = [["all", "All"], ["x", "X"], ["instagram", "Instagram"], ["youtube", "YouTube"], ["tiktok", "TikTok"], ["news", "News"]];
  const PLATFORMS = [["youtube", "YouTube"], ["twitch", "Twitch"], ["kick", "Kick"], ["instagram", "Instagram"], ["tiktok", "TikTok"], ["x", "X"]];
  const SAMPLE = "When the cops show up";
  const O = { filter: "all", ready: false, thumbs: {}, thumbKey: "", names: {}, outroKey: "", outroUrl: "", playing: 0 };
  const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* ---------- small helpers ---------- */
  const get = (d, path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), d);
  function put(d, path, value) {
    const keys = path.split(".");
    const last = keys.pop();
    const box = keys.reduce((o, k) => (o[k] = o[k] || {}), d);
    box[last] = value;
  }
  const cur = () => (DZ() && DZ().current()) || null;
  const set = (path, value, quiet) => DZ().update((d) => put(d, path, value), quiet);
  function names() {
    try { O.names = JSON.parse(localStorage.getItem("clipstudio.assetNames") || "{}"); } catch { O.names = {}; }
  }
  function remember(id, name) {
    O.names[id] = name;
    try { localStorage.setItem("clipstudio.assetNames", JSON.stringify(O.names)); } catch { /* private mode */ }
  }
  const slider = (path, label, min, max, step, hint, fmt = "") => `
    <label class="field"><span>${label} <b data-out="${path}" data-fmt="${fmt}"></b></span>
      <input type="range" data-k="${path}" min="${min}" max="${max}" step="${step}">
      ${hint ? `<small class="hint">${hint}</small>` : ""}</label>`;
  const seg = (path, items, num) => `<div class="seg small" data-seg="${path}"${num ? " data-num" : ""}>${items.map(([v, l]) => `<button type="button" data-v="${v}">${l}</button>`).join("")}</div>`;
  const upload = (path, accept, label) => `
    <div class="ov-file"><button type="button" class="ghost small" data-upload="${path}" data-accept="${accept}">${label}</button>
      <span class="name" data-name="${path}"></span>
      <button type="button" class="ghost small" data-clear="${path}" hidden>Remove</button></div>`;
  const color = (path, label) => `<label>${label} <input type="color" data-k="${path}"></label>`;

  /* ---------- the three panels ---------- */
  function cardsHtml() {
    return `
      <p class="hint">Put a social post on your clips. Leave the text empty and each clip's title is used.</p>
      <div class="seg small wrap" id="ovFilter">${FILTERS.map(([v, l]) => `<button type="button" data-f="${v}">${l}</button>`).join("")}</div>
      <div class="ov-gallery" id="ovGallery"></div>
      <div id="ovCardFields" class="ov-stack">
        <section class="ov-sec">
          <h3>Words</h3>
          <div data-when="card.kind:x_post,ig_comment,ig_question,ig_poll,yt_comment,tiktok_hook">${seg("card.theme", [["light", "☀ Light"], ["dark", "🌙 Dark"]])}</div>
          <label class="field"><span data-label-for-kind>Text</span>
            <textarea data-k="card.text" maxlength="280" placeholder="Empty = the clip's title"></textarea></label>
          <div data-when="card.kind:x_post,ig_comment,yt_comment" class="ov-stack">
            <div class="ov-row"><label class="field grow"><span>Name</span><input type="text" data-k="card.name" maxlength="40" data-ph="channel"></label>
              <label class="field grow"><span>@handle</span><input type="text" data-k="card.handle" maxlength="40" data-ph="handle"></label></div>
            <label class="switch"><input type="checkbox" data-k="card.verified"><span></span> Blue tick</label>
          </div>
          <div data-when="card.kind:x_post,ig_comment,ig_question,yt_comment" class="field"><span>Picture</span>
            ${upload("card.avatar", "image/*", "📷 Pick a picture")}<small class="hint">No picture = the first letter of the name in a circle.</small></div>
          <div data-when="card.kind:ig_poll" class="field"><span>Answers</span><div id="ovPoll" class="ov-stack"></div>
            <button type="button" class="ghost small" id="ovPollAdd">+ Add an answer</button>
            <small class="hint">2 to 4 answers. The % is what the bar shows.</small></div>
          <label data-when="card.kind:news" class="field"><span>Moving line at the bottom</span>
            <input type="text" data-k="card.ticker" maxlength="200" placeholder="Empty = the title + follow line"></label>
        </section>
        <section class="ov-sec" data-when="card.kind:x_post,ig_comment,ig_question,ig_poll,yt_comment,tiktok_hook">
          <h3>Position &amp; size</h3>
          <p class="hint">Or drag the card on the preview. Drag its corner to resize.</p>
          ${slider("card.size", "Card size", 0.35, 1, 0.01, "", "pct")}
          ${slider("card.y", "Up / down", 0, 1, 0.01, "", "pct")}
          ${slider("card.x", "Left / right", 0, 1, 0.01, "", "pct")}
        </section>
        <section class="ov-sec">
          <h3>Show the card</h3>
          ${seg("card.seconds", [["0", "Whole clip"], ["3", "First 3 s"], ["5", "First 5 s"], ["8", "First 8 s"]], true)}
        </section>
      </div>`;
  }
  function brandHtml() {
    return `
      <p class="hint">Your names, logo and frame go on every clip. They stay set for next time.</p>
      <section class="ov-sec">
        <h3>Social handles bar</h3>
        <div id="ovHandles" class="ov-stack"></div>
        <button type="button" class="ghost small" id="ovHandleAdd">+ Add a handle</button>
        <div data-when="brand.handles:some" class="ov-stack">
          ${slider("brand.handles_size", "Size", 16, 90, 1)}
          ${slider("brand.handles_y", "Up / down", 0, 1, 0.01, "Or drag the bar on the preview.", "pct")}
        </div>
      </section>
      <section class="ov-sec">
        <h3>Logo</h3>
        ${upload("brand.logo", "image/png,image/jpeg,image/webp", "🖼 Pick your logo")}
        <small class="hint">A PNG with a see-through background looks best.</small>
        <div data-when="brand.logo:set" class="ov-stack">
          ${seg("brand.logo_corner", [["tl", "↖ Top left"], ["tr", "↗ Top right"], ["bl", "↙ Bottom left"], ["br", "↘ Bottom right"]])}
          ${slider("brand.logo_size", "Size", 0.05, 0.5, 0.01, "", "pct")}
          ${slider("brand.logo_opacity", "Strength", 0.1, 1, 0.05, "Lower = more see-through.", "pct")}
        </div>
      </section>
      <section class="ov-sec">
        <h3>Credit line</h3>
        ${seg("credit_style", [["at", "@name"], ["clip", "Clip: name"]])}
        <small class="hint">The streamer's name at the bottom. Switch it on or off in Captions → On the video.</small>
      </section>
      <section class="ov-sec">
        <h3>Background card</h3>
        <small class="hint">Shrinks the video into a rounded card with a background around it.</small>
        ${seg("brand.frame", [["none", "Off"], ["blur", "Blurred"], ["color", "Colour"], ["gradient", "Gradient"], ["image", "My picture"]])}
        <div data-when="brand.frame:color,gradient,image" class="ov-colors">${color("brand.frame_color", "Colour")}<span data-when="brand.frame:gradient">${color("brand.frame_color2", "Second colour")}</span></div>
        <div data-when="brand.frame:image">${upload("brand.frame_image", "image/*", "🖼 Pick a picture")}</div>
        <div data-when="brand.frame:blur,color,gradient,image" class="ov-stack">
          ${slider("brand.frame_pad", "Space around", 0.02, 0.2, 0.01, "", "pct")}
          ${slider("brand.frame_radius", "Round corners", 0, 90, 1)}
        </div>
      </section>`;
  }
  function outroHtml() {
    return `
      <section class="ov-sec">
        <h3>Outro</h3>
        <small class="hint">A few seconds added at the end of every clip.</small>
        ${seg("outro.mode", [["off", "Off"], ["card", "End card"], ["video", "My video"]])}
        <div data-when="outro.mode:card" class="ov-stack">
          <label class="field"><span>Big text</span><input type="text" data-k="outro.text" maxlength="60"></label>
          <label class="field"><span>Small line</span><input type="text" data-k="outro.sub" maxlength="60" data-ph="handle"></label>
          <div class="ov-colors">${color("outro.bg", "Background")}${color("outro.color", "Text")}${color("outro.accent", "Accent")}</div>
          <label class="field"><span>Font</span><select data-k="outro.font" id="ovOutroFont"></select></label>
          <div class="field"><span>Picture</span>${upload("outro.picture", "image/*", "📷 Pick a picture")}
            <small class="hint">No picture = the card picture, or the first letter of your name.</small></div>
          ${slider("outro.seconds", "Length", 2, 4, 0.5, "", "s")}
          <div class="ov-outro-preview" id="ovOutroBox"><img alt="Outro preview" id="ovOutroImg"><i class="bar"></i></div>
          <button type="button" class="ghost small" id="ovOutroPlay">▶ Play preview</button>
        </div>
        <div data-when="outro.mode:video" class="ov-stack">
          ${upload("outro.video", "video/*", "🎬 Pick a video")}
          <small class="hint">Up to 10 seconds of it are used. It fills the frame.</small>
          <video id="ovOutroVideo" controls muted playsinline hidden style="width:100%;border-radius:12px"></video>
        </div>
      </section>
      <section class="ov-sec">
        <h3>Music</h3>
        <small class="hint">One song under the whole clip. Use music you're allowed to post.</small>
        ${upload("music.file", "audio/*,.mp3,.wav,.m4a", "🎵 Pick a song")}
        <audio id="ovMusic" controls hidden></audio>
        <div data-when="music.file:set" class="ov-stack">
          ${slider("music.volume", "Volume", 0, 1, 0.01, "", "pct")}
          <label class="switch"><input type="checkbox" data-k="music.duck"><span></span> Quieter while people talk</label>
          ${slider("music.rise", "Louder at the end", 0, 10, 0.5, "0 = stays the same.", "s")}
          <div data-when="music.rise:on">${slider("music.peak", "Volume at the end", 0, 1, 0.01, "", "pct")}</div>
        </div>
      </section>`;
  }

  /* ---------- binding ---------- */
  function bindPanel(root) {
    $$("[data-k]", root).forEach((el) => {
      const path = el.dataset.k;
      const evt = el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(evt, () => {
        let v = el.type === "checkbox" ? el.checked : el.value;
        if (el.type === "range" || el.type === "number") v = Number(v);
        if (el.type === "color") v = v.toUpperCase();
        set(path, v, el.type === "text" || el.tagName === "TEXTAREA");
        if (el.type === "range") paintOut(root, path, v);
      });
    });
    $$("[data-seg]", root).forEach((box) => $$("button", box).forEach((b) => b.addEventListener("click", () => {
      set(box.dataset.seg, box.hasAttribute("data-num") ? Number(b.dataset.v) : b.dataset.v);
    })));
    $$("[data-upload]", root).forEach((b) => b.addEventListener("click", () => pickFile(b.dataset.upload, b.dataset.accept)));
    $$("[data-clear]", root).forEach((b) => b.addEventListener("click", () => set(b.dataset.clear, "")));
  }
  function paintOut(root, path, v) {
    $$(`[data-out="${path}"]`, root).forEach((o) => {
      const f = o.dataset.fmt;
      o.textContent = f === "pct" ? Math.round(v * 100) + "%" : f === "s" ? v + " s" : String(v);
    });
  }
  function pickFile(path, accept) {
    const input = Object.assign(document.createElement("input"), { type: "file", accept });
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      try {
        CS().toast("Adding your file…");
        const item = await CS().api("/api/brand-assets", { method: "POST", body: form });
        remember(item.id, item.name);
        set(path, item.id);
        CS().toast("Added");
      } catch (err) { CS().notice(err.message); }
    });
    input.click();
  }

  /* ---------- syncing the panels with the design ---------- */
  function when(el, d) {
    const [path, rule] = el.dataset.when.split(":");
    const v = get(d, path);
    if (rule === "set") return !!v;
    if (rule === "on") return Number(v) > 0;
    if (rule === "some") return Array.isArray(v) && v.length > 0;
    return rule.split(",").includes(String(v));
  }
  function syncPanel(root, d) {
    const active = document.activeElement;
    $$("[data-k]", root).forEach((el) => {
      if (el === active && el.type !== "checkbox") return;
      const v = get(d, el.dataset.k);
      if (el.type === "checkbox") el.checked = !!v;
      else el.value = v ?? "";
      if (el.type === "range") paintOut(root, el.dataset.k, Number(v));
      if (el.dataset.ph === "channel") el.placeholder = DZ().credit() || "Channel name";
      if (el.dataset.ph === "handle") el.placeholder = "@" + ((DZ().credit() || "yourchannel").replace(/\s+/g, ""));
    });
    $$("[data-seg]", root).forEach((box) => {
      const v = String(get(d, box.dataset.seg));
      $$("button", box).forEach((b) => b.classList.toggle("on", b.dataset.v === v));
    });
    $$("[data-name]", root).forEach((el) => {
      const id = get(d, el.dataset.name);
      el.textContent = id ? O.names[id] || "Your file" : "";
      const clear = $(`[data-clear="${el.dataset.name}"]`, root);
      if (clear) clear.hidden = !id;
    });
    $$("[data-when]", root).forEach((el) => { el.hidden = !when(el, d); });
  }
  function syncGallery(d) {
    const box = $("#ovGallery");
    if (!box) return;
    const galKey = d.card.kind + "|" + O.filter;
    if (galKey === O.galKey) { loadThumbs(d); return; }
    O.galKey = galKey;
    $$("#ovFilter button").forEach((b) => b.classList.toggle("on", b.dataset.f === O.filter));
    box.replaceChildren(...CARDS.filter((c) => O.filter === "all" || c.tag === O.filter || c.id === "none").map((c) => {
      const tile = document.createElement("div");
      tile.className = "ov-tile" + (d.card.kind === c.id ? " on" : "");
      tile.tabIndex = 0;
      tile.setAttribute("role", "button");
      tile.innerHTML = c.id === "none" ? `<span class="none">✕</span><span>${c.name}</span>` : `<img alt="" data-id="${c.id}" src="${O.thumbs[c.id] || ""}"><span>${c.name}</span>`;
      const choose = () => {
        DZ().update((x) => {
          x.card.kind = c.id;
          if (c.id === "tiktok_hook" && x.card.y === 0.5) x.card.y = 0.3; // a hook reads best up top
        });
      };
      tile.addEventListener("click", choose);
      tile.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } });
      return tile;
    }));
    $("#ovCardFields").hidden = d.card.kind === "none";
    const label = $("[data-label-for-kind]");
    if (label) label.textContent = { ig_question: "Question", ig_poll: "Question", tiktok_hook: "Hook", news: "Headline", ig_comment: "Comment", yt_comment: "Comment" }[d.card.kind] || "Post text";
    loadThumbs(d);
  }
  async function loadThumbs(d) {
    const key = JSON.stringify([d.card.theme, d.card.avatar, d.aspect]);
    if (key === O.thumbKey) return;
    O.thumbKey = key;
    for (const c of CARDS) {
      if (c.id === "none") continue;
      const design = { ...d, credit: DZ().credit() || "yourchannel", resolution: 1080, card: { ...d.card, kind: c.id, size: 1, text: "" } };
      try {
        const res = await fetch("/api/overlay-preview", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ design, part: "card", title: SAMPLE }) });
        if (O.thumbKey !== key || res.status !== 200) continue;
        if (O.thumbs[c.id]) URL.revokeObjectURL(O.thumbs[c.id]);
        O.thumbs[c.id] = URL.createObjectURL(await res.blob());
        const img = $(`#ovGallery img[data-id="${c.id}"]`);
        if (img) img.src = O.thumbs[c.id];
      } catch { /* engine busy: tiles stay blank */ }
    }
  }
  function syncPoll(d) {
    const box = $("#ovPoll");
    if (!box || box.contains(document.activeElement)) return;
    const opts = d.card.options || [];
    box.replaceChildren(...opts.map((o, i) => {
      const row = document.createElement("div");
      row.className = "ov-row";
      row.innerHTML = `<input type="text" class="grow" maxlength="40" value="${esc(o.text)}" aria-label="Answer ${i + 1}">
        <input type="number" min="0" max="100" value="${o.pct}" aria-label="Percent for answer ${i + 1}"><span>%</span>
        <button type="button" class="ghost small" ${opts.length <= 2 ? "disabled" : ""} aria-label="Remove answer">✕</button>`;
      const [text, pct] = $$("input", row);
      text.addEventListener("input", () => DZ().update((x) => { x.card.options[i].text = text.value; }, true));
      pct.addEventListener("input", () => DZ().update((x) => { x.card.options[i].pct = Math.max(0, Math.min(100, Math.round(Number(pct.value) || 0))); }, true));
      $("button", row).addEventListener("click", () => DZ().update((x) => { x.card.options.splice(i, 1); }));
      return row;
    }));
    $("#ovPollAdd").hidden = opts.length >= 4;
  }
  function syncHandles(d) {
    const box = $("#ovHandles");
    if (!box || box.contains(document.activeElement)) return;
    const list = d.brand.handles || [];
    box.replaceChildren(...list.map((h, i) => {
      const row = document.createElement("div");
      row.className = "ov-row";
      row.innerHTML = `<select aria-label="Site">${PLATFORMS.map(([v, l]) => `<option value="${v}"${v === h.platform ? " selected" : ""}>${l}</option>`).join("")}</select>
        <input type="text" class="grow" maxlength="40" value="${esc(h.name)}" placeholder="yourname" aria-label="Handle">
        <button type="button" class="ghost small" aria-label="Remove">✕</button>`;
      const [pick] = $$("select", row), [name] = $$("input", row);
      pick.addEventListener("change", () => DZ().update((x) => { x.brand.handles[i].platform = pick.value; }));
      name.addEventListener("input", () => DZ().update((x) => { x.brand.handles[i].name = name.value; }, true));
      $("button", row).addEventListener("click", () => DZ().update((x) => { x.brand.handles.splice(i, 1); }));
      return row;
    }));
    $("#ovHandleAdd").hidden = list.length >= 5;
    $("#ovHandleAdd").textContent = list.length ? "+ Add another" : "+ Add a handle";
  }
  function syncOutro(d) {
    const sel = $("#ovOutroFont");
    if (sel && !sel.options.length) {
      sel.replaceChildren(...DZ().fonts().map((f) => Object.assign(document.createElement("option"), { value: f, textContent: f })));
      sel.value = d.outro.font;
    }
    const box = $("#ovOutroBox");
    if (box) box.classList.toggle("tv", d.aspect === "16:9");
    const video = $("#ovOutroVideo");
    if (video) {
      const src = d.outro.video ? `/api/brand-assets/${d.outro.video}` : "";
      video.hidden = !src;
      if (video.getAttribute("src") !== src) { if (src) video.src = src; else video.removeAttribute("src"); }
    }
    const audio = $("#ovMusic");
    if (audio) {
      const src = d.music.file ? `/api/brand-assets/${d.music.file}` : "";
      audio.hidden = !src;
      if (audio.getAttribute("src") !== src) { if (src) audio.src = src; else audio.removeAttribute("src"); }
      audio.volume = Math.max(0, Math.min(1, d.music.volume || 0));
    }
    if (d.outro.mode !== "card") return;
    const key = JSON.stringify([d.aspect, d.outro, d.card.avatar, DZ().credit()]);
    if (key === O.outroKey) return;
    O.outroKey = key;
    clearTimeout(O.outroTimer);
    O.outroTimer = setTimeout(async () => {
      try {
        const res = await fetch("/api/overlay-preview", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ design: { ...d, credit: DZ().credit() || "", resolution: 1080 }, part: "outro" }) });
        if (O.outroKey !== key || res.status !== 200) return;
        if (O.outroUrl) URL.revokeObjectURL(O.outroUrl);
        O.outroUrl = URL.createObjectURL(await res.blob());
        $("#ovOutroImg").src = O.outroUrl;
      } catch { /* engine busy */ }
    }, 200);
  }

  function sync() {
    const d = cur();
    if (!O.ready || !d || !d.card) return;
    for (const id of ["#dtab-cards", "#dtab-brand", "#dtab-outro"]) syncPanel($(id), d);
    syncGallery(d);
    syncPoll(d);
    syncHandles(d);
    syncOutro(d);
  }

  function init() {
    if (O.ready || !$("#dtab-cards")) return;
    names();
    $("#dtab-cards").innerHTML = cardsHtml();
    $("#dtab-brand").innerHTML = brandHtml();
    $("#dtab-outro").innerHTML = outroHtml();
    for (const id of ["#dtab-cards", "#dtab-brand", "#dtab-outro"]) bindPanel($(id));
    $$("#ovFilter button").forEach((b) => b.addEventListener("click", () => { O.filter = b.dataset.f; syncGallery(cur()); }));
    $("#ovPollAdd").addEventListener("click", () => DZ().update((x) => {
      if (x.card.options.length < 4) x.card.options.push({ text: `Option ${x.card.options.length + 1}`, pct: 10 });
    }));
    $("#ovHandleAdd").addEventListener("click", () => DZ().update((x) => {
      const used = new Set(x.brand.handles.map((h) => h.platform));
      const next = PLATFORMS.find(([p]) => !used.has(p)) || PLATFORMS[0];
      x.brand.handles.push({ platform: next[0], name: (DZ().credit() || "").replace(/\s+/g, "") });
    }));
    $("#ovOutroPlay").addEventListener("click", () => {
      const box = $("#ovOutroBox"), d = cur();
      box.classList.remove("play");
      void box.offsetWidth;
      box.style.setProperty("--secs", `${d.outro.seconds}s`);
      box.classList.add("play");
      clearTimeout(O.playing);
      O.playing = setTimeout(() => box.classList.remove("play"), d.outro.seconds * 1000 + 50);
    });
    O.ready = true;
  }
  function show() { sync(); }

  window.ClipOverlays = { init, sync, show };
})();
