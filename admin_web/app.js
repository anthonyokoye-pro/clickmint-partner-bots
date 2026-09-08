(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  const initData = tg ? tg.initData : "";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>\"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  async function api(path, options={}) {
    const headers = {"X-Telegram-Init-Data": initData, ...(options.headers || {})};
    const response = await fetch(path, {...options, headers});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }
  function render(data) {
    const channels = data.channels || [], tasks = data.tasks || [], broadcasts = data.broadcasts || [];
    $("summary").innerHTML = `<div class="card"><strong>${channels.length}</strong><span>Destinations</span></div><div class="card"><strong>${tasks.length}</strong><span>Recommended tasks</span></div><div class="card"><strong>${broadcasts.length}</strong><span>Recent campaigns</span></div>`;
    $("channels").innerHTML = channels.length ? channels.map(c => `<div class="item"><div><b>${esc(c.username || c.destination)}</b><div class="muted">${esc((c.categories||[]).join(", "))}</div></div><span class="pill">${esc(c.status)}</span></div>`).join("") : `<div class="muted">No destinations registered.</div>`;
    const cats = [...new Set(tasks.map(t => t.category))];
    const selected = $("category").value; $("category").innerHTML = `<option value="">All categories</option>` + cats.map(c => `<option ${c===selected?"selected":""}>${esc(c)}</option>`).join("");
    $("tasks").innerHTML = tasks.length ? tasks.map(t => `<div class="item"><div><b>${esc(t.title)}</b><div class="muted">${esc(t.category)}</div></div><span class="pill">🪙 ${esc(t.reward_amount)} Mint</span></div>`).join("") : `<div class="muted">No eligible tasks.</div>`;
    $("broadcasts").innerHTML = broadcasts.length ? broadcasts.map(b => { const d=b.deliveries||{}; return `<div class="item"><div><b>${esc(b.title || b.campaign_id)}</b><div class="muted">${esc(b.status)} · sent ${esc(d.sent||0)} · pending ${esc(d.pending||0)} · failed ${esc(d.failed||0)}</div></div><span>${["queued","running"].includes(b.status)?`<button data-action="pause" data-id="${esc(b.campaign_id)}">Pause</button>`:b.status==="paused"?`<button data-action="resume" data-id="${esc(b.campaign_id)}">Resume</button>`:b.status==="draft"?`<button data-action="queue" data-id="${esc(b.campaign_id)}">Queue</button>`:""}</span></div>`}).join("") : `<div class="muted">No campaigns.</div>`;
    document.querySelectorAll("[data-action]").forEach(button => button.onclick = async () => { try { await api(`/api/admin/broadcasts/${encodeURIComponent(button.dataset.id)}/${button.dataset.action}`, {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({reason:"Admin Mini App action"})}); refresh(); } catch (error) { $("notice").textContent = error.message; } });
  }
  async function loadSecondary(data) {
    try {
      const audit = await api(`/api/admin/audit?object_type=${encodeURIComponent($("audit-type").value)}&limit=20`);
      $("audit").innerHTML = audit.events.length ? audit.events.map(e => `<div class="item"><span>${esc(e.object_id)} · ${esc(e.action)}</span><span class="muted">${esc(e.reason)}</span></div>`).join("") : `<div class="muted">No audit events.</div>`;
      const histories = await Promise.all((data.channels || []).map(c => api(`/api/admin/performance/${encodeURIComponent(c.destination)}?limit=3`)));
      $("performance").innerHTML = histories.length ? histories.map(h => `<div class="item"><div><b>${esc(h.destination_id)}</b><div class="muted">${h.current ? `${esc(h.current.tier)} · score ${esc(h.current.score)}` : "No observations"}</div></div><span>${h.control && Number(h.control.cooldown_until) > Math.floor(Date.now()/1000) ? `<button data-performance-clear="${esc(h.destination_id)}">Clear cooldown</button>` : `<span class="pill">${h.history.length} snapshots</span>`}</span></div>`).join("") : `<div class="muted">No performance history.</div>`;
      document.querySelectorAll("[data-performance-clear]").forEach(button => button.onclick = async () => { try { await api(`/api/admin/performance/${encodeURIComponent(button.dataset.performanceClear)}/clear`, {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({reason:"Admin Mini App intervention"})}); refresh(); } catch (error) { $("notice").textContent = error.message; } });
      const period = $("period").value || new Date().toISOString().slice(0,7);
      try { const referral = await api(`/api/admin/referrals/${encodeURIComponent(period)}`); $("referrals").innerHTML = `<div class="item"><div><b>${esc(referral.period)}</b><div class="muted">${esc(referral.status)} · pool ${esc(referral.pool)} · winners ${esc(referral.winners)}</div></div>${referral.status === "preview" ? `<button id="approve-referral">Approve</button>` : `<span class="pill">Finalized</span>`}</div>`; const approve = $("approve-referral"); if (approve) approve.onclick = async () => { await api(`/api/admin/referrals/${encodeURIComponent(period)}/approve`, {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({reason:"Admin Mini App approval"})}); refresh(); }; } catch (_) { $("referrals").innerHTML = `<div class="muted">No snapshot for ${esc(period)}.</div>`; }
    } catch (error) { $("notice").textContent = error.message; }
  }
  async function refresh() {
    $("notice").textContent = "Loading authenticated dashboard…";
    try { const query = new URLSearchParams(); if ($("category").value) query.set("task_category", $("category").value); if ($("broadcast-status").value) query.set("broadcast_status", $("broadcast-status").value); const data = await api(`/api/admin/dashboard?${query}`); render(data); await loadSecondary(data); $("notice").textContent = "Authenticated dashboard"; }
    catch (error) { $("notice").textContent = `Unable to load dashboard: ${error.message}`; }
  }
  $("create-broadcast").addEventListener("click", async () => { const title = $("broadcast-title").value.trim(), text = $("broadcast-text").value.trim(); if (!title || !text) { $("notice").textContent = "Campaign title and text are required"; return; } const scheduled = $("broadcast-scheduled").value ? Math.floor(new Date($("broadcast-scheduled").value).getTime()/1000) : null; try { await api("/api/admin/broadcasts", {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({title,text,scheduled_at:scheduled})}); $("broadcast-text").value=""; refresh(); } catch (error) { $("notice").textContent = error.message; } });
  $("apply-safety").addEventListener("click", async () => { const entityId=$("safety-entity-id").value.trim(), reason=$("safety-reason").value.trim(), entityType=$("safety-entity-type").value, action=$("safety-action").value; if(!entityId||!reason){$("safety-result").textContent="Entity ID and reason are required";return;} if(!confirm(`Apply ${action} to ${entityType} ${entityId}?`)) return; try { const result=await api(`/api/admin/enforcement/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/${action}`,{method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({reason})}); $("safety-result").textContent=`${result.state} recorded for ${entityType} ${entityId}`; } catch(error){$("safety-result").textContent=error.message;} });
  $("toggle-safe-mode").addEventListener("click", async () => { const enabled=!confirm("Press Cancel to enable emergency safe mode; press OK to disable it."); const reason=prompt("Reason for changing emergency safe mode:"); if(!reason)return; try { const result=await api("/api/admin/safety/emergency",{method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({enabled,reason})}); $("safety-result").textContent=`Safe mode ${result.enabled?"enabled":"disabled"}`; }catch(error){$("safety-result").textContent=error.message;} });
  $("refresh").addEventListener("click", refresh); $("category").addEventListener("change", refresh); $("broadcast-status").addEventListener("change", refresh); $("audit-type").addEventListener("change", refresh); $("period").addEventListener("change", refresh); refresh();
})();
