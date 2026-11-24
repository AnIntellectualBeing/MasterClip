"use strict";
/* Campaigns: feed cards, "Clip for this", and My submissions with an earnings estimate. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const CS = () => window.ClipStudio;
  const money = (n) => (n == null ? "n/a" : "$" + Number(n).toFixed(2));
  const C = { data: null, tab: "list", adding: false };

  function h(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid) node.append(kid);
    return node;
  }
  const link = (href, text, cls) => h("a", { class: cls || "ghost", href, target: "_blank", rel: "noopener", text });

  function campaignCard(c) {
    const poster = h("div", { class: "poster" }, c.poster ? null : "🎬");
    if (c.poster) poster.style.backgroundImage = `url("${String(c.poster).replace(/"/g, "%22")}")`;
    const facts = [];
    if (c.budget) facts.push(h("span", { text: `Budget ${c.budget}` }));
    if (c.min_views) facts.push(h("span", { text: `From ${Number(c.min_views).toLocaleString()} views` }));
    if (c.clip_len) facts.push(h("span", { text: `${c.clip_len[0]}-${c.clip_len[1]} s clips` }));
    if (c.ends) facts.push(h("span", { text: `Ends ${c.ends}` }));
    for (const p of c.platforms || []) facts.push(h("span", { text: p }));
    return h("div", { class: "camp-card" }, poster, h("div", { class: "inner" },
      h("h4", { text: c.title }),
      c.brand ? h("div", { class: "muted", text: c.brand }) : null,
      h("div", { class: "camp-pay", text: c.payout_per_1k == null ? "Rate on the campaign page" : `${money(c.payout_per_1k)} per 1K views` }),
      h("div", { class: "camp-chips" }, facts),
      (c.rules || []).length ? h("ul", {}, c.rules.slice(0, 4).map((r) => h("li", { text: r }))) : null,
      h("div", { class: "btns" },
        c.url ? link(c.url, "Open campaign page") : null,
        h("button", { class: "primary", type: "button", text: "Clip for this", onclick: () => clipFor(c) }))));
  }

  function platformCard(p) {
    return h("div", { class: "camp-card" }, h("div", { class: "inner" },
      h("h4", { text: p.name }),
      p.pays ? h("div", { text: p.pays }) : h("div", { class: "muted", text: "Check their page for rates." }),
      p.payout_method ? h("div", { class: "muted", text: `Paid by ${p.payout_method}` }) : null,
      p.min_payout != null ? h("div", { class: "muted", text: `Minimum payout ${p.min_payout}` }) : null,
      p.note ? h("div", { class: "muted", text: p.note }) : null,
      h("div", { class: "btns" }, link(p.url, "Open site"))));
  }

  function paintList() {
    const d = C.data;
    $("#campEmpty").hidden = d.campaigns.length > 0;
    $("#campCards").replaceChildren(...d.campaigns.map(campaignCard));
    $("#campPlatforms").replaceChildren(...d.platforms.map(platformCard));
    const src = { live: "Up to date.", cache: "Offline. Showing the last list we saved.", bundled: "Offline. Showing the list that came with the app." }[d.source] || "";
    $("#campSrc").textContent = d.updated ? `List updated ${d.updated}. ${src}` : src;
  }

  function addForm() {
    const pick = h("select", { "aria-label": "Campaign" }, h("option", { value: "", text: "No campaign" }),
      ...C.data.campaigns.map((c) => h("option", { value: c.id, text: c.title })));
    const name = h("input", { type: "text", placeholder: "Clip name", "aria-label": "Clip name" });
    const url = h("input", { type: "url", placeholder: "Link to your post (https://...)", "aria-label": "Posted link" });
    const views = h("input", { type: "number", min: "0", placeholder: "Views so far", "aria-label": "Views" });
    return h("div", { class: "camp-form" }, pick, name, url, views,
      h("div", { class: "row-between", style: "margin-top:0" },
        h("button", { class: "ghost", type: "button", text: "Cancel", onclick: () => { C.adding = false; paintMine(); } }),
        h("button", { class: "primary", type: "button", text: "Save", onclick: async () => {
          await submit({ campaign_id: pick.value, clip_name: name.value, posted_url: url.value, views: Number(views.value) || 0 });
        } })));
  }

  async function submit(body) {
    try {
      const r = await CS().api("/api/campaigns/submissions", { method: "POST", body });
      Object.assign(C.data, { submissions: r.submissions, total_estimate: r.total_estimate });
      C.adding = false;
      paintMine();
    } catch (err) { CS().notice(err.message); }
  }

  function row(s) {
    const views = h("input", { type: "number", min: "0", value: String(s.views || 0), "aria-label": "Views" });
    const save = async () => {
      try {
        const r = await CS().api(`/api/campaigns/submissions/${s.id}`, { method: "PUT", body: { views: Math.max(0, Number(views.value) || 0) } });
        Object.assign(C.data, { submissions: r.submissions, total_estimate: r.total_estimate });
        paintMine();
      } catch (err) { CS().notice(err.message); }
    };
    views.addEventListener("change", save);
    return h("div", { class: "camp-row" },
      h("div", {}, h("strong", { text: s.clip_name || "Untitled clip" }),
        h("small", { text: [s.campaign_title, s.posted_at].filter(Boolean).join(" · ") }),
        s.posted_url ? link(s.posted_url, "Open post", "ghost small") : null),
      views,
      h("div", { class: "est", title: "Estimate", text: s.estimate == null ? "no rate" : money(s.estimate) }),
      h("button", { class: "ghost small", type: "button", "aria-label": "Remove", text: "✕", onclick: async () => {
        try {
          const r = await CS().api(`/api/campaigns/submissions/${s.id}`, { method: "DELETE" });
          Object.assign(C.data, { submissions: r.submissions, total_estimate: r.total_estimate });
          paintMine();
        } catch (err) { CS().notice(err.message); }
      } }));
  }

  function paintMine() {
    const d = C.data;
    $("#campTotal").textContent = money(d.total_estimate);
    $("#campMineEmpty").hidden = d.submissions.length > 0 || C.adding;
    const rows = d.submissions.map(row);
    $("#campRows").replaceChildren(...(C.adding ? [addForm()] : []), ...rows);
    $("#campAdd").hidden = C.adding;
  }

  function showTab(tab) {
    C.tab = tab;
    document.querySelectorAll("#campDlg .tab[data-camp]").forEach((t) => t.classList.toggle("active", t.dataset.camp === tab));
    $("#campList").hidden = tab !== "list";
    $("#campMine").hidden = tab !== "mine";
    if (C.data) (tab === "list" ? paintList : paintMine)();
  }

  async function open(tab) {
    $("#campDlg").hidden = false;
    showTab(tab || C.tab);
    if (!C.data) $("#campSrc").textContent = "Loading…";
    try {
      C.data = await CS().api("/api/campaigns");
      showTab(C.tab);
    } catch (err) { CS().notice(err.message); }
  }
  const close = () => { $("#campDlg").hidden = true; };

  /* ---- Clip for this ---- */
  function lengthFor(range) {
    const [lo, hi] = range && range.length === 2 ? range : [15, 60];
    const want = Math.min(Math.max(30, lo), hi);
    return Math.max(5, Math.min(180, Math.round(want)));
  }

  function clipFor(c) {
    const st = CS().state;
    st.campaign = { id: c.id, title: c.title };
    close();
    CS().go(1);
    const src = (c.sources || []).find((s) => /^https?:\/\//i.test(s.link || ""));
    if (src) {
      document.querySelector('.tab[data-tab="link"]').click();
      $("#linkInput").value = src.link;
      CS().pickUrl(src.link);
    } else {
      const name = ((c.sources || [])[0] || {}).name || c.brand || c.title;
      document.querySelector('.tab[data-tab="find"]').click();
      $("#searchInput").value = name;
      CS().runSearch(name);
    }
    const len = lengthFor(c.clip_len);
    if (document.querySelector(`#lengthSeg [data-len="${len}"]`)) CS().setLength(len);
    else {
      document.querySelector('#lengthSeg [data-len="custom"]').click();
      $("#customLength").value = len;
      $("#customLength").dispatchEvent(new Event("input"));
    }
    if (window.ClipDesigner && window.ClipDesigner.ensureCaptions) window.ClipDesigner.ensureCaptions();
    banner();
  }

  function banner() {
    const c = CS().state.campaign;
    $("#campBanner").hidden = !c;
    if (c) $("#campBannerText").textContent = `Campaign: ${c.title}`;
  }
  function clear() { CS().state.campaign = null; banner(); }

  $("#openCampaigns").addEventListener("click", () => open());
  $("#campClose").addEventListener("click", close);
  $("#campDlg").addEventListener("click", (e) => { if (e.target.id === "campDlg") close(); });
  $("#campBannerX").addEventListener("click", clear);
  $("#campAdd").addEventListener("click", () => { if (!C.data) return; C.adding = true; paintMine(); });
  document.querySelectorAll("#campDlg .tab[data-camp]").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.camp)));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#campDlg").hidden) close(); });

  window.ClipCampaigns = { open, clear, clipFor, lengthFor };
})();
