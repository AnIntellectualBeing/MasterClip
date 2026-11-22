"use strict";
/* Posting: setup wizard (Zernio key, accounts, times, caption, auto-DM) and the queue. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const CS = () => window.ClipStudio;
  const P = { status: null, accounts: [], items: [], poll: 0 };

  const api = (...a) => CS().api(...a);
  const toast = (m) => CS().toast(m);
  const el = (...a) => CS().el(...a);

  /* ---- open / close ---- */
  function open(tab) {
    $("#posting").hidden = false;
    document.body.style.overflow = "hidden";
    showTab(tab || (P.status && P.status.ready ? "queue" : "setup"));
    load();
  }
  function close() {
    $("#posting").hidden = true;
    document.body.style.overflow = "";
    clearInterval(P.poll);
  }
  function showTab(name) {
    $$(".tab[data-ptab]").forEach((t) => t.classList.toggle("active", t.dataset.ptab === name));
    $("#ptab-setup").hidden = name !== "setup";
    $("#ptab-queue").hidden = name !== "queue";
    if (name === "queue") loadQueue(true);
  }
  $("#openPosting").addEventListener("click", () => open());
  $("#postingClose").addEventListener("click", close);
  $$(".tab[data-ptab]").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.ptab)));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#posting").hidden) close(); });

  /* ---- status + setup ---- */
  async function load() {
    try {
      P.status = await api("/api/post/status");
    } catch (err) { toast(err.message); return; }
    paintSetup();
    if (P.status.has_key) loadAccounts();
  }
  function paintSetup() {
    const st = P.status, s = st.settings;
    $("#keyEmpty").hidden = st.has_key;
    $("#keySet").hidden = !st.has_key;
    $("#keyHint").textContent = st.key_hint ? `(ending ${st.key_hint})` : "";
    $("#tick1").hidden = !st.has_key;
    $("#pstep2").classList.toggle("locked", !st.has_key);
    $("#pstep3").classList.toggle("locked", !st.has_key);
    $("#postingDot").hidden = st.ready;
    // times
    seg("perDay", s.per_day);
    const tz = $("#tz");
    if (!tz.options.length) tz.replaceChildren(...st.timezones.map((z) => new Option(z.replace("_", " "), z)));
    if (![...tz.options].some((o) => o.value === s.timezone)) tz.append(new Option(s.timezone, s.timezone));
    tz.value = s.timezone;
    paintSlots(s);
    $("#captionTpl").value = s.caption;
    $("#hashtags").value = s.hashtags;
    $("#dmOn").checked = s.dm.on;
    $("#dmKeyword").value = s.dm.keyword;
    $("#dmMessage").value = s.dm.message;
    $("#dmReply").value = s.dm.reply;
    $("#dmFields").hidden = !s.dm.on;
    paintExtra();
    preview();
  }
  // ---- more Zernio keys: clips take turns across accounts ----
  function paintExtra() {
    const st = P.status, s = st.settings, list = s.extra || [];
    $("#rotateOn").checked = !!s.rotate;
    const names = (e) => Object.entries(e.accounts || {}).filter(([, v]) => v).map(([k]) => (k === "youtube" ? "YouTube" : "Instagram")).join(" + ") || "no account connected";
    $("#extraKeys").replaceChildren(...list.map((e) => {
      const li = document.createElement("li");
      li.innerHTML = `<label class="switch"><input type="checkbox" ${e.on ? "checked" : ""}><span></span></label>
        <div><strong></strong><small class="muted"></small></div><button class="ghost small" type="button">Remove</button>`;
      li.querySelector("strong").textContent = `${e.slot}. ${e.label}`;
      li.querySelector("small").textContent = ` ${names(e)} · key ending ${e.hint}`;
      li.querySelector("input").addEventListener("change", async (ev) => {
        P.status = await api(`/api/post/keys/${e.slot}/${ev.target.checked ? "on" : "off"}`, { method: "POST" }); paintExtra();
      });
      li.querySelector("button").addEventListener("click", async () => {
        P.status = await api(`/api/post/keys/${e.slot}`, { method: "DELETE" }); paintExtra();
      });
      return li;
    }));
    const total = 1 + list.length;
    $("#extraCount").textContent = s.rotate && st.senders > 1
      ? `✅ Clips rotate across ${st.senders} accounts.`
      : `${total} of ${st.max_keys || 30} keys added.`;
  }
  $("#rotateOn").addEventListener("change", async (e) => {
    try { P.status = await api("/api/post/settings", { method: "POST", body: { rotate: e.target.checked } }); paintExtra(); }
    catch (err) { toast(err.message); }
  });
  $("#extraAdd").addEventListener("click", async () => {
    const key = $("#extraKey").value.trim();
    if (!key) { toast("Paste a Zernio key first."); return; }
    const btn = $("#extraAdd");
    btn.disabled = true; btn.textContent = "Checking…";
    try {
      P.status = await api("/api/post/keys", { method: "POST", body: { key, label: $("#extraLabel").value.trim() } });
      $("#extraKey").value = ""; $("#extraLabel").value = "";
      paintExtra();
      toast("Added ✓");
    } catch (err) { toast(err.message); }
    finally { btn.disabled = false; btn.textContent = "+ Add"; }
  });
  function seg(id, v) { $$(`#${id} button`).forEach((b) => b.classList.toggle("on", b.dataset.v === String(v))); }
  function paintSlots(s) {
    const box = $("#slots");
    box.replaceChildren(...s.slots.slice(0, s.per_day).map((slot, i) => {
      const input = el("input", { type: "time", value: slot, "aria-label": `Time ${i + 1}`, step: "300" });
      input.addEventListener("change", () => { s.slots[i] = input.value || slot; dirty(); });
      return input;
    }));
  }
  function preview() {
    const tpl = $("#captionTpl").value, tags = $("#hashtags").value;
    let text = tpl.replace("{title}", "Bro's rizz goes wrong").replace("{hashtags}", tags).replace("{streamer}", "@streamer");
    const kw = $("#dmKeyword").value.trim();
    if ($("#dmOn").checked && kw && !text.toLowerCase().includes(kw.toLowerCase())) text = text.trimEnd() + `\n\nComment ${kw} and I'll DM you the link.`;
    $("#captionPreview").textContent = text.trim();
  }
  function dirty() { $("#saveState").textContent = "Not saved yet"; preview(); }
  ["#captionTpl", "#hashtags", "#dmKeyword", "#dmMessage", "#dmReply"].forEach((id) => $(id).addEventListener("input", dirty));
  $("#tz").addEventListener("change", dirty);
  $("#dmOn").addEventListener("change", () => { $("#dmFields").hidden = !$("#dmOn").checked; dirty(); });
  $$("#perDay button").forEach((b) => b.addEventListener("click", () => {
    const s = P.status.settings;
    s.per_day = Number(b.dataset.v);
    seg("perDay", s.per_day);
    paintSlots(s);
    dirty();
  }));

  /* key */
  $("#keyForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = $("#keyInput").value.trim();
    if (!key) { toast("Paste your Zernio key first."); return; }
    const btn = $("#keyTest");
    btn.disabled = true; btn.textContent = "Testing…";
    try {
      const r = await api("/api/post/key", { method: "POST", body: { key } });
      $("#keyInput").value = "";
      P.status = r;
      P.accounts = r.accounts || [];
      paintSetup();
      paintAccounts(r.chosen);
      toast("Key works ✓");
    } catch (err) { toast(err.message); }
    btn.disabled = false; btn.textContent = "Test & save";
  });
  $("#keyRemove").addEventListener("click", async () => {
    P.status = await api("/api/post/key", { method: "DELETE" });
    P.accounts = [];
    paintSetup();
    paintAccounts({});
  });

  /* accounts */
  async function loadAccounts() {
    try {
      const r = await api("/api/post/accounts");
      P.accounts = r.accounts;
      paintAccounts(r.chosen);
    } catch (err) { toast(err.message); }
  }
  function paintAccounts(chosen) {
    chosen = chosen || {};
    let any = false;
    $$(".acct").forEach((row) => {
      const platform = row.dataset.platform;
      const list = P.accounts.filter((a) => a.platform === platform);
      const pick = $(".acct-pick", row);
      const current = list.find((a) => a.id === chosen[platform]);
      row.classList.toggle("on", !!current);
      $(".acct-state", row).textContent = current ? `✓ Connected${current.username ? " as @" + current.username.replace(/^@/, "") : ""}` : "Not connected";
      $(".acct-connect", row).textContent = current ? "Add another" : "Connect";
      $(".acct-connect", row).className = (current ? "ghost" : "primary") + " small acct-connect";
      pick.hidden = list.length < 2;
      pick.replaceChildren(...list.map((a) => new Option("@" + (a.username || a.id).replace(/^@/, ""), a.id)));
      if (current) pick.value = current.id;
      any = any || !!current;
    });
    $("#tick2").hidden = !any;
    if (P.status) {
      P.status.settings.accounts = { ...P.status.settings.accounts, ...chosen };
      P.status.ready = P.status.has_key && any;
      $("#postingDot").hidden = P.status.ready;
    }
  }
  $$(".acct").forEach((row) => {
    $(".acct-connect", row).addEventListener("click", async () => {
      try {
        await api(`/api/post/connect/${row.dataset.platform}`, { method: "POST" });
        toast("Your browser opened. Approve there, then press “Check again”.");
      } catch (err) { toast(err.message); }
    });
    $(".acct-pick", row).addEventListener("change", async (e) => {
      const accounts = { ...P.status.settings.accounts, [row.dataset.platform]: e.target.value };
      P.status = await api("/api/post/settings", { method: "POST", body: { accounts } });
      paintAccounts(accounts);
    });
  });
  $("#acctRefresh").addEventListener("click", loadAccounts);

  /* save */
  $("#postSave").addEventListener("click", async () => {
    const s = P.status.settings;
    const body = {
      per_day: s.per_day,
      slots: [...$$("#slots input").map((i) => i.value), ...s.slots.slice(s.per_day)].slice(0, 6),
      timezone: $("#tz").value,
      caption: $("#captionTpl").value,
      hashtags: $("#hashtags").value,
      dm: { on: $("#dmOn").checked, keyword: $("#dmKeyword").value, message: $("#dmMessage").value, reply: $("#dmReply").value },
    };
    if (body.dm.on && !/https?:\/\//.test(body.dm.message)) { toast("Put your link in the DM message (starts with https://)."); return; }
    try {
      P.status = await api("/api/post/settings", { method: "POST", body });
      paintSetup();
      paintAccounts(P.status.settings.accounts);
      $("#saveState").textContent = "Saved ✓";
      toast("Posting settings saved");
    } catch (err) { toast(err.message); }
  });

  /* ---- queue ---- */
  const LABEL = { waiting: "Waiting", uploading: "Uploading…", scheduled: "Scheduled", publishing: "Posting…", published: "Posted", partial: "Partly posted", failed: "Failed" };
  function friendlyWhen(when) {
    if (!when) return "As soon as possible";
    const d = new Date(when);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const day = new Date(d); day.setHours(0, 0, 0, 0);
    const diff = Math.round((day - today) / 86400000);
    const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const name = diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    return `${name}, ${time}`;
  }
  async function loadQueue(refresh) {
    try {
      const r = await api(`/api/post/queue${refresh ? "?refresh=true" : ""}`);
      P.items = r.items;
      paintQueue();
    } catch (err) { toast(err.message); }
  }
  function paintQueue() {
    const order = (i) => (i.when || "0000") + (i.created || "");
    const items = [...P.items].sort((a, b) => order(a).localeCompare(order(b)));
    const open = items.filter((i) => ["waiting", "uploading", "scheduled", "publishing"].includes(i.status)).length;
    $("#queueCount").textContent = open || "";
    const zone = P.status ? P.status.settings.timezone.replace("_", " ") : "your time zone";
    $("#queueHint").textContent = !items.length ? "Nothing scheduled yet. Make clips, then press “📤 Schedule all”."
      : open ? `${open} waiting to go out. Times are in ${zone}.` : "Everything has gone out ✓";
    $("#queueList").replaceChildren(...items.map((item) => {
      const links = Object.entries(item.links || {}).map(([p, url]) => el("a", { href: url, target: "_blank", rel: "noopener", text: p === "youtube" ? "Open on YouTube" : "Open on Instagram" }));
      const side = el("div", { class: "qside" }, el("span", { class: `pill ${item.status}`, text: LABEL[item.status] || item.status }));
      if (["failed", "partial"].includes(item.status)) side.append(el("button", { class: "primary small", type: "button", text: "Retry", onclick: () => act(`/api/post/queue/${item.id}/retry`, "POST", "Trying again…") }));
      if (["waiting", "failed"].includes(item.status)) side.append(el("button", { class: "ghost small", type: "button", text: "Remove", onclick: () => act(`/api/post/queue/${item.id}`, "DELETE", "Removed") }));
      const dm = item.dm === "set" ? " · Auto-DM ready" : item.dm === "waiting" ? " · Auto-DM pending" : item.dm === "failed" ? " · Auto-DM not set" : "";
      return el("div", { class: "qitem" },
        el("a", { class: "qthumb", href: `/api/post/queue/${item.id}/video`, target: "_blank", title: "Watch" }, el("img", { src: `/api/post/queue/${item.id}/thumb`, alt: "", loading: "lazy" })),
        el("div", { class: "qtext" },
          el("strong", { text: item.title, title: item.caption }),
          el("span", { class: "muted", text: friendlyWhen(item.when) + dm }),
          item.error ? el("span", { class: "err", text: item.error }) : null,
          links.length ? el("div", {}, links) : null),
        side);
    }));
    clearInterval(P.poll);
    if (!$("#posting").hidden && P.items.some((i) => ["waiting", "uploading"].includes(i.status))) P.poll = setInterval(() => loadQueue(false), 2500);
  }
  async function act(path, method, message) {
    try {
      const r = await api(path, { method });
      P.items = r.items;
      paintQueue();
      toast(message);
    } catch (err) { toast(err.message); }
  }
  $("#queueRefresh").addEventListener("click", () => loadQueue(true));

  /* ---- schedule from the clips screen: one clear choice of WHEN, every time ---- */
  const pad = (n) => String(n).padStart(2, "0");
  const localInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  let pending = null;
  function whenChoice() { const r = $('input[name="schedWhen"]:checked'); return r ? r.value : "at"; }
  function paintDialog() {
    const choice = whenChoice();
    $("#schedAtBox").hidden = choice !== "at";
    $("#schedGapRow").hidden = !pending || pending.count < 2;
    const go = $("#schedGo");
    go.textContent = choice === "now" ? "Post now" : "Schedule";
    const at = $("#schedAt").value ? new Date($("#schedAt").value) : null;
    let note = "";
    if (choice === "at" && at) {
      const mins = Math.round((at - Date.now()) / 60000);
      note = mins < 1 ? "That time has passed. Pick a later one."
        : `First one ${mins < 60 ? `in ${mins} min` : friendlyWhen(localInput(at)).toLowerCase()}` +
          (pending.count > 1 ? `, then one every ${$("#schedGap").selectedOptions[0].text}.` : ".");
    }
    if (choice === "slots" && P.status) {
      const s = P.status.settings;
      note = `Daily times: ${s.slots.slice(0, s.per_day).join(", ")} (${s.timezone.replace("_", " ")}). Change them in Posting → Settings.`;
    }
    $("#schedNote").textContent = note;
  }
  function closeDialog() { $("#schedDlg").hidden = true; pending = null; }
  async function schedule(jobId, indexes, extra) {
    if (!P.status) { try { P.status = await api("/api/post/status"); } catch { /* below */ } }
    if (!P.status || !P.status.ready) {
      open("setup");
      toast("Set up posting first: add your Zernio key and connect an account.");
      return;
    }
    const job = CS().state.job;
    const count = extra && extra.long_index != null ? 1 : indexes ? indexes.length : ((job && job.clips) || []).length || 1;
    pending = { jobId, indexes, extra: extra || {}, count };
    $("#schedWhat").textContent = extra && extra.long_index != null ? "Your YouTube video" : `${count} clip${count === 1 ? "" : "s"}`;
    const soon = new Date(Date.now() + 10 * 60000);
    soon.setMinutes(Math.ceil(soon.getMinutes() / 5) * 5, 0, 0);
    $("#schedAt").value = localInput(soon);
    $("#schedAt").min = localInput(new Date());
    let last = "at";
    try { last = localStorage.getItem("clipstudio.when") || "at"; } catch { /* private mode */ }
    const radio = $(`input[name="schedWhen"][value="${last}"]`) || $('input[name="schedWhen"][value="at"]');
    radio.checked = true;
    const s = P.status.settings;
    $("#schedSlots").textContent = `${s.slots.slice(0, s.per_day).join(", ")} every day. Change them in Posting → Settings.`;
    $("#schedDlg").hidden = false;
    paintDialog();
  }
  $$('input[name="schedWhen"]').forEach((r) => r.addEventListener("change", paintDialog));
  ["#schedAt", "#schedGap"].forEach((id) => $(id).addEventListener("input", paintDialog));
  $("#schedClose").addEventListener("click", closeDialog);
  $("#schedCancel").addEventListener("click", closeDialog);
  $("#schedGo").addEventListener("click", async () => {
    if (!pending) return;
    const when = whenChoice();
    const body = { job_id: pending.jobId, indexes: pending.indexes, ...pending.extra, when };
    if (when === "at") {
      if (!$("#schedAt").value) { toast("Pick a date and time."); return; }
      body.at = $("#schedAt").value;
      body.zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      body.gap_minutes = pending.count > 1 ? Number($("#schedGap").value) : 0;
    }
    try { localStorage.setItem("clipstudio.when", when); } catch { /* private mode */ }
    const btn = $("#schedGo");
    btn.disabled = true;
    try {
      const r = await api("/api/post/queue", { method: "POST", body });
      P.items = r.items;
      const first = r.added[0];
      closeDialog();
      toast(when === "now" ? `Posting ${r.added.length === 1 ? "it" : r.added.length + " clips"} now…`
        : `${r.added.length} scheduled, first one ${friendlyWhen(first && first.when).toLowerCase()}`);
      open("queue");
    } catch (err) { toast(err.message); }
    btn.disabled = false;
  });
  $("#postAll").addEventListener("click", () => {
    const job = CS().state.job;
    if (job) schedule(job.id, null);
  });

  window.ClipPosting = { open, schedule };
  api("/api/post/status").then((s) => { P.status = s; $("#postingDot").hidden = s.ready; }).catch(() => {});
})();
