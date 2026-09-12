(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  const initData = tg ? tg.initData : "";
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  async function api(path, options = {}) {
    const headers = {"X-Telegram-Init-Data": initData, ...(options.headers || {})};
    const r = await fetch(path, {...options, headers, credentials: "omit", cache: "no-store"});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
    return data;
  }
  const post = (path, body) => api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  async function refresh() {
    try {
      const s = await api("/api/onboarding/status");
      $("conn-pill").textContent = s.connected ? `@${s.bot.username}` : "not connected";
      const dests = (s.destinations || []).map(d => `<div class="item"><span>${esc(d.username || d.chat_id)}</span><span class="pill">${esc(d.status)}</span></div>`).join("");
      $("status").innerHTML = (s.connected ? `<div class="item"><b>Bot</b><span>@${esc(s.bot.username)} · id ${esc(s.bot.bot_id)}</span></div>` : `<div class="muted">No bot connected yet.</div>`) + dests;
      $("notice").textContent = "Authenticated as Telegram user " + s.user_id;
    } catch (e) { $("notice").textContent = e.message; }
  }
  $("connect").addEventListener("click", async () => {
    const token = $("token").value.trim();
    if (!/^\d{4,12}:[A-Za-z0-9_-]{30,}$/.test(token)) { $("result").textContent = "That does not look like a BotFather token."; $("result").className = "err"; return; }
    $("connect").disabled = true;
    try {
      const r = await post("/api/onboarding/connect", {token});
      $("token").value = "";                         // never keep the secret in the DOM
      $("result").textContent = `Connected @${r.bot.username}.` + (r.bot_changed ? " Your destinations were disconnected and must be re-verified." : "");
      $("result").className = "ok";
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      refresh();
    } catch (e) { $("result").textContent = e.message; $("result").className = "err"; }
    finally { $("connect").disabled = false; }
  });
  $("disconnect").addEventListener("click", async () => {
    if (!confirm("Disconnect your bot? All your destinations stop receiving posts until you reconnect and re-verify.")) return;
    try { await post("/api/onboarding/disconnect", {}); $("result").textContent = "Disconnected."; $("result").className = "ok"; refresh(); }
    catch (e) { $("result").textContent = e.message; $("result").className = "err"; }
  });
  refresh();
})();
