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
  async function campaignAction(action, id, campaign) {
    if (action === "inspect") { try { const data=await api(`/api/admin/broadcasts/${encodeURIComponent(id)}/deliveries`); const d=data.deliveries||[]; $("notice").textContent=`${d.length} deliveries: ${d.filter(x=>x.status==='sent').length} sent, ${d.filter(x=>x.status==='failed').length} failed, ${d.filter(x=>x.status==='pending').length} pending`; } catch(e) { $("notice").textContent=e.message; } return; }
    if (["cancel", "delete"].includes(action)) && !confirm(`${action === "delete" ? "Delete" : "Cancel"} ${campaign.title || campaign.campaign_id}?`)) return;
    let body = {reason: `Admin Mini App ${action}`};
    if (action === "edit") {
      const title = prompt("Campaign title", campaign.title || "");
      if (title === null) return;
      const text = prompt("Plain-text campaign content", campaign.payload && campaign.payload.text || "");
      if (text === null) return;
      body = {title: title.trim(), text: text.trim(), scheduled_at: campaign.scheduled_at || null};
    }
    try {
      await api(`/api/admin/broadcasts/${encodeURIComponent(id)}/${action}`, {method:"POST", headers:{"Content-Type":"application/json", "X-Idempotency-Key":crypto.randomUUID()}, body:JSON.stringify(body)});
      $("notice").textContent = `Campaign ${action} completed`;
      await refresh();
    } catch (error) { $("notice").textContent = error.message; }
  }
  function campaignButtons(campaign) {
    const id = esc(campaign.campaign_id), status = campaign.status;
    const button = (action, label) => `<button data-campaign-action="${action}" data-id="${id}">${label}</button>`;
    const inspect = button("inspect", "Inspect deliveries");
    if (status === "draft") return inspect + button("edit", "Edit") + button("queue", "Continue") + button("cancel", "Cancel") + button("delete", "Delete");
    if (["queued", "running"].includes(status)) return inspect + button("pause", "Pause") + button("cancel", "Cancel");
    if (status === "paused") return inspect + button("resume", "Continue") + button("cancel", "Cancel");
    return inspect;
  }
  function render(data) {
    const channels = data.channels || [], tasks = data.tasks || [], broadcasts = data.broadcasts || [];
    $("summary").innerHTML = `<div class="card"><strong>${channels.length}</strong><span>Destinations</span></div><div class="card"><strong>${tasks.length}</strong><span>Recommended tasks</span></div><div class="card"><strong>${broadcasts.length}</strong><span>Recent campaigns</span></div>`;
    $("channels").innerHTML = channels.length ? channels.map(c => `<div class="item"><div><b>${esc(c.username || c.destination)}</b><div class="muted">${esc((c.categories||[]).join(", "))}</div><div id="scan-${esc(c.destination)}" class="muted">${esc(c.status)}</div></div><span><button data-scan="${esc(c.destination)}">Run Telegram checks</button></span></div>`).join("") : `<div class="muted">No destinations registered.</div>`;
    document.querySelectorAll("[data-scan]").forEach(button => button.onclick = async () => { const target = $("scan-" + button.dataset.scan); target.textContent = "Checking Telegram membership, administrator rights, permissions, and metadata…"; button.disabled = true; try { const result = await api(`/api/admin/verification/${encodeURIComponent(button.dataset.scan)}`); const scan = result.scan || {}; const checks = scan.checks || {}; target.textContent = `${esc(scan.state || "UNKNOWN")} · ${Object.entries(checks).map(([k,v]) => `${k}: ${v}`).join(" · ")}${scan.reasons && scan.reasons.length ? ` · ${esc(scan.reasons.join("; "))}` : ""}`; } catch (error) { target.textContent = `Scan failed: ${esc(error.message)}`; } finally { button.disabled = false; } });
    const cats = [...new Set(tasks.map(t => t.category))];
    const selected = $("category").value; $("category").innerHTML = `<option value="">All categories</option>` + cats.map(c => `<option ${c===selected?"selected":""}>${esc(c)}</option>`).join("");
    $("tasks").innerHTML = tasks.length ? tasks.map(t => { const p=t.progress||{}; const action = t.status === "published" ? `<button data-task-action="cancel" data-task-id="${esc(t.task_id)}">Cancel</button>` : ""; return `<div class="item"><div><b>${esc(t.title)}</b><div class="muted">${esc(t.category)} · ${esc(t.status || "unknown")} · ${esc(p.completed||0)}/${esc(p.required||0)} complete</div></div><span class="actions"><span class="pill">🪙 ${esc(t.reward_amount)} Mint</span>${action}</span></div>`; }).join("") : `<div class="muted">No eligible tasks.</div>`;
    document.querySelectorAll("[data-task-action]").forEach(button => button.onclick = async () => { if (!confirm("Cancel this task?")) return; try { await post(`/api/admin/tasks/${encodeURIComponent(button.dataset.taskId)}/${button.dataset.taskAction}`, {reason:"Admin task lifecycle action"}); refresh(); } catch (error) { $("notice").textContent = error.message; } });
    $("broadcasts").innerHTML = broadcasts.length ? broadcasts.map(b => { const d=b.deliveries||{}; return `<div class="item"><div><b>${esc(b.title || b.campaign_id)}</b><div class="muted">${esc(b.status)} · sent ${esc(d.sent||0)} · pending ${esc(d.pending||0)} · failed ${esc(d.failed||0)} · ${b.composed_in==="telegram"?`composed in Telegram, ${esc(b.entity_count||0)} formatting entit${b.entity_count===1?"y":"ies"}`:"plain text"}</div>${b.preview_html?`<div class="preview">${b.preview_html}</div>`:""}</div><span class="actions">${campaignButtons(b)}</span></div>`}).join("") : `<div class="muted">No campaigns.</div>`;
    document.querySelectorAll("[data-campaign-action]").forEach(button => button.onclick = () => { const campaign = broadcasts.find(item => item.campaign_id === button.dataset.id); if (campaign) campaignAction(button.dataset.campaignAction, button.dataset.id, campaign); });
  }
  async function loadSecondary(data) {
    try {
      const audit = await api(`/api/admin/audit?object_type=${encodeURIComponent($("audit-type").value)}&limit=20`);
      $("audit").innerHTML = audit.events.length ? audit.events.map(e => `<div class="item"><span>${esc(e.object_id)} · ${esc(e.action)}</span><span class="muted">${esc(e.reason)}</span></div>`).join("") : `<div class="muted">No audit events.</div>`;
      const histories = await Promise.all((data.channels || []).map(c => api(`/api/admin/performance/${encodeURIComponent(c.destination)}?limit=3`)));
      $("performance").innerHTML = histories.length ? histories.map(h => `<div class="item"><div><b>${esc(h.destination_id)}</b><div class="muted">${h.current ? `${esc(h.current.tier)} · score ${esc(h.current.score)}` : "No observations"}</div></div><span>${h.control && Number(h.control.cooldown_until) > Math.floor(Date.now()/1000) ? `<button data-performance-clear="${esc(h.destination_id)}">Clear cooldown</button>` : `<span class="pill">${h.history.length} snapshots</span>`}</span></div>`).join("") : `<div class="muted">No performance history.</div>`;
      document.querySelectorAll("[data-performance-clear]").forEach(button => button.onclick = async () => { try { await api(`/api/admin/performance/${encodeURIComponent(button.dataset.performanceClear)}/clear`, {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({reason:"Admin Mini App intervention"})}); refresh(); } catch (error) { $("notice").textContent = error.message; } });
      try { const worker = await api("/api/admin/worker/metrics"); const scopes = worker.scopes || {}; $("worker-metrics").innerHTML = Object.keys(scopes).length ? Object.entries(scopes).map(([scope, values]) => `<div class="item"><b>${esc(scope)}</b><span class="muted">${Object.entries(values).map(([outcome, stat]) => `${esc(outcome)}: ${esc(stat.count)} (${esc(stat.average_ms)} ms)`).join(" · ")}</span></div>`).join("") : `<div class="muted">No delivery metrics yet.</div>`; } catch (error) { $("worker-metrics").innerHTML = `<div class="muted">Metrics unavailable: ${esc(error.message)}</div>`; }
      const period = $("period").value || new Date().toISOString().slice(0,7);
      try { const referral = await api(`/api/admin/referrals/${encodeURIComponent(period)}`); $("referrals").innerHTML = `<div class="item"><div><b>${esc(referral.period)}</b><div class="muted">${esc(referral.status)} · pool ${esc(referral.pool)} · winners ${esc(referral.winners)}</div></div>${referral.status === "preview" ? `<button id="approve-referral">Approve</button>` : `<span class="pill">Finalized</span>`}</div>`; const approve = $("approve-referral"); if (approve) approve.onclick = async () => { await api(`/api/admin/referrals/${encodeURIComponent(period)}/approve`, {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({reason:"Admin Mini App approval"})}); refresh(); }; } catch (_) { $("referrals").innerHTML = `<div class="muted">No snapshot for ${esc(period)}.</div>`; }
    } catch (error) { $("notice").textContent = error.message; }
  }
  const post = (path, body) => api(path, {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify(body)});
  async function loadAds() {
    try {
      const ads = await api("/api/admin/ads");
      $("ads-status").textContent = `${ads.enabled ? "ENABLED" : "disabled"} · ${ads.consents} consenting destinations`;
      $("ads-terms").textContent = ads.terms ? `Terms v${ads.terms.version} published ${new Date(ads.terms.published_at*1000).toLocaleDateString()}: ${ads.terms.text.slice(0,200)}` : "No advertising terms published — publish terms before any consent or campaign.";
      const rows = ads.campaigns || [];
      const btn = (id, action, label) => `<button data-ad="${esc(id)}" data-ad-action="${action}">${label}</button>`;
      $("ads").innerHTML = rows.length ? rows.map(c => { const d=c.deliveries||{}; let actions=""; if (c.status==="draft"||c.status==="rejected") actions=btn(c.campaign_id,"edit","Edit")+btn(c.campaign_id,"submit","Submit for review")+btn(c.campaign_id,"delete","Delete"); else if (c.status==="in_review") actions=btn(c.campaign_id,"approve","Approve")+btn(c.campaign_id,"reject","Reject"); else if (c.status==="approved") actions=btn(c.campaign_id,"queue","Queue to consenting"); else if (["queued","running"].includes(c.status)) actions=btn(c.campaign_id,"pause","Pause"); else if (c.status==="paused") actions=btn(c.campaign_id,"resume","Resume"); if (!["completed","cancelled"].includes(c.status)) actions+=btn(c.campaign_id,"cancel","Cancel"); return `<div class="item"><div><b>${esc(c.title)}</b> <span class="muted">by ${esc(c.advertiser_label)}</span><div class="muted">${esc(c.status)} · ${esc(c.category)} · terms v${esc(c.terms_version)} · sent ${esc(d.sent||0)} · pending ${esc(d.pending||0)} · blocked ${esc(d.blocked||0)}${c.review_reason?` · review: ${esc(c.review_reason)}`:""}</div></div><span>${actions}</span></div>`; }).join("") : `<div class="muted">No ad campaigns.</div>`;
      document.querySelectorAll("[data-ad]").forEach(b => b.onclick = async () => { const a=b.dataset.adAction; let reason=""; if (a === "edit") { const title=prompt("Ad title"); const text=prompt("Ad text"); if(title===null||text===null)return; try { await post(`/api/admin/ads/${encodeURIComponent(b.dataset.ad)}/edit`, {title, text}); loadAds(); } catch(e) { $("notice").textContent=e.message; } return; } if (["approve","reject","cancel","delete"].includes(a)) { reason = prompt(`Reason for ${a} (required for review):`) || ""; if ((a==="approve"||a==="reject") && !reason) return; } try { await post(`/api/admin/ads/${encodeURIComponent(b.dataset.ad)}/${a}`, {reason}); loadAds(); } catch (e) { $("notice").textContent = e.message; } });
    } catch (error) { $("ads-status").textContent = "unavailable"; }
  }
  $("publish-ad-terms").addEventListener("click", async () => { const text=$("ads-terms-text").value.trim(); if(!text){$("notice").textContent="Terms text is required";return;} if(!confirm("Publishing new terms invalidates every existing consent. Continue?")) return; try { await post("/api/admin/ads/terms",{text}); $("ads-terms-text").value=""; loadAds(); } catch(e){$("notice").textContent=e.message;} });
  $("create-ad").addEventListener("click", async () => { const body={title:$("ad-title").value.trim(), advertiser_label:$("ad-advertiser").value.trim(), category:$("ad-category").value.trim(), text:$("ad-text").value.trim()}; if(!body.title||!body.advertiser_label||!body.category||!body.text){$("notice").textContent="Title, advertiser, category and text are required";return;} try { await post("/api/admin/ads", body); $("ad-text").value=""; loadAds(); } catch(e){$("notice").textContent=e.message;} });
  async function loadSafety() {
    try {
      const safety = await api("/api/admin/safety");
      const st = safety.status || {};
      $("safety-status").textContent = `${st.safe_mode && st.safe_mode.enabled ? "SAFE MODE ON" : "normal"} · ${st.open_reports||0} reports · ${st.open_appeals||0} appeals`;
      const reports = safety.reports || [], appeals = safety.appeals || [];
      $("reports").innerHTML = reports.length ? reports.map(r => `<div class="item"><div><b>${esc(r.entity_type)} ${esc(r.entity_id)}</b><div class="muted">${esc(r.status)} · ${esc(r.reason)} · by ${esc(r.reporter_id)}</div></div><span><button data-report="${esc(r.report_id)}" data-status="UNDER_REVIEW">Review</button><button data-report="${esc(r.report_id)}" data-status="DISMISSED">Dismiss</button><button data-report="${esc(r.report_id)}" data-status="WARNED">Warn</button><button data-report-restrict="${esc(r.report_id)}" data-entity="${esc(r.entity_id)}" data-type="${esc(r.entity_type)}">Restrict</button></span></div>`).join("") : `<div class="muted">No open reports.</div>`;
      $("appeals").innerHTML = appeals.length ? appeals.map(a => `<div class="item"><div><b>${esc(a.entity_type)} ${esc(a.entity_id)}</b><div class="muted">${esc(a.status)} · ${esc(a.statement)}</div></div><span><button data-appeal="${esc(a.appeal_id)}" data-decision="UNDER_REVIEW">Review</button><button data-appeal="${esc(a.appeal_id)}" data-decision="UPHELD">Uphold</button><button data-appeal="${esc(a.appeal_id)}" data-decision="OVERTURNED">Overturn</button></span></div>`).join("") : `<div class="muted">No open appeals.</div>`;
      document.querySelectorAll("[data-report]").forEach(b => b.onclick = async () => { const notes = prompt(`Notes for ${b.dataset.status}:`) || ""; try { await post(`/api/admin/reports/${encodeURIComponent(b.dataset.report)}/review`, {status:b.dataset.status, notes}); loadSafety(); } catch (e) { $("safety-result").textContent = e.message; } });
      document.querySelectorAll("[data-report-restrict]").forEach(b => b.onclick = async () => { const reason = prompt("Reason for RESTRICT (required; owner only):"); if(!reason) return; try { await post(`/api/admin/enforcement/${encodeURIComponent(b.dataset.type)}/${encodeURIComponent(b.dataset.entity)}/restricted`, {reason, related_report_id:b.dataset.reportRestrict}); loadSafety(); } catch (e) { $("safety-result").textContent = e.message; } });
      document.querySelectorAll("[data-appeal]").forEach(b => b.onclick = async () => { const notes = prompt(`Notes for ${b.dataset.decision}:`) || ""; if (b.dataset.decision === "OVERTURNED" && !confirm("Overturning restores the entity to ACTIVE. Continue?")) return; try { await post(`/api/admin/appeals/${encodeURIComponent(b.dataset.appeal)}/decide`, {decision:b.dataset.decision, notes}); loadSafety(); } catch (e) { $("safety-result").textContent = e.message; } });
    } catch (error) { $("safety-status").textContent = "unavailable"; }
  }
  $("file-report").addEventListener("click", async () => { const entity_id=$("report-entity-id").value.trim(), entity_type=$("report-entity-type").value, reason=$("report-reason").value.trim(); if(!entity_id||!reason){$("safety-result").textContent="Entity ID and reason are required";return;} try { await post("/api/admin/reports", {entity_id, entity_type, reason}); $("report-reason").value=""; loadSafety(); } catch(e){$("safety-result").textContent=e.message;} });
  async function refresh() {
    $("notice").textContent = "Loading authenticated dashboard…";
    try { const query = new URLSearchParams(); if ($("category").value) query.set("task_category", $("category").value); if ($("broadcast-status").value) query.set("broadcast_status", $("broadcast-status").value); const data = await api(`/api/admin/dashboard?${query}`); render(data); await loadSecondary(data); await loadSafety(); await loadAds(); $("notice").textContent = "Authenticated dashboard"; }
    catch (error) { $("notice").textContent = `Unable to load dashboard: ${error.message}`; }
  }
  $("recover-broadcasts").addEventListener("click", async () => { try { const result = await post("/api/admin/broadcasts/recover", {older_than_seconds: 900}); $("notice").textContent = `Recovered ${result.recovered || 0}; terminal ${result.terminal || 0}`; refresh(); } catch (error) { $("notice").textContent = error.message; } });
  $("create-broadcast").addEventListener("click", async () => { const title = $("broadcast-title").value.trim(), text = $("broadcast-text").value.trim(); if (!title || !text) { $("notice").textContent = "Campaign title and text are required"; return; } const scheduled = $("broadcast-scheduled").value ? Math.floor(new Date($("broadcast-scheduled").value).getTime()/1000) : null; let entities = []; try { entities = JSON.parse($("broadcast-entities").value || "[]"); if (!Array.isArray(entities)) throw new Error("entities must be an array"); } catch (error) { $("notice").textContent = `Invalid Telegram entities JSON: ${error.message}`; return; } try { await api("/api/admin/broadcasts", {method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({title,text,entities,scheduled_at:scheduled})}); $("broadcast-text").value=""; $("broadcast-entities").value=""; refresh(); } catch (error) { $("notice").textContent = error.message; } });
  $("apply-safety").addEventListener("click", async () => { const entityId=$("safety-entity-id").value.trim(), reason=$("safety-reason").value.trim(), entityType=$("safety-entity-type").value, action=$("safety-action").value; if(!entityId||!reason){$("safety-result").textContent="Entity ID and reason are required";return;} if(!confirm(`Apply ${action} to ${entityType} ${entityId}?`)) return; try { const result=await api(`/api/admin/enforcement/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/${action}`,{method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({reason})}); $("safety-result").textContent=`${result.state} recorded for ${entityType} ${entityId}`; } catch(error){$("safety-result").textContent=error.message;} });
  $("toggle-safe-mode").addEventListener("click", async () => { const enabled=!confirm("Press Cancel to enable emergency safe mode; press OK to disable it."); const reason=prompt("Reason for changing emergency safe mode:"); if(!reason)return; try { const result=await api("/api/admin/safety/emergency",{method:"POST",headers:{"Content-Type":"application/json","X-Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({enabled,reason})}); $("safety-result").textContent=`Safe mode ${result.enabled?"enabled":"disabled"}`; }catch(error){$("safety-result").textContent=error.message;} });
  $("refresh-worker-metrics").addEventListener("click", refresh);
  $("refresh").addEventListener("click", refresh); $("category").addEventListener("change", refresh); $("broadcast-status").addEventListener("change", refresh); $("audit-type").addEventListener("change", refresh); $("period").addEventListener("change", refresh); refresh();
})();
