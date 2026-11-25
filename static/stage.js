"use strict";
/* One live preview ("stage"). It draws a design the way the renderer will, and lets the buyer
   drag AND resize every part (gameplay, streamer cam, captions, title, credit) with snap guides.
   The maths mirror clipstudio/design.py (layout) and clipstudio/styles.py (text): keep in step.
   Used on step 2 (template editor) and in the clip editor. */
(function () {
  const CANVAS = { "9:16": [1080, 1920], "16:9": [1920, 1080], "1:1": [1080, 1080] };
  const TITLE_LOOKS = {
    white_box: { color: "#111", bg: "#fff" },
    red_box: { color: "#fff", bg: "#E5322D" },
    yellow: { color: "#FFD233", stroke: "#000", w: 7 },
    outline: { color: "#fff", stroke: "#000", w: 8 },
    meme: { color: "#111" },
  };
  const LIMITS = { caption: [24, 220], title: [24, 200], credit: [14, 120], card: [0.35, 1], handles: [16, 90] };
  const NAMES = { game: "Gameplay", cam: "Streamer cam", extra: "Extra part", caption: "Captions", title: "Title", credit: "Credit", card: "Card", handles: "Handles bar" };
  const CAM_LAYOUTS = ["stack", "corner", "side", "trio"];
  const SIDE = { "9:16": [[0, 0.34, 0.62, 0.32], [0.62, 0.34, 0.38, 0.32]], "16:9": [[0, 0, 0.68, 1], [0.68, 0.25, 0.32, 0.5]], "1:1": [[0, 0.2, 0.62, 0.6], [0.62, 0.2, 0.38, 0.6]] };
  const TRIO = [[0, 0, 1, 0.3], [0, 0.3, 1, 0.42], [0, 0.72, 1, 0.28]]; // cam, game, extra (= design._TRIO)
  const SNAP = 0.012; // how close (share of the frame) before a part snaps to a guide
  const PLACEHOLDER_CAM = [0.02, 0.6, 0.24, 0.36];

  /* ---- layout maths (mirror of design.py) ---- */
  function effectiveLayout(d) {
    const layout = d.layout;
    if (layout === "meme" && d.title && d.title.show === false) return "full"; // title removed: no empty bar
    if (CAM_LAYOUTS.includes(layout) && !d.cam_roi) return "full";
    if (d.aspect !== "9:16" && ["stack", "split", "trio"].includes(layout)) return { stack: "corner", split: "full", trio: "side" }[layout];
    return layout;
  }
  function gameRegion(d) {
    if (d.game_roi) return d.game_roi;
    return [0, 0, 1, 1]; // whole frame: gameplay stays centred (= design.game_region)
  }
  function camOutputRect(d) {
    const l = effectiveLayout(d);
    if (l === "stack") {
      if (d.cam_rect) return d.cam_rect;
      return d.cam_side === "top" ? [0, 0, 1, d.split] : [0, 1 - d.split, 1, d.split];
    }
    if (l === "corner") return d.cam_rect || [0.05, 0.05, 0.4, 0.2];
    if (l === "side") return d.cam_rect || SIDE[d.aspect][1];
    if (l === "trio") return d.cam_rect || TRIO[0];
    return null;
  }
  // Mirror of design.extra_area(): the third part (Trio), or null.
  function extraArea(d) {
    const l = effectiveLayout(d);
    if (l === "trio") return d.extra_rect || TRIO[2];
    if (l === "side" && d.extra_rect) return d.extra_rect;
    return null;
  }
  function extraRegion(d) { return d.extra_roi || [0.3, 0.3, 0.4, 0.4]; }
  // Mirror of design.cam_cover(): blur the stream's own cam when it was removed or moved into a box.
  function coversCam(d) {
    if (!d.cam_roi || effectiveLayout(d) === "split") return false;
    return !!(d.hide_cam || camOutputRect(d));
  }
  function gameArea(d) {
    const l = effectiveLayout(d);
    if (l === "full" && CAM_LAYOUTS.includes(d.layout)) return [0, 0, 1, 1]; // no cam found
    if (d.game_rect && l !== "split") return d.game_rect;
    if (l === "stack") return d.cam_side === "top" ? [0, d.split, 1, 1 - d.split] : [0, 0, 1, 1 - d.split];
    if (l === "meme") return [0, d.meme_bar, 1, 1 - d.meme_bar];
    if (l === "side") return SIDE[d.aspect][0];
    if (l === "trio") return TRIO[1];
    return [0, 0, 1, 1];
  }

  /* ---- caption maths (mirror of styles.py) ---- */
  function chunks(words, size) {
    const out = []; let chunk = [];
    for (const w of words) {
      const last = chunk[chunk.length - 1];
      if (chunk.length && (chunk.length >= size || w.start - last.end > 0.6 || /[.?!]$/.test(last.text))) { out.push(chunk); chunk = []; }
      chunk.push(w);
    }
    if (chunk.length) out.push(chunk);
    return out;
  }
  function balancedCut(texts) {
    const total = texts.reduce((n, t) => n + t.length, 0) + texts.length - 1;
    let best = 1, bestGap = total, run = 0;
    for (let i = 1; i < texts.length; i++) {
      run += texts[i - 1].length + (i > 1 ? 1 : 0);
      const gap = Math.abs(total - 2 * run);
      if (gap < bestGap) { best = i; bestGap = gap; }
    }
    return best;
  }
  let EMOJI = { words: {}, files: [] };
  function emojiFor(text) {
    for (const raw of String(text).toLowerCase().split(/\s+/)) {
      const code = EMOJI.words[raw.replace(/[^a-z]/g, "")];
      if (code) return code;
    }
    return null;
  }
  // Mirror of styles.key_word(): the word that gets the highlight colour in "punch" / "twotone".
  const PLAIN = new Set("the a an to of and is it i you we he she they in on at for this that be was are me my so just like do oh im i'm its it's your our with but or if then there what yeah hai ka ki ke ko se ne toh na bhi ho main hum".split(" "));
  const BANNED = new Set("fuck fucking fucked shit bitch pussy dick cunt asshole motherfucker bastard slut whore".split(" "));
  function keyWord(texts) {
    let best = -1, bestScore = 0;
    texts.forEach((t, i) => {
      const core = [...t.toLowerCase()].filter((ch) => /\p{L}|'/u.test(ch)).join("");
      if (!core || PLAIN.has(core)) return;
      const score = core.length + (t.includes("*") || BANNED.has(core) ? 6 : 0) + (/[!?]$/.test(t) ? 3 : 0);
      if (score > bestScore) { best = i; bestScore = score; }
    });
    return best;
  }
  // Mirror of styles.caption_animation(): "auto" = what each style always did.
  function animationOf(c) {
    if (c.animation && c.animation !== "auto") return c.animation;
    return c.style === "pop" ? "pop_auto" : c.style === "karaoke" ? "highlight" : "full_line";
  }
  const SHOUT_WORDS = [12, 13]; // "no way" in the sample words
  const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

  /* ---- drawing helpers ---- */
  function cover(ctx, img, sx, sy, sw, sh, dx, dy, dw, dh, fx) {
    const scale = Math.max(dw / sw, dh / sh);
    const cw = dw / scale, ch = dh / scale;
    ctx.drawImage(img, sx + (sw - cw) * fx, sy + (sh - ch) / 2, cw, ch, dx, dy, dw, dh);
  }
  function contain(ctx, img, sx, sy, sw, sh, dx, dy, dw, dh) {
    const scale = Math.min(dw / sw, dh / sh);
    const w = sw * scale, h = sh * scale;
    ctx.drawImage(img, sx, sy, sw, sh, dx + (dw - w) / 2, dy + (dh - h) / 2, w, h);
  }
  function fit(ctx, img, src, area, fill, fx) {
    const [sx, sy, sw, sh] = src, [dx, dy, dw, dh] = area;
    ctx.save();
    ctx.beginPath(); ctx.rect(dx, dy, dw, dh); ctx.clip();
    if (fill === "bars") { ctx.fillStyle = "#000"; ctx.fillRect(dx, dy, dw, dh); contain(ctx, img, sx, sy, sw, sh, dx, dy, dw, dh); }
    else if (fill === "blur") {
      ctx.filter = "blur(14px) brightness(.9)"; cover(ctx, img, sx, sy, sw, sh, dx - 20, dy - 20, dw + 40, dh + 40, 0.5);
      ctx.filter = "none"; contain(ctx, img, sx, sy, sw, sh, dx, dy, dw, dh);
    } else cover(ctx, img, sx, sy, sw, sh, dx, dy, dw, dh, fx);
    ctx.restore();
  }
  function mediaSize(m) {
    if (!m) return [0, 0];
    return [m.videoWidth || m.naturalWidth || m.width || 0, m.videoHeight || m.naturalHeight || m.height || 0];
  }
  function drawCamPlaceholder(ctx, x, y, w, h) {
    const g = ctx.createLinearGradient(x, y, x, y + h);
    g.addColorStop(0, "#F6B08F"); g.addColorStop(1, "#E9825D");
    ctx.fillStyle = g; ctx.fillRect(x, y, w, h);
    const r = Math.min(w, h) * 0.17, cx = x + w / 2, cy = y + h * 0.42;
    ctx.fillStyle = "rgba(255,255,255,.88)";
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(cx, y + h * 0.42 + r * 2.6, r * 2, r * 1.4, 0, Math.PI, 0); ctx.fill();
    // A friendly face + a label, so it's obvious this box is the streamer's camera.
    ctx.fillStyle = "#3A2A22";
    ctx.beginPath(); ctx.arc(cx - r * 0.38, cy - r * 0.15, r * 0.12, 0, Math.PI * 2); ctx.arc(cx + r * 0.38, cy - r * 0.15, r * 0.12, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#3A2A22"; ctx.lineWidth = Math.max(2, r * 0.12);
    ctx.beginPath(); ctx.arc(cx, cy + r * 0.1, r * 0.45, 0.15 * Math.PI, 0.85 * Math.PI); ctx.stroke();
  }

  /* A stand-in "gameplay" picture for when there's no video yet. */
  let sample = null;
  function samplePicture() {
    if (sample) return sample;
    const c = document.createElement("canvas");
    c.width = 1280; c.height = 720;
    const x = c.getContext("2d");
    const sky = x.createLinearGradient(0, 0, 0, 420);
    sky.addColorStop(0, "#7FB7E8"); sky.addColorStop(1, "#F7D9B8");
    x.fillStyle = sky; x.fillRect(0, 0, 1280, 720);
    x.fillStyle = "#8C9BAD";
    [[90, 140, 110, 280], [230, 90, 90, 330], [350, 180, 130, 240], [860, 110, 120, 310], [1010, 60, 95, 360], [1130, 170, 110, 250]]
      .forEach(([a, b, w, h]) => {
        x.fillStyle = "#8C9BAD"; x.fillRect(a, b, w, h);
        x.fillStyle = "rgba(255,240,200,.75)";
        for (let wy = b + 18; wy < b + h - 30; wy += 34) for (let wx = a + 14; wx < a + w - 20; wx += 28) x.fillRect(wx, wy, 14, 18);
      });
    x.fillStyle = "#5B6470"; x.beginPath(); x.moveTo(0, 720); x.lineTo(520, 420); x.lineTo(760, 420); x.lineTo(1280, 720); x.fill();
    x.strokeStyle = "#F4E7C8"; x.lineWidth = 8; x.setLineDash([40, 40]);
    x.beginPath(); x.moveTo(640, 430); x.lineTo(640, 720); x.stroke();
    x.setLineDash([]);
    x.fillStyle = "#2C2F36"; x.fillRect(560, 560, 160, 90); x.fillStyle = "#E5322D"; x.fillRect(570, 620, 36, 14); x.fillRect(674, 620, 36, 14);
    // Label the game part too: the template shows where each part of the stream ends up.
    x.font = "900 64px Poppins, Arial, sans-serif"; x.textAlign = "center"; x.textBaseline = "middle";
    x.fillStyle = "rgba(0,0,0,.45)"; x.fillText("GAMEPLAY", 646, 304);
    x.fillStyle = "#fff"; x.fillText("GAMEPLAY", 640, 298);
    const [px, py, pw, ph] = PLACEHOLDER_CAM;
    drawCamPlaceholder(x, px * 1280, py * 720, pw * 1280, ph * 720);
    sample = c;
    return c;
  }

  const assetImages = {};
  function assetImage(id, onload) {
    if (!id) return null;
    if (!assetImages[id]) {
      const img = new Image();
      img.onload = onload;
      img.src = `/api/brand-assets/${id}`;
      assetImages[id] = img;
    }
    return assetImages[id];
  }

  /* ---- the stage ---- */
  function create(host, opts = {}) {
    const root = document.createElement("div");
    root.className = "stage st";
    root.tabIndex = 0;
    root.innerHTML = `
      <canvas class="st-canvas"></canvas>
      <div class="st-box st-game" data-part="game"><span class="st-tag">Gameplay</span></div>
      <div class="st-box st-cam" data-part="cam"><span class="st-tag">Streamer cam</span></div>
      <div class="st-box st-extra" data-part="extra"><span class="st-tag">Extra part</span></div>
      <div class="st-safe" hidden><i class="top"></i><i class="bottom"></i><i class="right"></i></div>
      <img class="st-logo" alt="" hidden>
      <div class="st-ov st-card" data-part="card" hidden><img alt="" draggable="false"></div>
      <div class="st-ov st-handles" data-part="handles" hidden><img alt="" draggable="false"></div>
      <img class="st-emoji" alt="" hidden>
      <div class="st-text st-caption" data-part="caption"></div>
      <div class="st-text st-title" data-part="title"></div>
      <div class="st-text st-credit" data-part="credit"></div>
      <div class="st-shout" hidden></div>
      <i class="st-guide v" hidden></i><i class="st-guide v" hidden></i>
      <i class="st-guide h" hidden></i><i class="st-guide h" hidden></i>`;
    host.replaceChildren(root);
    const q = (s) => root.querySelector(s);
    const parts = { game: q(".st-game"), cam: q(".st-cam"), extra: q(".st-extra"), caption: q(".st-caption"), title: q(".st-title"), credit: q(".st-credit"),
      card: q(".st-card"), handles: q(".st-handles") };
    const isBox = (name) => name === "game" || name === "cam" || name === "extra";
    for (const [name, node] of Object.entries(parts)) {
      const corners = isBox(name) ? ["nw", "ne", "sw", "se"] : ["se"];
      corners.forEach((c) => { const h = document.createElement("span"); h.className = "st-handle " + c; h.dataset.corner = c; node.append(h); });
      node.title = `Drag to move ${NAMES[name].toLowerCase()} · drag a corner to resize`;
    }
    const canvas = q(".st-canvas"), emojiImg = q(".st-emoji"), shoutBox = q(".st-shout");
    const st = {
      root, d: null, media: null, words: [], time: 0, title: "", selected: null, creditFallback: "",
      editable: opts.editable !== false, placeholderCam: !!opts.placeholderCam, safe: false,
      onChange: opts.onChange || (() => {}), onSelect: opts.onSelect || (() => {}),
    };

    function view() {
      // What we draw: in the template editor there's no cam yet. Without a video the sample picture
      // has a pretend cam painted in (so Remove/Move can be seen); over a real video we draw one.
      let d = st.d;
      const size = st.media ? mediaSize(st.media) : [0, 0];
      if (size[0] && size[1] > size[0] * 1.15) {
        // Mirror of design.adapt_to_source(): a vertical stream fills the Short, no cam box or split,
        // and sits whole on a blurred copy in a 16:9 / square video.
        d = { ...d, hide_cam: false, cam_rect: null, game_rect: null, game_roi: null, cam_roi: null, extra_rect: null, extra_roi: null, bg_blur: false,
          layout: [...CAM_LAYOUTS, "split"].includes(d.layout) ? "full" : d.layout,
          fill: d.aspect !== "9:16" && d.fill === "zoom" ? "blur" : d.fill };
        return d;
      }
      if (st.placeholderCam && !d.cam_roi) {
        const real = st.media && mediaSize(st.media)[0];
        if (!real) return { ...d, cam_roi: PLACEHOLDER_CAM };
        if (CAM_LAYOUTS.includes(d.layout)) return { ...d, cam_roi: PLACEHOLDER_CAM, game_roi: d.game_roi || [0, 0, 1, 1], _fakeCam: true };
      }
      return d;
    }
    let blurCanvas = null;
    function withCamBlurred(media, cam) {
      // a copy of the picture with the streamer cam blurred out (same idea as the renderer's gblur)
      const [mw, mh] = mediaSize(media);
      const k = Math.min(1, 960 / mw);
      const w = Math.round(mw * k), h = Math.round(mh * k);
      blurCanvas = blurCanvas || document.createElement("canvas");
      if (blurCanvas.width !== w || blurCanvas.height !== h) { blurCanvas.width = w; blurCanvas.height = h; }
      const x = blurCanvas.getContext("2d");
      x.filter = "none";
      x.drawImage(media, 0, 0, w, h);
      const [cx, cy, cw, ch] = [cam[0] * w, cam[1] * h, cam[2] * w, cam[3] * h];
      x.save(); x.beginPath(); x.rect(cx, cy, cw, ch); x.clip();
      x.filter = `blur(${Math.max(6, Math.min(cw, ch) / 6)}px)`;
      x.drawImage(media, 0, 0, w, h);
      x.restore();
      x.filter = "none";
      return blurCanvas;
    }
    function scale() {
      const d = st.d;
      return d.aspect === "16:9" ? root.clientHeight / 1080 : root.clientWidth / 1080;
    }

    function drawCanvas() {
      drawLayout();
      drawFrame();
    }
    // Background card (My brand): the picture shrinks into a rounded card on blur / colour / picture,
    // like overlays.frame_parts() + the renderer.
    let frameCopy = null;
    function drawFrame() {
      const d = st.d, b = d.brand;
      if (!b || !b.frame || b.frame === "none") return;
      const [W, H] = CANVAS[d.aspect];
      const cw = canvas.width, ch = canvas.height, k = cw / W, side = Math.min(W, H);
      frameCopy = frameCopy || document.createElement("canvas");
      frameCopy.width = cw; frameCopy.height = ch;
      frameCopy.getContext("2d").drawImage(canvas, 0, 0);
      const ctx = canvas.getContext("2d");
      const m = Math.round((b.frame_pad * side) / 2) * 2 * k;
      ctx.save();
      if (b.frame === "blur") { ctx.filter = "blur(10px) brightness(.85)"; ctx.drawImage(frameCopy, -20, -20, cw + 40, ch + 40); ctx.filter = "none"; }
      else if (b.frame === "gradient") { const g = ctx.createLinearGradient(0, 0, 0, ch); g.addColorStop(0, b.frame_color); g.addColorStop(1, b.frame_color2); ctx.fillStyle = g; ctx.fillRect(0, 0, cw, ch); }
      else {
        ctx.fillStyle = b.frame_color; ctx.fillRect(0, 0, cw, ch);
        const pic = b.frame === "image" && assetImage(b.frame_image, () => paint());
        if (pic && pic.complete && pic.naturalWidth) {
          ctx.filter = "blur(8px)"; cover(ctx, pic, 0, 0, pic.naturalWidth, pic.naturalHeight, 0, 0, cw, ch, 0.5);
          ctx.filter = "none"; ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fillRect(0, 0, cw, ch);
        }
      }
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(m, m, cw - 2 * m, ch - 2 * m, (b.frame_radius * side / 1080) * k); else ctx.rect(m, m, cw - 2 * m, ch - 2 * m);
      ctx.clip();
      ctx.drawImage(frameCopy, m, m, cw - 2 * m, ch - 2 * m);
      ctx.restore();
    }
    function drawLayout() {
      const d = st.d;
      if (!d) return;
      const [W, H] = CANVAS[d.aspect];
      const res = opts.resolution || 0.5;
      if (canvas.width !== Math.round(W * res)) { canvas.width = Math.round(W * res); canvas.height = Math.round(H * res); }
      const ctx = canvas.getContext("2d");
      const cw = canvas.width, ch = canvas.height;
      const v = view(), layout = effectiveLayout(v);
      ctx.fillStyle = layout === "meme" ? "#fff" : "#000";
      ctx.fillRect(0, 0, cw, ch);
      const media = st.media && mediaSize(st.media)[0] ? st.media : samplePicture();
      const [mw, mh] = mediaSize(media);
      const px = (r, w, h) => [r[0] * w, r[1] * h, r[2] * w, r[3] * h];
      const g = px(gameRegion(v), mw, mh);
      const gamePic = coversCam(v) && !v._fakeCam ? withCamBlurred(media, v.cam_roi) : media;
      const gs = mediaSize(gamePic)[0] / mw;
      if (layout === "split") {
        const half = g[2] / 2;
        cover(ctx, media, g[0], g[1], half, g[3], 0, 0, cw, ch / 2, 0.5);
        cover(ctx, media, g[0] + g[2] - half, g[1], half, g[3], 0, ch / 2, cw, ch / 2, 0.5);
        return;
      }
      const ga = px(gameArea(v), cw, ch);
      if (v.bg_blur && layout !== "meme" && !(ga[0] <= 0 && ga[1] <= 0 && ga[2] >= cw && ga[3] >= ch)) {
        // = design._compose(): the space around the boxes is a blurred, slightly darker copy
        ctx.save(); ctx.filter = "blur(16px) brightness(.85)";
        cover(ctx, media, 0, 0, mw, mh, -20, -20, cw + 40, ch + 40, 0.5);
        ctx.restore();
      }
      fit(ctx, gamePic, g.map((n) => n * gs), ga, d.fill, d.focus_x);
      const er = extraArea(v);
      if (er) {
        const e = px(extraRegion(v), mw, mh), eo = px(er, cw, ch);
        ctx.save(); ctx.beginPath(); ctx.rect(eo[0], eo[1], eo[2], eo[3]); ctx.clip();
        cover(ctx, media, e[0], e[1], e[2], e[3], eo[0], eo[1], eo[2], eo[3], 0.5);
        ctx.restore();
      }
      const cr = camOutputRect(v);
      if (!cr) return;
      let o = px(cr, cw, ch);
      const shape = layout === "corner" ? v.cam_shape || "frame" : "box";
      let b = shape === "frame" ? 6 * (cw / W) : 0;
      ctx.save();
      if (shape === "circle") {
        // = design._compose(): a circle the size of the box's short side, with a thin white ring
        const dd = Math.min(o[2], o[3]);
        o = [o[0] + (o[2] - dd) / 2, o[1] + (o[3] - dd) / 2, dd, dd];
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(o[0] + dd / 2, o[1] + dd / 2, dd / 2, 0, Math.PI * 2); ctx.fill();
        b = Math.max(2, dd * 0.025);
        ctx.beginPath(); ctx.arc(o[0] + dd / 2, o[1] + dd / 2, dd / 2 - b, 0, Math.PI * 2); ctx.clip();
      } else if (shape === "round") {
        const r = Math.min(o[2], o[3]) * 0.09;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(o[0], o[1], o[2], o[3], r); else ctx.rect(o[0], o[1], o[2], o[3]);
        ctx.clip();
      } else {
        if (b) { ctx.fillStyle = "#fff"; ctx.fillRect(o[0], o[1], o[2], o[3]); }
        ctx.beginPath(); ctx.rect(o[0] + b, o[1] + b, o[2] - 2 * b, o[3] - 2 * b); ctx.clip();
      }
      if (v._fakeCam) drawCamPlaceholder(ctx, o[0] + b, o[1] + b, o[2] - 2 * b, o[3] - 2 * b);
      else { const c = px(v.cam_roi, mw, mh); cover(ctx, media, c[0], c[1], c[2], c[3], o[0] + b, o[1] + b, o[2] - 2 * b, o[3] - 2 * b, 0.5); }
      ctx.restore();
    }

    function place(node, r) {
      Object.assign(node.style, { left: r[0] * 100 + "%", top: r[1] * 100 + "%", width: r[2] * 100 + "%", height: r[3] * 100 + "%" });
    }
    function paintBoxes() {
      const v = view(), layout = effectiveLayout(v);
      parts.game.hidden = !st.editable || layout === "split";
      if (!parts.game.hidden) place(parts.game, gameArea(v));
      const cr = camOutputRect(v);
      parts.cam.hidden = !st.editable || !cr;
      if (cr) place(parts.cam, cr);
      const er = extraArea(v);
      parts.extra.hidden = !st.editable || !er;
      if (er) place(parts.extra, er);
      // Platform safe zones (preview only): where the app's buttons and text sit on top of a Short.
      q(".st-safe").hidden = !st.safe || st.d.aspect !== "9:16";
    }

    function captionGroup() {
      const c = st.d.caption;
      const groups = chunks(st.words, c.style === "classic" ? 7 : c.words);
      const t = st.time;
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i];
        let end = g[g.length - 1].end + 0.4;
        if (i + 1 < groups.length) end = Math.min(end, groups[i + 1][0].start);
        if (t >= g[0].start && t < Math.max(end, g[g.length - 1].end)) return g;
      }
      return null;
    }
    function paintCaption() {
      const d = st.d, c = d.caption, box = parts.caption, k = scale();
      box.hidden = c.style === "none";
      emojiImg.hidden = true;
      if (box.hidden) return;
      let group = captionGroup();
      const placeholder = !group;
      if (placeholder) group = (c.uppercase ? "YOUR CAPTIONS HERE" : "Your captions here").split(" ").map((text) => ({ text, start: -1, end: -1 }));
      const texts = group.map((w) => (c.uppercase ? w.text.toUpperCase() : w.text));
      // Sample shout (template step only): "NO WAY!" big and yellow instead of the caption, like the video.
      const shout = d.shout && st.words.sample && !placeholder && SHOUT_WORDS.some((i) => st.words[i] && st.time >= st.words[i].start && st.time < st.words[SHOUT_WORDS[1]].end + 0.35);
      shoutBox.hidden = !shout;
      if (shout) {
        const [W, H] = CANVAS[d.aspect];
        const size = Math.min(200, Math.max(c.size * 1.45, 110));
        const fresh = shoutBox.hidden !== false || !shoutBox.dataset.on;
        shoutBox.dataset.on = "1";
        shoutBox.textContent = "NO WAY!";
        Object.assign(shoutBox.style, { left: c.x * 100 + "%", top: (c.style === "none" ? 0.45 : c.y) * 100 + "%", fontFamily: `"${c.font}"`, fontSize: size * k + "px", webkitTextStroke: `${9 * k * 2}px #000` });
        if (fresh) { shoutBox.classList.remove("hit"); void shoutBox.offsetWidth; shoutBox.classList.add("hit"); }
        box.hidden = true;
        root.classList.add("shaking");
        return;
      }
      delete shoutBox.dataset.on;
      root.classList.remove("shaking");
      let html;
      if (c.style === "punch" || c.style === "twotone") {
        let lines = [texts];
        if (c.style === "twotone" && texts.length > 2) { const cut = balancedCut(texts); lines = [texts.slice(0, cut), texts.slice(cut)]; }
        const key = keyWord(lines[0]);
        // Words appear one at a time (first-colour line, then the second colour), as the video does.
        let n = -1;
        const shownUpTo = placeholder ? 99 : group.reduce((acc, w, i) => (st.time >= w.start ? i : acc), -1);
        const word = (t, colour) => { n += 1; return `<span style="color:${colour};${n > shownUpTo ? "opacity:0" : ""}">${esc(t)}</span>`; };
        html = lines[0].map((t, i) => word(t, i === key ? c.highlight : c.color)).join(" ");
        if (lines.length === 2) html += "<br>" + lines[1].map((t) => word(t, c.second)).join(" ");
      } else {
        // Mirror of styles.caption_animation() + the per-word captions in build_ass().
        const anim = animationOf(c);
        const active = placeholder ? (anim === "one_by_one" ? 0 : -1)
          : group.reduce((acc, w, i) => (st.time >= w.start ? i : acc), -1);
        const cut = c.style === "twoline" && texts.length > 2 ? balancedCut(texts) : -1;
        const pieces = texts.map((t, i) => {
          if (anim === "one_by_one") return i === active ? `<span class="wpop" style="color:${c.highlight}">${esc(t)}</span>` : null;
          if (anim === "full_line" || anim === "pop_line") return esc(t);
          if (i === active) return `<span${anim === "pop_word" ? ' class="wpop"' : ""} style="color:${c.highlight}">${esc(t)}</span>`;
          if (anim === "build_up" && i > active && !placeholder) return `<span style="opacity:0">${esc(t)}</span>`;
          return esc(t);
        });
        if (anim === "one_by_one") html = pieces.filter(Boolean).join("");
        else html = cut > 0 ? pieces.slice(0, cut).join(" ") + "<br>" + pieces.slice(cut).join(" ") : pieces.join(" ");
      }
      const key = html + "|" + c.style + "|" + (c.animation || "auto");
      if (box.dataset.key !== key) {
        const anim = animationOf(c);
        const fresh = !placeholder && (anim === "pop_auto" || anim === "pop_line") && box.dataset.first !== String(group[0].start);
        box.dataset.first = String(group[0].start);
        box.dataset.key = key;
        box.querySelector(".st-words")?.remove();
        const span = document.createElement("span");
        span.className = "st-words" + (fresh ? " pop" : "");
        span.innerHTML = html;
        box.prepend(span);
      }
      box.style.left = c.x * 100 + "%";
      box.style.top = c.y * 100 + "%";
      box.style.fontFamily = `"${c.font}"`;
      box.style.fontSize = c.size * k + "px";
      box.style.color = c.color;
      const oneLine = ["twoline", "punch", "twotone"].includes(c.style);
      box.style.whiteSpace = oneLine ? "nowrap" : "normal";
      box.style.maxWidth = oneLine ? "none" : "";
      const boxed = c.style === "boxed";
      const soft = c.style === "punch" || c.style === "twotone";
      box.style.webkitTextStroke = boxed || soft ? "0" : `${c.outline * k * 2}px ${c.outline_color}`;
      box.style.paintOrder = "stroke fill";
      box.style.background = boxed ? "rgba(0,0,0,.38)" : "transparent";
      box.style.padding = boxed ? `${Math.max(8, c.outline * 2) * k}px` : `${4 * k}px ${8 * k}px`;
      box.style.textShadow = boxed ? "none" : soft ? `${6 * k}px ${8.4 * k}px 0 rgba(0,0,0,.94), ${11 * k}px ${16 * k}px ${8 * k}px rgba(0,0,0,.55)` : `0 ${2 * k}px ${3 * k}px rgba(0,0,0,.5)`;
      // Long lines shrink to fit the frame, as styles.py does (\fscx) for long Kannada / Hindi words.
      box.style.transform = "translate(-50%, -50%)";
      if (oneLine) {
        const room = root.clientWidth * 0.92, wide = box.scrollWidth;
        if (wide > room) box.style.transform = `translate(-50%, -50%) scale(${(room / wide).toFixed(3)})`;
      }
      // Emoji: same place and size as styles.emoji_overlays()
      if (c.emoji) {
        const code = emojiFor(texts.join(" ")) || (placeholder ? EMOJI.files[0] || null : null);
        if (code) {
          const [W, H] = CANVAS[d.aspect];
          const outPx = root.clientWidth / W;
          const size = Math.max(48, c.size * 1.25);
          const lines = c.style === "twoline" ? 2 : 1;
          const y = Math.max(size / 2, c.y * H - c.size * (0.55 * lines + 0.25) - size / 2);
          const src = `/emoji/${code}.png`;
          if (emojiImg.getAttribute("src") !== src) emojiImg.src = src;
          Object.assign(emojiImg.style, { left: c.x * 100 + "%", top: (y / H) * 100 + "%", width: size * outPx + "px", height: size * outPx + "px" });
          emojiImg.hidden = false;
        }
      }
    }
    function paintTitle() {
      const d = st.d, t = d.title, box = parts.title, k = scale();
      const look = TITLE_LOOKS[t.style] || TITLE_LOOKS.white_box;
      const text = (st.title || "").trim();
      box.hidden = !text || t.show === false;
      const meme = effectiveLayout(view()) === "meme";
      const shown = t.style === "meme" || t.style === "yellow" ? text.toUpperCase() : text;
      if (box.dataset.text !== shown) { box.dataset.text = shown; box.firstChild?.nodeType === 3 ? (box.firstChild.data = shown) : box.prepend(document.createTextNode(shown)); }
      box.style.left = (meme ? 0.5 : t.x) * 100 + "%";
      box.style.top = (meme ? d.meme_bar / 2 : t.y) * 100 + "%";
      box.style.fontFamily = `"${t.font}"`;
      box.style.fontSize = t.size * k + "px";
      box.style.color = look.color;
      box.style.background = look.bg || "transparent";
      box.style.padding = look.bg ? `${18 * k}px` : `${4 * k}px ${8 * k}px`;
      box.style.borderRadius = look.bg ? `${22 * k}px` : ""; // = styles.TITLE_RADIUS, rounded in the video too
      box.style.webkitTextStroke = look.stroke ? `${look.w * 2 * k}px ${look.stroke}` : "0";
      box.style.paintOrder = "stroke fill";
      box.classList.toggle("locked", meme);
      const credit = parts.credit;
      const name = (d.credit || st.creditFallback || "").replace(/^@/, "");
      credit.hidden = !(d.credit_on && name);
      const label = "@" + name;
      if (credit.dataset.text !== label) { credit.dataset.text = label; credit.firstChild?.nodeType === 3 ? (credit.firstChild.data = label) : credit.prepend(document.createTextNode(label)); }
      credit.style.left = d.credit_x * 100 + "%";
      credit.style.top = d.credit_y * 100 + "%";
      credit.style.fontSize = d.credit_size * k + "px";
    }
    /* Cards, handles bar and logo come from the engine (/api/overlay-preview) so they look exactly like the video. */
    const ov = { card: { key: "", url: "", w: 0, h: 0, size: 0 }, handles: { key: "", url: "", w: 0, h: 0, size: 0 }, timer: 0 };
    function overlayDesign() {
      const d = st.d;
      return { ...d, credit: d.credit || st.creditFallback || "", resolution: 1080 };
    }
    function wanted(part) {
      const d = st.d;
      if (part === "card") {
        const c = d.card;
        if (!c || c.kind === "none") return "";
        return JSON.stringify([d.aspect, c.kind, c.theme, c.name, c.handle, c.verified, c.avatar, c.text, c.options, c.ticker,
          c.kind === "news" ? 0 : c.size, d.credit || st.creditFallback, st.title]);
      }
      const b = d.brand;
      return b && b.handles && b.handles.some((h) => (h.name || "").trim()) ? JSON.stringify([d.aspect, b.handles, b.handles_size]) : "";
    }
    function fetchOverlays() {
      if (!st.d) return;
      for (const part of ["card", "handles"]) {
        const key = wanted(part), slot = ov[part];
        if (key === slot.key) continue;
        slot.key = key;
        if (!key) { slot.url = ""; continue; }
        const size = part === "card" ? st.d.card.size : st.d.brand.handles_size;
        fetch("/api/overlay-preview", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ design: overlayDesign(), part, title: st.title || "" }) })
          .then((r) => (r.status === 200 ? r.blob() : null))
          .then((blob) => {
            if (slot.key !== key) return;
            const img = new Image();
            const url = blob ? URL.createObjectURL(blob) : "";
            img.onload = () => {
              if (slot.key !== key) return;
              if (slot.url) URL.revokeObjectURL(slot.url);
              Object.assign(slot, { url, w: img.naturalWidth, h: img.naturalHeight, size });
              paintOverlays(true);
            };
            if (url) img.src = url; else slot.url = "";
          })
          .catch(() => {});
      }
    }
    function placeOverlay(node, slot, w, h, cx, cy) {
      // w, h, cx, cy in frame px (1080 reference); kept inside the frame like overlays.card_box()
      const [W, H] = CANVAS[st.d.aspect];
      const x = Math.max(0, Math.min(W - w, cx - w / 2)), y = Math.max(0, Math.min(H - h, cy - h / 2));
      const img = node.querySelector("img");
      if (img.getAttribute("src") !== slot.url) img.src = slot.url;
      Object.assign(node.style, { left: (x / W) * 100 + "%", top: (y / H) * 100 + "%", width: (w / W) * 100 + "%", height: (h / H) * 100 + "%" });
    }
    function paintOverlays(fresh) {
      const d = st.d, [W, H] = CANVAS[d.aspect], side = Math.min(W, H);
      if (!fresh) { clearTimeout(ov.timer); ov.timer = setTimeout(fetchOverlays, 160); }
      const card = d.card || {}, cs = ov.card;
      parts.card.hidden = !card.kind || card.kind === "none" || !cs.url || !wanted("card");
      if (!parts.card.hidden) {
        const news = card.kind === "news";
        parts.card.classList.toggle("locked", news);
        if (news) placeOverlay(parts.card, cs, W, H, W / 2, H / 2);
        else { const w = card.size * side, h = cs.w ? (w * cs.h) / cs.w : 0; placeOverlay(parts.card, cs, w, h, card.x * W, card.y * H); }
        parts.card.classList.toggle("timed-out", !!card.seconds && st.time > card.seconds);
      }
      const b = d.brand || {}, hs = ov.handles;
      parts.handles.hidden = !hs.url || !wanted("handles");
      if (!parts.handles.hidden && hs.size) {
        const k = b.handles_size / hs.size, w = Math.min(W * 0.96, hs.w * k), h = hs.h * (w / hs.w);
        placeOverlay(parts.handles, hs, w, h, b.handles_x * W, b.handles_y * H);
      }
      const logo = q(".st-logo");
      logo.hidden = !b.logo;
      if (b.logo) {
        const src = `/api/brand-assets/${b.logo}`;
        if (logo.getAttribute("src") !== src) logo.src = src;
        const m = 0.035 * side, w = b.logo_size * side, corner = b.logo_corner || "tr";
        Object.assign(logo.style, {
          width: (w / W) * 100 + "%", opacity: b.logo_opacity,
          left: corner.includes("l") ? (m / W) * 100 + "%" : "", right: corner.includes("r") ? (m / W) * 100 + "%" : "",
          top: corner.includes("t") ? (m / H) * 100 + "%" : "", bottom: corner.includes("b") ? (m / H) * 100 + "%" : "",
        });
      }
    }
    function paint() {
      if (!st.d) return;
      root.classList.toggle("landscape", st.d.aspect === "16:9");
      root.classList.toggle("square", st.d.aspect === "1:1");
      root.classList.toggle("editable", st.editable);
      drawCanvas();
      paintBoxes();
      paintCaption();
      paintTitle();
      paintOverlays();
      for (const [name, node] of Object.entries(parts)) node.classList.toggle("selected", st.selected === name);
    }

    /* ---- snapping + dragging ---- */
    function bounds(name) {
      const node = parts[name];
      if (!node || node.hidden) return null;
      const r = root.getBoundingClientRect(), b = node.getBoundingClientRect();
      if (!r.width || !b.width) return null;
      return [(b.left - r.left) / r.width, (b.top - r.top) / r.height, b.width / r.width, b.height / r.height];
    }
    function targets(except) {
      const xs = [0, 0.5, 1], ys = [0, 0.5, 1];
      for (const name of Object.keys(parts)) {
        if (name === except) continue;
        const b = bounds(name);
        if (!b) continue;
        xs.push(b[0], b[0] + b[2] / 2, b[0] + b[2]);
        ys.push(b[1], b[1] + b[3] / 2, b[1] + b[3]);
      }
      return { xs, ys };
    }
    function snap(values, lines) {
      let best = null;
      for (const v of values) for (const t of lines) {
        const gap = t - v;
        if (Math.abs(gap) <= SNAP && (!best || Math.abs(gap) < Math.abs(best.gap))) best = { gap, line: t };
      }
      return best;
    }
    const guides = { v: [...root.querySelectorAll(".st-guide.v")], h: [...root.querySelectorAll(".st-guide.h")] };
    function showGuides(xs, ys) {
      guides.v.forEach((g, i) => { g.hidden = xs[i] == null; if (xs[i] != null) g.style.left = xs[i] * 100 + "%"; });
      guides.h.forEach((g, i) => { g.hidden = ys[i] == null; if (ys[i] != null) g.style.top = ys[i] * 100 + "%"; });
    }
    const r4 = (v) => Math.round(v * 10000) / 10000;
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

    function getRect(name) {
      const v = view();
      if (name === "extra") return [...(extraArea(v) || TRIO[2])];
      return name === "game" ? [...gameArea(v)] : [...(camOutputRect(v) || [0.05, 0.05, 0.4, 0.2])];
    }
    function setRect(name, r) {
      const d = st.d;
      if (name === "game") d.game_rect = r.map(r4);
      else if (name === "extra") d.extra_rect = r.map(r4);
      else d.cam_rect = r.map(r4);
    }
    function textState(name) {
      const d = st.d;
      if (name === "caption") return { x: d.caption.x, y: d.caption.y, size: d.caption.size };
      if (name === "title") return { x: d.title.x, y: d.title.y, size: d.title.size };
      if (name === "card") return { x: d.card.x, y: d.card.y, size: d.card.size };
      if (name === "handles") return { x: d.brand.handles_x, y: d.brand.handles_y, size: d.brand.handles_size };
      return { x: d.credit_x, y: d.credit_y, size: d.credit_size };
    }
    function setText(name, s) {
      const d = st.d;
      const x = r4(clamp(s.x, 0.02, 0.98)), y = r4(clamp(s.y, 0.02, 0.98));
      const [lo, hi] = LIMITS[name];
      const size = name === "card" ? Math.round(clamp(s.size, lo, hi) * 100) / 100 : Math.round(clamp(s.size, lo, hi));
      if (name === "card") Object.assign(d.card, { x, y, size });
      else if (name === "handles") Object.assign(d.brand, { handles_x: x, handles_y: y, handles_size: size });
      else if (name === "caption") Object.assign(d.caption, { x, y, size });
      else if (name === "title") Object.assign(d.title, { x, y, size });
      else Object.assign(d, { credit_x: x, credit_y: y, credit_size: size });
    }

    function select(name) {
      st.selected = name;
      for (const [n, node] of Object.entries(parts)) node.classList.toggle("selected", n === name);
      st.onSelect(name);
    }

    function startDrag(e, name) {
      if (!st.editable || e.button > 0) return;
      const node = parts[name];
      if (node.classList.contains("locked")) return;
      e.preventDefault();
      e.stopPropagation();
      select(name);
      root.focus({ preventScroll: true });
      const corner = e.target.dataset.corner || null;
      const rect = root.getBoundingClientRect();
      const sx = e.clientX, sy = e.clientY;
      const lines = targets(name);
      const isRect = isBox(name);
      const r0 = isRect ? getRect(name) : null;
      const t0 = isRect ? null : textState(name);
      const b0 = isRect ? null : bounds(name);
      const d0 = t0 ? Math.hypot((sx - rect.left) / rect.width - t0.x, ((sy - rect.top) / rect.height - t0.y) * rect.height / rect.width) || 0.01 : 0;
      node.setPointerCapture(e.pointerId);
      root.classList.add("dragging");
      const move = (ev) => {
        const dx = (ev.clientX - sx) / rect.width, dy = (ev.clientY - sy) / rect.height;
        const free = ev.altKey; // hold Alt to move without snapping
        let gx = [], gy = [];
        if (isRect) {
          let [x, y, w, h] = r0;
          const min = 0.06;
          if (!corner) {
            x = clamp(x + dx, 0, 1 - w); y = clamp(y + dy, 0, 1 - h);
            const a = !free && snap([x, x + w / 2, x + w], lines.xs), b = !free && snap([y, y + h / 2, y + h], lines.ys);
            if (a) { x = clamp(x + a.gap, 0, 1 - w); gx.push(a.line); }
            if (b) { y = clamp(y + b.gap, 0, 1 - h); gy.push(b.line); }
          } else {
            let left = x, top = y, right = x + w, bottom = y + h;
            if (corner.includes("w")) left = clamp(left + dx, 0, right - min); else right = clamp(right + dx, left + min, 1);
            if (corner.includes("n")) top = clamp(top + dy, 0, bottom - min); else bottom = clamp(bottom + dy, top + min, 1);
            const ex = corner.includes("w") ? left : right, ey = corner.includes("n") ? top : bottom;
            const a = !free && snap([ex], lines.xs), b = !free && snap([ey], lines.ys);
            if (a) { if (corner.includes("w")) left = clamp(a.line, 0, right - min); else right = clamp(a.line, left + min, 1); gx.push(a.line); }
            if (b) { if (corner.includes("n")) top = clamp(b.line, 0, bottom - min); else bottom = clamp(b.line, top + min, 1); gy.push(b.line); }
            x = left; y = top; w = right - left; h = bottom - top;
          }
          setRect(name, [x, y, w, h]);
        } else if (!corner) {
          let x = t0.x + dx, y = t0.y + dy;
          const bw = b0 ? b0[2] : 0, bh = b0 ? b0[3] : 0;
          const a = !free && snap([x - bw / 2, x, x + bw / 2], lines.xs), b = !free && snap([y - bh / 2, y, y + bh / 2], lines.ys);
          if (a) { x += a.gap; gx.push(a.line); }
          if (b) { y += b.gap; gy.push(b.line); }
          setText(name, { ...t0, x, y });
        } else {
          const px = (ev.clientX - rect.left) / rect.width, py = (ev.clientY - rect.top) / rect.height;
          const dist = Math.hypot(px - t0.x, (py - t0.y) * rect.height / rect.width);
          setText(name, { ...t0, size: t0.size * (dist / d0) });
        }
        showGuides(gx, gy);
        paint();
        st.onChange(st.d, name);
      };
      const up = () => {
        node.removeEventListener("pointermove", move);
        node.removeEventListener("pointerup", up);
        node.removeEventListener("pointercancel", up);
        root.classList.remove("dragging");
        showGuides([], []);
        st.onChange(st.d, name, true);
      };
      node.addEventListener("pointermove", move);
      node.addEventListener("pointerup", up);
      node.addEventListener("pointercancel", up);
    }
    for (const [name, node] of Object.entries(parts)) node.addEventListener("pointerdown", (e) => startDrag(e, name));
    root.addEventListener("pointerdown", (e) => { if (e.target === root || e.target === canvas) select(null); });
    root.addEventListener("keydown", (e) => {
      const name = st.selected;
      if (!name || !st.editable || !/^Arrow/.test(e.key)) return;
      e.preventDefault();
      const step = e.shiftKey ? 0.02 : 0.004;
      const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
      const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
      if (isBox(name)) {
        const [x, y, w, h] = getRect(name);
        setRect(name, [clamp(x + dx, 0, 1 - w), clamp(y + dy, 0, 1 - h), w, h]);
      } else {
        const t = textState(name);
        setText(name, { ...t, x: t.x + dx, y: t.y + dy });
      }
      paint();
      st.onChange(st.d, name, true);
    });

    let ro = null;
    if (window.ResizeObserver) { ro = new ResizeObserver(() => paint()); ro.observe(root); }

    return Object.assign(st, {
      paint,
      select,
      setDesign(d) { st.d = d; paint(); },
      setMedia(m) { st.media = m; paint(); },
      setSafe(on) { st.safe = !!on; paint(); },
      setWords(words) { st.words = words || []; },
      setTime(t) { st.time = t; },
      tick(t) {
        st.time = t;
        if (!st.d) return;
        paintCaption();
        const c = st.d.card;
        parts.card.classList.toggle("timed-out", !!(c && c.seconds && t > c.seconds));
      },
      setTitle(text) { st.title = text || ""; },
      destroy() { if (ro) ro.disconnect(); root.remove(); },
    });
  }

  /* Sample words that loop, so caption styles can be seen without a video. */
  const SAMPLE = "This is insane bro haha the cops are chasing us right now no way we got the money".split(" ");
  function sampleWords() {
    let t = 0;
    const words = SAMPLE.map((text, i) => {
      const len = 0.32 + (text.length > 4 ? 0.12 : 0);
      const w = { text, start: t, end: t + len };
      t += len + (i === 3 || i === 9 ? 0.7 : 0.04);
      return w;
    });
    words.sample = true; // lets the preview play the sample shout
    return words;
  }

  window.ClipStage = {
    create, effectiveLayout, gameRegion, gameArea, camOutputRect, extraArea, chunks, sampleWords, CANVAS, CAM_LAYOUTS,
    setEmoji(table) { EMOJI = table || EMOJI; },
  };
})();
