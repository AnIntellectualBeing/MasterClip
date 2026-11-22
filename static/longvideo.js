"use strict";
/* "Make a YouTube video": compilation or full-length, then preview, description, post. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const CS = () => window.ClipStudio;
  const opts = { kind: "compilation", moments: "clips", count: 10, length: 45, order: "time" };

  function seg(id, key, after) {
    $$(`#${id} button`).forEach((b) => b.addEventListener("click", () => {
      $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
      opts[key] = isNaN(Number(b.dataset.v)) ? b.dataset.v : Number(b.dataset.v);
      if (after) after();
      sync();
    }));
  }
  seg("longKind", "kind");
  $$("#longKind .long-kind").forEach((b) => b.addEventListener("click", () => {
    $$("#longKind .long-kind").forEach((x) => x.classList.toggle("on", x === b));
  }));
  seg("longMoments", "moments");
  seg("longLen", "length");
  seg("longOrder", "order");
  $("#longCount").addEventListener("input", (e) => { opts.count = Number(e.target.value); $("#longCountVal").textContent = opts.count; sync(); });

  function sync() {
    const job = CS().state.job;
    const clips = (job && job.clips) || [];
    const comp = opts.kind === "compilation";
    $("#longCompOpts").hidden = !comp;
    $("#longIntroWrap").hidden = !comp;
    const best = opts.moments === "best" || !clips.length;
    $("#longCountField").hidden = !best;
    $("#longLenField").hidden = !best;
    $('#longMoments [data-v="clips"]').textContent = `Use my ${clips.length} clip${clips.length === 1 ? "" : "s"}`;
    const who = job && job.channel ? job.channel : "";
    $("#longTitle").placeholder = comp ? (who ? `${who} best moments` : "Best moments") : ((job && job.title) || "Full stream");
    if (comp) {
      const n = best ? opts.count : clips.length;
      const secs = best ? n * opts.length : clips.reduce((t, c) => t + (c.end - c.start), 0);
      $("#longHint").textContent = `About ${Math.max(1, Math.round((secs + 3) / 60))} min, ${n} chapters`;
    } else {
      $("#longHint").textContent = job ? `${CS().clock(job.duration)} long. Big videos take a few minutes.` : "";
    }
  }

  $("#makeLong").addEventListener("click", async () => {
    const job = CS().state.job;
    if (!job) return;
    const body = {
      kind: opts.kind, moments: opts.moments, count: opts.count, length: opts.length, order: opts.order,
      captions: $("#longCaptions").checked, intro: $("#longIntro").checked, title: $("#longTitle").value.trim(),
    };
    try {
      await CS().api(`/api/jobs/${job.id}/long`, { method: "POST", body });
      CS().toast("Making your YouTube video…");
      $("#longTitle").value = "";
      CS().watchJob(job.id);
    } catch (err) { CS().notice(err.message); }
  });

  function paint(job) {
    sync();
    const videos = (job.long_videos || []).map((v, i) => ({ ...v, i })).reverse();
    const failed = job.error && /long video/i.test(job.message || "");
    $("#longHint").textContent = failed ? `That didn't work: ${job.error}` : $("#longHint").textContent;
    $("#longVideos").replaceChildren(...videos.map((v) => {
      const box = CS().el("textarea", { readonly: true, "aria-label": "YouTube description" });
      box.value = v.description;
      return CS().el("div", { class: "long-video", "data-clip-index": `long-${v.i}` },
        CS().el("video", { src: v.url, controls: true, preload: "metadata", playsinline: true }),
        CS().el("div", { class: "long-meta" },
          CS().el("strong", { text: v.title }),
          CS().el("span", { class: "muted", text: `${v.kind === "full" ? "Full-length" : "Compilation"} · ${CS().clock(v.duration)} · ${v.chapters.length} chapters` }),
          box,
          CS().el("div", { class: "clip-posting", "aria-live": "polite" }),
          CS().el("div", { class: "long-actions" },
            CS().el("button", { class: "ghost small", type: "button", text: "📋 Copy description", onclick: async () => {
              try { await navigator.clipboard.writeText(v.description); CS().toast("Description copied"); } catch { box.select(); }
            } }),
            CS().el("a", { class: "ghost small", href: v.url, download: `${v.title}.mp4`, text: "Download" }),
            CS().el("button", { class: "primary small manual-post", type: "button", text: "📤 Post to YouTube", onclick: () => postLong(job.id, v.i) }))));
    }));
    if (window.ClipPosting) window.ClipPosting.paintOutput();
  }

  function postLong(jobId, index) {
    // Same "when should it go out?" choice as the clips (now / my time / daily times).
    window.ClipPosting.schedule(jobId, null, { long_index: index });
  }

  window.ClipLong = { paint };
})();
