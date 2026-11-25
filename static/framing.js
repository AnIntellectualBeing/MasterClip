"use strict";
/* "Set it manually" (Advanced): pick one of the layouts, then drag coloured boxes over a frame of the
   stream to choose exactly what shows (Gameplay, Streamer cam, an extra part). The live preview next
   to it (stage.js) shows the result. Layout patches come from clipstudio/design.py (LAYOUTS);
   cornerRect / layoutId / patch mirror corner_rect / layout_id / layout_patch there: keep in step.
   Used in the clip editor (editor.js) and on the template step (designer.js). */
(function () {
  const BOXES = {
    game: { key: "game_roi", label: "Gameplay", color: "#2FD46B" },
    cam: { key: "cam_roi", label: "Streamer cam", color: "#FF9F1C" },
    extra: { key: "extra_roi", label: "Extra part", color: "#4DA3FF" },
  };
  const CANVAS = { "9:16": [1080, 1920], "16:9": [1920, 1080], "1:1": [1080, 1080] };
  const RESET = { cam_rect: null, game_rect: null, extra_rect: null, hide_cam: false, cam_shape: "frame" };
  const r4 = (v) => Math.round(v * 10000) / 10000;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function cornerRect(aspect, corner, size, square) {
    const [W, H] = CANVAS[aspect] || CANVAS["9:16"];
    const m = 0.04;
    let w, h;
    if (aspect === "16:9") { h = size; w = h * H / W * (square ? 1 : 16 / 9); }
    else { w = size; h = w * W / H * (square ? 1 : 9 / 16); }
    w = Math.min(w, 1 - 2 * m); h = Math.min(h, 1 - 2 * m);
    const mx = m * (aspect === "16:9" ? H / W : 1), my = m * (aspect !== "16:9" ? W / H : 1);
    const x = corner === "tl" || corner === "bl" ? mx : 1 - mx - w;
    let y = corner === "tl" || corner === "tr" ? my : 1 - my - h;
    if (aspect === "9:16") y = corner === "tl" || corner === "tr" ? 0.16 : 1 - 0.05 - h;
    return [x, y, w, h].map(r4);
  }
  function layoutId(d) {
    if (d.layout === "full") return d.hide_cam ? "game_only" : d.fill === "blur" ? "centred" : "full";
    if (d.layout === "stack") return d.cam_side === "top" ? "cam_top" : "cam_bottom";
    if (d.layout === "corner") return d.cam_shape === "circle" ? "circle" : "pip";
    return { side: d.extra_rect ? "trio" : "side", trio: "trio", split: "halves", meme: "topbar" }[d.layout] || "full";
  }
  function patchFor(item, aspect) {
    const p = { ...(aspect !== "9:16" && item.long ? item.long : item.short) };
    if (aspect !== "9:16" && p.layout === "trio") p.layout = "side";
    return { ...RESET, ...JSON.parse(JSON.stringify(p)) };
  }
  function cornerOf(r) {
    if (!r) return "tl";
    const cx = r[0] + r[2] / 2, cy = r[1] + r[3] / 2;
    return (cy < 0.5 ? "t" : "b") + (cx < 0.5 ? "l" : "r");
  }

  /* A tiny drawing of a layout for its chip (same maths as the preview). */
  function icon(item, aspect) {
    const S = window.ClipStage;
    const d = { aspect, layout: "full", fill: "zoom", cam_side: "top", split: 0.4, meme_bar: 0.24, title: { show: true }, ...patchFor(item, aspect), cam_roi: [0.7, 0.05, 0.28, 0.28] };
    const box = document.createElement("span");
    box.className = "fr-ico " + (aspect === "16:9" ? "tv" : "phone");
    const add = (cls, r) => {
      const i = document.createElement("i");
      i.className = cls;
      i.style.cssText = `left:${r[0] * 100}%;top:${r[1] * 100}%;width:${r[2] * 100}%;height:${r[3] * 100}%`;
      box.append(i);
    };
    const l = S.effectiveLayout(d);
    if (d.bg_blur) box.classList.add("blur");
    if (l === "meme") add("bar", [0, 0, 1, d.meme_bar]);
    if (l === "split") { add("g", [0, 0, 1, 0.5]); add("g alt", [0, 0.5, 1, 0.5]); return box; }
    if (l === "full" && d.fill === "blur") { add("g", aspect === "9:16" ? [0, 0.36, 1, 0.28] : [0, 0, 1, 1]); box.classList.add("blur"); }
    else add("g", S.gameArea(d));
    if (d.hide_cam) add("x", [0.62, 0.06, 0.3, 0.14]);
    const er = S.extraArea(d);
    if (er) add("e", er);
    const cr = S.camOutputRect(d);
    if (cr) add("c" + (d.cam_shape === "circle" ? " circle" : d.cam_shape === "round" ? " round" : ""), cr);
    return box;
  }

  function mount(host, opts) {
    const ctx = opts.context || "clip";
    const root = document.createElement("details");
    root.className = "fr";
    root.innerHTML = `
      <summary><span class="fr-sum"><b>Set it manually</b><small>Advanced · choose exactly what shows and where</small></span></summary>
      <div class="fr-body">
        <div class="field"><span>Layout</span><div class="fr-chips" role="radiogroup" aria-label="Layout"></div>
          <p class="hint fr-hint"></p></div>
        <div class="field"><span>What to show from the stream</span>
          <div class="fr-src"><canvas></canvas><p class="fr-empty" hidden>A picture of the video shows here once it loads.</p></div>
          <p class="hint">Drag a coloured box to move it, pull a corner to resize, or scroll on it to zoom in and out.</p>
          <div class="fr-row"><button type="button" class="ghost small" data-act="addcam">+ Mark the streamer cam</button>
            <button type="button" class="ghost small" data-act="auto" title="Put the boxes back where MasterClip found them">↺ Reset to auto</button></div>
        </div>
        <label class="field fr-split"><span>Cam height <b></b></span><input type="range" min="0.2" max="0.7" step="0.01" aria-label="Cam height">
          <small class="hint">How much of the screen the streamer takes.</small></label>
        <div class="field fr-corner"><span>Corner</span>
          <div class="seg small"><button type="button" data-c="tl">↖ Top left</button><button type="button" data-c="tr">↗ Top right</button><button type="button" data-c="bl">↙ Bottom left</button><button type="button" data-c="br">↘ Bottom right</button></div></div>
        <label class="field fr-size"><span>Cam size <b></b></span><input type="range" min="0.18" max="0.7" step="0.01" aria-label="Cam size">
          <small class="hint">Bigger or smaller cam, it stays in its corner.</small></label>
        <div class="switch-row col">
          <label class="switch fr-round"><input type="checkbox" data-opt="round"><span></span> Round corners on the cam</label>
          <label class="switch"><input type="checkbox" data-opt="blur"><span></span> Blurred stream behind empty space</label>
          <label class="switch"><input type="checkbox" data-opt="safe"><span></span> Show where app buttons cover the video (preview only)</label>
          ${ctx === "clip" ? ""
            : `<label class="switch"><input type="checkbox" data-opt="keep"><span></span> Same boxes for every video (saved with your template)</label>`}
        </div>
      </div>`;
    host.replaceChildren(root);
    const q = (s) => root.querySelector(s);
    const qa = (s) => [...root.querySelectorAll(s)];
    const state = { safe: false, drag: null };
    const src = q(".fr-src"), canvas = q(".fr-src canvas");
    const nodes = {};
    for (const [name, b] of Object.entries(BOXES)) {
      const n = document.createElement("div");
      n.className = "fr-box";
      n.dataset.box = name;
      n.style.setProperty("--c", b.color);
      n.innerHTML = `<span class="fr-tag">${esc(b.label)}</span>` + ["nw", "ne", "sw", "se"].map((c) => `<i class="fr-h ${c}" data-corner="${c}"></i>`).join("");
      n.title = `${b.label}: drag to move, pull a corner to resize, scroll to zoom`;
      src.append(n);
      nodes[name] = n;
    }
    const d = () => opts.get();
    const changed = () => { opts.changed(); sync(); };

    function chips() {
      const aspect = d().aspect;
      q(".fr-chips").replaceChildren(...opts.layouts().map((item) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "fr-chip";
        b.dataset.id = item.id;
        b.setAttribute("role", "radio");
        b.title = item.hint;
        b.append(icon(item, aspect), Object.assign(document.createElement("small"), { textContent: item.name }));
        b.addEventListener("click", () => pick(item));
        return b;
      }));
      chips.aspect = aspect;
    }
    function pick(item) {
      const des = d();
      if (item.needs_cam && !des.cam_roi && ctx === "clip") {
        des.cam_roi = (opts.autoCam && opts.autoCam()) || [0.7, 0.05, 0.28, 0.28];
        if (window.ClipStudio) window.ClipStudio.toast("Move the orange box over the streamer's cam");
      }
      Object.assign(des, patchFor(item, des.aspect));
      if (item.id === "trio" && !des.extra_roi) des.extra_roi = [0.02, 0.02, 0.26, 0.3];
      changed();
    }

    /* --- source picture + boxes --- */
    function visible() {
      const des = d(), l = window.ClipStage.effectiveLayout(des);
      return {
        game: !["split"].includes(l),
        cam: !!des.cam_roi,
        extra: !!window.ClipStage.extraArea(des),
      };
    }
    function rectOf(name) {
      const des = d();
      if (name === "game") return des.game_roi || [0, 0, 1, 1];
      if (name === "extra") return des.extra_roi || [0.3, 0.3, 0.4, 0.4];
      return des.cam_roi;
    }
    function setRect(name, r) {
      const des = d();
      des[BOXES[name].key] = r.map(r4);
      if (ctx === "template" && name !== "game") des.keep_boxes = true; // drawn by hand = keep them
    }
    function paintSource() {
      if (!root.open) return;
      const m = opts.media && opts.media();
      const w = m ? m.videoWidth || m.naturalWidth || 0 : 0, h = m ? m.videoHeight || m.naturalHeight || 0 : 0;
      q(".fr-empty").hidden = !!w;
      src.style.aspectRatio = w && h ? `${w} / ${h}` : "16 / 9";
      if (w) {
        const k = Math.min(1, 640 / w);
        const cw = Math.round(w * k), chh = Math.round(h * k);
        if (canvas.width !== cw) { canvas.width = cw; canvas.height = chh; }
        try { canvas.getContext("2d").drawImage(m, 0, 0, cw, chh); } catch { /* not ready yet */ }
      }
      const show = visible();
      for (const [name, n] of Object.entries(nodes)) {
        n.hidden = !show[name];
        const r = show[name] && rectOf(name);
        if (r) Object.assign(n.style, { left: r[0] * 100 + "%", top: r[1] * 100 + "%", width: r[2] * 100 + "%", height: r[3] * 100 + "%" });
      }
    }
    src.addEventListener("pointerdown", (e) => {
      const n = e.target.closest(".fr-box");
      if (!n || e.button > 0) return;
      e.preventDefault();
      const name = n.dataset.box, corner = e.target.dataset.corner || null;
      const area = src.getBoundingClientRect(), r0 = [...rectOf(name)];
      const sx = e.clientX, sy = e.clientY, min = 0.04;
      n.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const dx = (ev.clientX - sx) / area.width, dy = (ev.clientY - sy) / area.height;
        let [x, y, w, h] = r0;
        if (!corner) { x = clamp(x + dx, 0, 1 - w); y = clamp(y + dy, 0, 1 - h); }
        else {
          let l = x, t = y, rr = x + w, b = y + h;
          if (corner.includes("w")) l = clamp(l + dx, 0, rr - min); else rr = clamp(rr + dx, l + min, 1);
          if (corner.includes("n")) t = clamp(t + dy, 0, b - min); else b = clamp(b + dy, t + min, 1);
          x = l; y = t; w = rr - l; h = b - t;
        }
        setRect(name, [x, y, w, h]);
        paintSource();
        opts.changed();
      };
      const up = () => { n.removeEventListener("pointermove", move); n.removeEventListener("pointerup", up); n.removeEventListener("pointercancel", up); sync(); };
      n.addEventListener("pointermove", move);
      n.addEventListener("pointerup", up);
      n.addEventListener("pointercancel", up);
    });
    src.addEventListener("wheel", (e) => {
      const n = e.target.closest(".fr-box");
      if (!n) return;
      e.preventDefault();
      const name = n.dataset.box;
      const [x, y, w, h] = rectOf(name);
      const f = e.deltaY < 0 ? 0.94 : 1 / 0.94; // wheel up = zoom in = a smaller box
      const nw = clamp(w * f, 0.04, 1), nh = clamp(h * f, 0.04, 1);
      const cx = x + w / 2, cy = y + h / 2;
      setRect(name, [clamp(cx - nw / 2, 0, 1 - nw), clamp(cy - nh / 2, 0, 1 - nh), nw, nh]);
      paintSource();
      opts.changed();
    }, { passive: false });

    /* --- controls --- */
    function markCam() {
      const des = d();
      des.cam_roi = des.cam_roi || (opts.autoCam && opts.autoCam()) || [0.7, 0.05, 0.28, 0.28];
      if (ctx === "template") des.keep_boxes = true;
      if (des.layout === "full" && !des.hide_cam) Object.assign(des, patchFor(opts.layouts().find((x) => x.id === "cam_top"), des.aspect));
      changed();
    }
    q('[data-act="addcam"]').addEventListener("click", markCam);
    q('[data-act="auto"]').addEventListener("click", () => {
      const des = d();
      des.cam_roi = (opts.autoCam && opts.autoCam()) || null;
      des.game_roi = null; des.extra_roi = null;
      des.cam_rect = null; des.game_rect = null; des.extra_rect = null;
      des.keep_boxes = false;
      const item = opts.layouts().find((x) => x.id === layoutId(des));
      if (item) Object.assign(des, patchFor(item, des.aspect));
      if (window.ClipStudio) window.ClipStudio.toast(des.cam_roi ? "Boxes back where MasterClip found them" : "No cam found in this video: full gameplay");
      changed();
    });
    q(".fr-split input").addEventListener("input", (e) => { const des = d(); des.split = Number(e.target.value); des.cam_rect = null; des.game_rect = null; opts.changed(); sync(); });
    qa(".fr-corner button").forEach((b) => b.addEventListener("click", () => {
      const des = d();
      des.cam_rect = cornerRect(des.aspect, b.dataset.c, camSize(des), des.cam_shape === "circle");
      changed();
    }));
    function camSize(des) {
      const r = window.ClipStage.camOutputRect(des) || [0, 0, 0.42, 0.2];
      return des.aspect === "16:9" ? r[3] : r[2];
    }
    q(".fr-size input").addEventListener("input", (e) => {
      const des = d();
      des.cam_rect = cornerRect(des.aspect, cornerOf(window.ClipStage.camOutputRect(des)), Number(e.target.value), des.cam_shape === "circle");
      opts.changed(); sync();
    });
    root.addEventListener("change", (e) => {
      const opt = e.target.dataset.opt;
      if (!opt) return;
      const des = d(), on = e.target.checked;
      if (opt === "round") des.cam_shape = on ? "round" : "frame";
      else if (opt === "blur") des.bg_blur = on;
      else if (opt === "keep") des.keep_boxes = on;
      else if (opt === "safe") { state.safe = on; if (opts.stage && opts.stage()) opts.stage().setSafe(on); return; }
      changed();
    });
    root.addEventListener("toggle", () => paintSource());

    function sync() {
      const des = d();
      if (!des) return;
      if (chips.aspect !== des.aspect) chips();
      const id = layoutId(des), item = opts.layouts().find((x) => x.id === id) || {};
      qa(".fr-chip").forEach((b) => { const on = b.dataset.id === id; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
      q(".fr-hint").textContent = item.hint || "";
      const l = window.ClipStage.effectiveLayout(des);
      q(".fr-split").hidden = l !== "stack" || !!des.cam_rect;
      q(".fr-split input").value = des.split; q(".fr-split b").textContent = Math.round(des.split * 100) + "%";
      const corner = l === "corner";
      q(".fr-corner").hidden = !corner; q(".fr-size").hidden = !corner;
      if (corner) {
        const c = cornerOf(window.ClipStage.camOutputRect(des));
        qa(".fr-corner button").forEach((b) => b.classList.toggle("on", b.dataset.c === c));
        const size = camSize(des);
        q(".fr-size input").value = size; q(".fr-size b").textContent = Math.round(size * 100) + "%";
      }
      q(".fr-round").hidden = !(corner && des.cam_shape !== "circle");
      q('[data-opt="round"]').checked = des.cam_shape === "round";
      q('[data-opt="blur"]').checked = !!des.bg_blur;
      q('[data-opt="safe"]').checked = state.safe;
      const keep = q('[data-opt="keep"]');
      if (keep) keep.checked = !!des.keep_boxes;
      q('[data-act="addcam"]').hidden = !!des.cam_roi;
      root.classList.toggle("has-cam", !!des.cam_roi);
      paintSource();
    }
    chips();
    return {
      sync, paintSource,
      reset() { root.open = false; sync(); },
      /* "Mark the streamer cam" from outside (clip editor's Streamer cam field): open the editor with the
         orange box on the frame, ready to drag. */
      markCam() { root.open = true; markCam(); root.scrollIntoView({ block: "nearest", behavior: "smooth" }); },
      get open() { return root.open; },
    };
  }

  window.ClipFraming = { mount, cornerRect, layoutId };
})();
