"use strict";
/* "Something went wrong" instead of a frozen or blank window.
   - Page errors are sent to the engine, which writes them to crash.log.
   - If the engine stops answering, a friendly screen says what to do. */
(function () {
  let reported = 0, shown = false, support = null, softShown = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function report(message, where, stack) {
    if (reported++ > 20) return;
    try {
      fetch("/api/client-error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: String(message || "").slice(0, 2000), where: String(where || "").slice(0, 500), stack: String(stack || "").slice(0, 6000) }),
      }).catch(() => {});
    } catch { /* nothing more we can do */ }
  }
  async function alive() {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      return r.ok;
    } catch { return false; }
  }
  async function loadSupport() {
    try { support = await (await fetch("/api/support", { cache: "no-store" })).json(); } catch { /* engine down */ }
  }

  function details(kind) {
    return [
      `MasterClip ${support ? support.version : ""}`,
      `Problem: ${kind}`,
      `Time: ${new Date().toString()}`,
      `Screen: ${location.pathname}`,
      `Crash log: ${support ? support.crash_log : "%LOCALAPPDATA%\\Clip Studio\\crash.log"}`,
    ].join("\n");
  }

  function show(engine) {
    if (shown) return;
    shown = true;
    document.querySelectorAll(".crash-soft").forEach((n) => n.remove());
    const box = document.createElement("div");
    box.className = "crash";
    box.id = "crash";
    box.setAttribute("role", "alertdialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "crashTitle");
    const log = support ? support.crash_log : "%LOCALAPPDATA%\\Clip Studio\\crash.log";
    box.innerHTML = `
      <div class="crash-card">
        <div class="crash-face" aria-hidden="true">😕</div>
        <h2 id="crashTitle">Something went wrong</h2>
        <p class="muted">${engine ? "MasterClip's engine stopped answering." : "This screen hit a problem."} Your finished clips are safe.</p>
        <p><strong>Here's what to do:</strong></p>
        <ol>
          <li>Press <b>Try again</b>.</li>
          <li>Still stuck? Close MasterClip and open it again from the Start menu.</li>
          <li>If it keeps happening, restart your PC, then email us the crash log:<br><code class="crash-path"></code></li>
        </ol>
        <p class="crash-status muted" aria-live="polite"></p>
        <div class="crash-actions">
          <button type="button" class="primary" data-act="retry">Try again</button>
          <button type="button" class="ghost" data-act="logs">Open the log folder</button>
          <button type="button" class="ghost" data-act="copy">Copy details</button>
        </div>
      </div>`;
    box.querySelector(".crash-path").textContent = log;
    document.body.append(box);
    const status = box.querySelector(".crash-status");
    box.querySelector('[data-act="retry"]').addEventListener("click", async () => {
      status.textContent = "Checking…";
      if (await alive()) { location.reload(); return; }
      status.textContent = "The engine is still not answering. Close MasterClip and open it again.";
    });
    box.querySelector('[data-act="logs"]').addEventListener("click", async () => {
      try {
        const r = await fetch("/api/open-logs", { method: "POST" });
        status.textContent = r.ok ? "Opened the log folder." : "Couldn't open it. The path is above.";
      } catch { status.textContent = "Couldn't open it while the engine is down. The path is above."; }
    });
    box.querySelector('[data-act="copy"]').addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(details(engine ? "engine stopped" : "page error")); status.textContent = "Copied. Paste it into your email."; }
      catch { status.textContent = "Couldn't copy. Select the path above instead."; }
    });
    box.querySelector('[data-act="retry"]').focus();
  }

  function showSoft() {
    // A script error on the page: offer a reload without blocking what still works.
    if (softShown || shown) return;
    softShown = true;
    const bar = document.createElement("div");
    bar.className = "crash-soft";
    bar.setAttribute("role", "alert");
    bar.innerHTML = `<span>Something on this screen went wrong. Your clips are safe.</span>
      <button type="button" class="primary small">Reload the screen</button>
      <button type="button" class="ghost small" aria-label="Hide">✕</button>`;
    const [reload, hide] = bar.querySelectorAll("button");
    reload.addEventListener("click", () => location.reload());
    hide.addEventListener("click", () => bar.remove());
    document.body.append(bar);
  }

  let checking = false;
  async function engineLost() {
    if (shown || checking) return;
    checking = true;
    for (let i = 0; i < 4; i++) {
      if (await alive()) { checking = false; return; }
      await sleep(900);
    }
    checking = false;
    show(true);
  }

  window.addEventListener("error", (e) => {
    if (!e.message) return; // a broken <img> etc., not a script error
    report(e.message, `${e.filename || ""}:${e.lineno || 0}:${e.colno || 0}`, e.error && e.error.stack);
    if (document.body) showSoft();
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason || {};
    if (typeof r.status === "number") return; // an api() error the screen already explained
    report(r.message || String(r), "unhandled promise", r.stack);
  });
  // Heartbeat: notice a dead engine even when the buyer isn't clicking anything.
  let misses = 0;
  setInterval(async () => {
    if (document.hidden || shown) return;
    if (await alive()) { misses = 0; return; }
    if (++misses >= 2) engineLost();
  }, 5000);
  document.addEventListener("DOMContentLoaded", loadSupport);

  window.ClipCrash = { engineLost, show, report };
})();
