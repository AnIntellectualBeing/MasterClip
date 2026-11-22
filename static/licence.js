"use strict";
/* Licence chip in the header + the licence window. A 402 from the server opens it (see app.js api()). */
(function () {
  const $ = (s) => document.querySelector(s);
  const CS = () => window.ClipStudio;
  let state = null;

  function paint() {
    if (!state) return;
    const chip = $("#openLicence");
    chip.hidden = !!state.free;  // the free, open-source edition has no trial and no licence
    if (state.free) return;
    if (state.licensed) {
      $("#licenceChip").textContent = "Licensed";
      chip.className = "ghost licence-chip ok";
    } else {
      const left = state.trial_left;
      $("#licenceChip").textContent = left > 0 ? `Free trial · ${left} left` : "Trial over · enter key";
      chip.className = "ghost licence-chip" + (left <= 1 ? " trial-low" : "");
      $("#licenceLeft").textContent = left > 0 ? `${left} of ${state.trial_total} free videos left.` : "Paste your licence key.";
      $("#licenceLead").textContent = left > 0
        ? "After that, one licence keeps MasterClip working on up to 2 PCs. No subscription."
        : "One licence works on up to 2 PCs, with no subscription. It's in the email you got after buying.";
    }
    $("#licenceTrial").hidden = state.licensed;
    $("#licenceOk").hidden = !state.licensed;
    $("#licenceOrder").textContent = state.order || "";
    $("#licenceSeats").textContent = state.seats || 2;
    const buy = $("#licenceBuy");
    buy.hidden = !state.buy_url || state.licensed;
    if (state.buy_url) buy.href = state.buy_url;
    buy.textContent = state.price ? `Buy a licence · ${state.price}` : "Buy a licence";
    $("#licenceBuyNote").hidden = buy.hidden || !state.recover_url;  // the note describes our own Razorpay page
    $("#licenceLost").hidden = !state.recover_url || state.licensed;
    if (state.recover_url) $("#licenceRecover").href = state.recover_url;
  }

  async function load() {
    try { state = await CS().api("/api/licence"); paint(); } catch { /* engine starting */ }
  }

  function open(why) {
    $("#licenceWhy").textContent = why || "";
    $("#licenceWhy").hidden = !why;
    $("#licenceMsg").textContent = "";
    $("#licence").hidden = false;
    load();
    setTimeout(() => { if (!$("#licenceTrial").hidden) $("#licenceKey").focus(); }, 50);
  }
  function close() { $("#licence").hidden = true; }

  $("#openLicence").addEventListener("click", () => open());
  $("#licenceClose").addEventListener("click", close);
  $("#licence").addEventListener("click", (e) => { if (e.target.id === "licence") close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#licence").hidden) close(); });

  $("#licenceForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#licenceMsg");
    msg.className = "hint";
    msg.textContent = "Checking your key…";
    try {
      state = await CS().api("/api/licence", { method: "POST", body: { key: $("#licenceKey").value } });
      $("#licenceKey").value = "";
      $("#licenceWhy").hidden = true;
      paint();
      CS().toast("Licence activated. Thank you!");
    } catch (err) {
      msg.className = "hint bad";
      msg.textContent = err.message;
    }
  });

  $("#licenceRemove").addEventListener("click", async () => {
    try { state = await CS().api("/api/licence", { method: "DELETE" }); paint(); } catch (err) { CS().notice(err.message); }
  });

  // refresh the chip after each new video starts
  const makeBtn = document.getElementById("makeClips");
  if (makeBtn) makeBtn.addEventListener("click", () => setTimeout(load, 1500));

  window.ClipLicence = { open, load };
  load();
})();
