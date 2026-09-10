function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (value != null) node.setAttribute(key, value);
    }
  }
  for (const child of children || []) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

async function loadContext() {
  return api("/api/insights/context");
}

function renderEmpty(title, body) {
  return el("div", { class: "empty" }, [el("h2", {}, [title]), el("p", { class: "muted" }, [body])]);
}

window.renderSettingsPanel = async function renderSettingsPanel() {
  const panel = document.getElementById("panel-settings");
  panel.replaceChildren(el("p", { class: "muted" }, ["正在读取身份…"]));
  const data = await loadContext();
  panel.replaceChildren();
  panel.appendChild(el("h1", {}, ["档案与设置"]));
  panel.appendChild(el("p", { class: "muted" }, ["确认“我是谁”和会话用途。关系由你补充，不是系统推断。"]));
  const audit = data.audit || {};
  panel.appendChild(el("p", {}, [`身份检查：${audit.verification_state || "unknown"}`]));
  if (data.needs_identity_confirmation) {
    panel.appendChild(el("p", { class: "notice" }, ["记录里的本人标记不一致或缺失。请选择一个 sender，不要按发言数量猜测。"]));
    for (const id of audit.self_sender_ids || []) {
      const btn = el("button", { type: "button" }, [`使用 ${id}`]);
      btn.addEventListener("click", async () => {
        await api("/api/insights/context", { method: "POST", body: JSON.stringify({ confirm_self_sender_id: id }) });
        renderSettingsPanel();
      });
      panel.appendChild(btn);
    }
  } else if (!data.identity) {
    const btn = el("button", { type: "button", class: "primary" }, ["确认当前本人身份"]);
    btn.addEventListener("click", async () => {
      await api("/api/insights/context", { method: "POST", body: JSON.stringify({ accept_consistent_self: true }) });
      renderSettingsPanel();
    });
    panel.appendChild(btn);
  } else {
    panel.appendChild(el("p", {}, [`已确认本人 ID 数量：${(data.identity.self_sender_ids || []).length}`]));
  }
  const migration = data.migration || {};
  if (migration.status === "conflict") {
    panel.appendChild(el("p", { class: "notice" }, [
      `本机发现另一份派生库，没有自动覆盖。冲突副本：${migration.legacy_conflict || "见 insights 目录"}。身份和笔记仍以当前这份为准。`,
    ]));
  } else if (migration.status === "needs_recovery") {
    panel.appendChild(el("p", { class: "notice" }, ["发现未能确认归属的旧记录。请使用下方恢复入口预览，不要跨账号合并。"]));
  } else if (migration.status === "failed") {
    panel.appendChild(el("p", { class: "notice" }, ["上次升级派生库没有完成，重新打开档案会再试一次，不会覆盖你后来写的笔记。"]));
  }
  renderRecovery(panel,data);
  panel.appendChild(el("h2", {}, ["会话用途"]));
  const convos = (window.state && window.state.convos) || [];
  const roles = {};
  for (const role of data.conversation_roles || []) roles[role.conversation_id] = role.purpose;
  for (const convo of convos.slice(0, 40)) {
    const row = el("label", { class: "role-row" });
    row.appendChild(document.createTextNode((convo.display_name || convo.conversation_id) + " "));
    const select = el("select");
    for (const purpose of ["daily", "read_later", "work", "excluded"]) {
      const opt = el("option", { value: purpose }, [purpose === "read_later" ? "收藏学习" : purpose === "daily" ? "日常聊天" : purpose === "work" ? "工作协作" : "暂不参与分析"]);
      if ((roles[convo.conversation_id] || "daily") === purpose) opt.selected = true;
      select.appendChild(opt);
    }
    select.addEventListener("change", async () => {
      await api("/api/insights/context", {
        method: "POST",
        body: JSON.stringify({ conversation_id: convo.conversation_id, purpose: select.value }),
      });
    });
    row.appendChild(select);
    panel.appendChild(row);
  }
  if (data.ambiguous_names && data.ambiguous_names.length) {
    panel.appendChild(el("p", { class: "notice" }, ["有重名会话，请按用途分别确认，不要按名字合并。"]));
  }

  let engines;
  try {
    engines = await api("/api/insights/engines");
  } catch (err) {
    panel.appendChild(el("p", { class: "notice" }, [`引擎设置暂时读不到：${err.message || err}`]));
    return;
  }
  panel.appendChild(el("h2", {}, ["AI 后端"]));
  panel.appendChild(el("p", { class: "muted" }, ["本机原话始终可用，不必在这里选。AI 后端是 Grok CLI 或 BYOK 二选一；两个都可以配好，生成时用当前默认。密钥只写本机，不会回显。"]));
  const byEng = {};
  for (const engine of engines.engines || []) byEng[engine.id] = engine;
  const grok = byEng.grok_cli || {};
  const byok = byEng.byok || {};
  const defaultSelect = el("select");
  for (const item of [
    ["grok_cli", "Grok CLI"],
    ["byok", "BYOK"],
  ]) {
    const opt = el("option", { value: item[0] }, [item[1]]);
    if ((engines.default === "byok" ? "byok" : "grok_cli") === item[0]) opt.selected = true;
    defaultSelect.appendChild(opt);
  }
  const defaultRow = el("label", { class: "role-row" });
  defaultRow.append(document.createTextNode("默认 AI 后端 "), defaultSelect);
  panel.appendChild(defaultRow);
  panel.appendChild(el("p", { class: "tiny muted" }, [grok.available ? `Grok CLI 可用：${grok.model || "grok-4.6"}` : "本机未找到 grok CLI。"]));
  panel.appendChild(el("p", { class: "tiny muted" }, [byok.available ? `BYOK 已配置：${byok.host || ""} / ${byok.model || ""}` : "BYOK 尚未配置。"]));

  function field(labelText, attrs) {
    const wrap = el("label", { class: "field-row" });
    wrap.appendChild(document.createTextNode(labelText));
    const input = el("input", attrs);
    wrap.appendChild(input);
    return { wrap, input };
  }
  const grokCmd = field("Grok 命令", { type: "text", value: grok.command || "", placeholder: "/Users/…/.grok/bin/grok" });
  const grokModel = field("Grok 模型", { type: "text", value: grok.model || "grok-4.6" });
  const byokUrl = field("BYOK Base URL", { type: "text", value: byok.base_url || "", placeholder: "https://token.weichao.site/v1" });
  const byokModel = field("BYOK 模型", { type: "text", value: byok.model || "gpt-6-astra" });
  const byokFile = field("BYOK 密钥文件（可选）", { type: "text", value: "", placeholder: "留空则使用已保存的密钥或下面粘贴的密钥" });
  const byokKey = field("BYOK API Key（可选，不会回显）", { type: "password", value: "", placeholder: byok.has_api_key ? "已保存，留空不改" : "粘贴密钥，保存后不会再显示" });
  panel.append(grokCmd.wrap, grokModel.wrap, byokUrl.wrap, byokModel.wrap, byokFile.wrap, byokKey.wrap);
  const save = el("button", { type: "button", class: "primary" }, ["保存引擎设置"]);
  const status = el("p", { class: "hint", role: "status" });
  save.addEventListener("click", async () => {
    try {
      const payload = {
        default: defaultSelect.value,
        grok_cli: { command: grokCmd.input.value, model: grokModel.input.value },
        byok: { base_url: byokUrl.input.value, model: byokModel.input.value },
      };
      if (byokFile.input.value.trim()) payload.byok.api_key_file = byokFile.input.value.trim();
      if (byokKey.input.value.trim()) payload.byok.api_key = byokKey.input.value.trim();
      const saved = await api("/api/insights/engines", { method: "POST", body: JSON.stringify(payload) });
      byokKey.input.value = "";
      status.textContent = `已保存。默认 ${saved.default}。密钥${(saved.engines || []).some((e) => e.id === "byok" && e.has_api_key) ? "已配置" : "未写入"}。`;
    } catch (err) {
      status.textContent = String(err.message || err);
    }
  });
  panel.append(save, status);
};

function consentLabel(engine, preview) {
  const consent = preview.consent || {};
  const host = engine.host || consent.host || "已配置引擎";
  const model = engine.model || "模型";
  const chars = consent.estimated_chars || 0;
  const count = consent.upload_count || 0;
  const via = engine.id === "grok_cli" ? "经本机 grok CLI 发到" : "发到";
  return `批准本次把 ${count} 条文字摘录（约 ${chars} 字）${via} ${host}，使用 ${model}。不发送附件和密钥。`;
}

const KIND_LABEL = {
  image: "图片",
  voice: "语音",
  video: "视频",
  link: "链接",
  quote: "引用",
  app: "应用消息",
  sticker: "表情",
  forwarded: "转发",
  system: "系统",
  file: "文件",
  location: "位置",
  card: "名片",
};

function engineLabel(engine) {
  if (engine.id === "grok_cli") return engine.available ? `Grok CLI（${engine.model || "grok-4.6"}）` : "Grok CLI（未找到）";
  if (engine.id === "byok") return engine.available ? `BYOK（${engine.model || engine.host || "OpenAI 兼容"}）` : "BYOK（未配置）";
  return engine.display_name || engine.id;
}

function findEngine(preview, id) {
  return (preview.engines || []).find((item) => item.id === id) || { id: id || "grok_cli", needs_consent: true, available: false };
}

function defaultAiEngine(preview) {
  const preferred = preview.default_engine === "byok" ? "byok" : "grok_cli";
  const first = findEngine(preview, preferred);
  if (first.available) return first;
  const other = findEngine(preview, preferred === "byok" ? "grok_cli" : "byok");
  return other.available ? other : first;
}

function aiBackendPicker(preview, name, current, onChange) {
  const box = el("div", { class: "engine-pick" });
  box.appendChild(el("span", { class: "tiny muted" }, ["AI 后端"]));
  for (const id of ["grok_cli", "byok"]) {
    const engine = findEngine(preview, id);
    const label = el("label");
    const radio = el("input", { type: "radio", name, value: id });
    if (current && current.id === id) radio.checked = true;
    if (!engine.available) radio.disabled = true;
    radio.addEventListener("change", () => onChange(engine));
    label.append(radio, document.createTextNode(engineLabel(engine)));
    box.appendChild(label);
  }
  return box;
}

function paintCoverageStats(body, preview) {
  body.replaceChildren();
  if (preview.needs_identity) {
    body.appendChild(renderEmpty("先确认我是谁", "身份未确认前不会生成画像。"));
    return;
  }
  const cov = preview.coverage || {};
  const total = cov.total_in_scope || (cov.self_count || 0) + (cov.other_count || 0);
  const unread = cov.non_readable || 0;
  const withText = Math.max(0, total - unread);
  const consent = preview.consent || {};
  body.appendChild(el("p", {}, [`范围内 ${total} 条。本人 ${cov.self_count || 0} 条，其他人 ${cov.other_count || 0} 条。`]));
  body.appendChild(el("p", {}, [`有正文可引用 ${withText} 条；图片/语音/链接等无正文 ${unread} 条，不会当成原话证据。`]));
  if (consent.upload_count != null) {
    body.appendChild(el("p", { class: "tiny muted" }, [`若用 AI，本次最多送出 ${consent.upload_count} 条文字摘录（约 ${consent.estimated_chars || 0} 字），不是全量档案。`]));
  }
  const kinds = Object.entries(cov.non_readable_by_kind || {}).sort((a, b) => b[1] - a[1]);
  if (kinds.length) {
    const top = kinds.slice(0, 8).map(([kind, n]) => `${KIND_LABEL[kind] || kind} ${n}`).join(" · ");
    body.appendChild(el("p", { class: "tiny muted" }, [top]));
  }
  if (cov.family_or_single_thread_heavy) {
    body.appendChild(el("p", { class: "notice" }, ["有一个会话占本人发言一半以上。相关观察会标成该场景中的情况，不会推广成整个人格。"]));
  }
  body.appendChild(el("p", { class: "muted" }, ["本机原话只在电脑上抽原句。用 AI 才会把范围内文字发给所选后端。报告生成后可导出为离线阅读包。"]));
}

function runStatusLabel(run) {
  if (run.is_stale || run.stale) return "这份报告基于旧档案，仅作历史，不是当前核实结论";
  if (run.status === "insufficient") return "资料不足";
  if (run.status === "partial") return "仅部分原话可核对，不是画像归纳完成";
  if (run.status === "completed") return "已整理可核对的原话，不是人格鉴定";
  return run.status || "";
}

function renderRunObservations(body, run, emptyNote) {
  const exportBtn=el('button',{type:'button',class:'primary'},['导出这份报告']);
  exportBtn.onclick=async()=>{exportBtn.disabled=true;try{const result=await api('/api/insights/exports',{method:'POST',body:JSON.stringify({kind:'profile',run_id:run.run_id})});const reveal=el('button',{type:'button'},['在访达中显示']);reveal.onclick=()=>api('/api/insights/reveal',{method:'POST',body:JSON.stringify({delivery_id:result.delivery_id})});body.prepend(el('p',{class:'notice'},['已导出 Markdown、JSON 和离线网页。']),reveal);}catch(e){body.prepend(el('p',{class:'notice'},[e.message]));}finally{exportBtn.disabled=false;}};
  body.append(exportBtn);
  if (run.is_stale || run.stale) {
    body.appendChild(el("p", { class: "notice" }, [runStatusLabel(run)]));
  }
  const result = run.result || {};
  if (result.sampled_count != null || result.candidate_count != null) {
    body.appendChild(el("p", { class: "tiny muted" }, [
      `候选 ${result.candidate_count || 0} 条，实际取样 ${result.sampled_count || 0} 条，写出观察 ${result.observation_count != null ? result.observation_count : (run.observations || []).length} 条。观察数不是处理消息数。`,
    ]));
  }
  if (run.status === "insufficient") {
    body.appendChild(renderEmpty("资料不足", "当前范围内可读发言太少，不能生成观察。"));
    return;
  }
  if (run.status === "partial" && !(run.observations || []).length) {
    const rejected = result.rejected_count ? `有 ${result.rejected_count} 条因证据无效或结论不被原文支持而未写入。` : "云端结果未通过原文核对，所以没有写成画像。";
    body.appendChild(renderEmpty("没有可核对的观察", rejected + " 这不是画像归纳完成。"));
    return;
  }
  if (!run.observations.length) {
    body.appendChild(el("p", { class: "notice" }, [emptyNote]));
  }
  for (const obs of run.observations) {
    if (obs.review_state === "excluded") continue;
    const entry = el("section", { class: "entry" });
    entry.appendChild(el("h3", {}, [obs.statement]));
    entry.appendChild(el("p", { class: "tiny muted" }, [`${obs.basis} · ${(obs.caveats || []).join(" ")}`]));
    const ev = (obs.evidence || [])[0];
    if (ev && ev.record_uid) {
      const jump = el("button", { type: "button", class: "link" }, ["在聊天中查看"]);
      jump.addEventListener("click", () => {
        showProductPage("chat");
        const convo = (window.state.convos || []).find((c) => c.conversation_id === ev.conversation_id);
        if (convo && window.openConvo) window.openConvo(convo, true, ev.record_uid);
      });
      entry.appendChild(jump);
    }
    if (obs.observation_id) {
      const fix = el("button", { type: "button", class: "link" }, ["排除这条"]);
      fix.addEventListener("click", async () => {
        await api(`/api/profiles/observations/${obs.observation_id}/corrections`, {
          method: "POST",
          body: JSON.stringify({ action: "exclude" }),
        });
        entry.appendChild(el("p", { class: "tiny" }, ["已排除，下次更新不再使用。"]));
      });
      entry.appendChild(fix);
    }
    body.appendChild(entry);
  }
}

window.renderSelfPanel = async function renderSelfPanel() {
  const panel = document.getElementById("panel-self");
  panel.replaceChildren();
  const heading = el("div", { class: "heading" }, [
    el("div", {}, [el("h1", {}, ["我的画像"]), el("p", { class: "muted" }, ["从记录里看见自己，而不是被一个标签定义。"])]),
  ]);
  const actions = el("div", { class: "actions" });
  const previewBtn = el("button", { type: "button" }, ["查看资料范围"]);
  const localBtn = el("button", { type: "button" }, ["生成本机原话"]);
  const aiBtn = el("button", { type: "button", class: "primary" }, ["用 AI 生成"]);
  actions.append(previewBtn, localBtn, aiBtn);
  heading.appendChild(actions);
  panel.appendChild(heading);
  const pickerHost = el("div");
  const consentRow = el("label", { class: "readable" });
  const check = el("input", { type: "checkbox" });
  consentRow.append(check, document.createTextNode("批准本次云端分析"));
  panel.append(pickerHost, consentRow);
  const body = el("div");
  panel.appendChild(body);
  renderTaskHistory(panel);
  let preview = { engines: [], default_engine: "grok_cli", consent: {} };
  let current = defaultAiEngine(preview);

  function syncConsent() {
    consentRow.replaceChildren(check, document.createTextNode(consentLabel(current, preview)));
  }

  async function runProfile(useAi) {
    if (useAi && !current.available) {
      body.replaceChildren(renderEmpty("这个 AI 后端还不能用", "到档案与设置里检查 Grok CLI 或 BYOK。"));
      return;
    }
    if (useAi && !check.checked) {
      body.replaceChildren(renderEmpty("需要先批准这次上传", "勾选批准后才会把范围内的文字发给所选 AI 后端。"));
      return;
    }
    try {
      const payload = {
        kind: "self",
        scope: {},
        engine: useAi ? current.id : "local_explicit",
      };
      if (useAi) {
        const ticket = await api("/api/profiles/consent", {
          method: "POST",
          body: JSON.stringify({ kind: "self", scope: {}, engine: current.id, approve_remote: true }),
        });
        payload.approve_remote = true;
        payload.consent_ticket = ticket.ticket_id;
      }
      payload.background=true;
      const task = await api("/api/profiles/runs", {method:"POST",body:JSON.stringify(payload)});
      check.checked=false;
      const done=await waitForAnalysis(task,body);
      const run=await api(`/api/profiles/runs/${done.run_id}`);
      body.replaceChildren();
      renderRunObservations(body, run, "没有提取到可核对的原话计划。这不是失败装点的假报告。");
    } catch (err) {
      body.replaceChildren(renderEmpty("还不能生成画像", String(err.message || err)));
    }
  }

  previewBtn.addEventListener("click", () => paintCoverageStats(body, preview));
  localBtn.addEventListener("click", () => runProfile(false));
  aiBtn.addEventListener("click", () => runProfile(true));
  try {
    preview = await api("/api/profiles/preview", { method: "POST", body: "{}" });
    current = defaultAiEngine(preview);
    pickerHost.replaceChildren(
      aiBackendPicker(preview, "self-ai", current, (engine) => {
        current = engine;
        check.checked=false;
        syncConsent();
      })
    );
    syncConsent();
    paintCoverageStats(body, preview);
    try {
      const listed = await api("/api/profiles/runs?kind=self");
      if (listed.runs && listed.runs[0]) {
        const latest = await api(`/api/profiles/runs/${listed.runs[0].run_id}`);
        if (latest.observations && latest.observations.length) {
          body.appendChild(el("h2", {}, ["最近一次报告"]));
          renderRunObservations(body, latest, "没有提取到可核对的原话计划。这不是失败装点的假报告。");
        }
      }
    } catch (_err) {
      /* 没有历史报告时保持资料范围。 */
    }
  } catch (err) {
    body.replaceChildren(renderEmpty("还不能读取资料范围", String(err.message || err)));
  }
};

window.renderFriendPanel = async function renderFriendPanel() {
  const panel = document.getElementById("panel-friend");
  panel.replaceChildren();
  panel.appendChild(el("h1", {}, ["好友画像"]));
  panel.appendChild(el("p", { class: "muted" }, ["只用对方本人的发言。关系由你补充。"]));
  const convos = ((window.state && window.state.convos) || []).filter((c) => c.conversation_type === "private");
  if (!convos.length) {
    panel.appendChild(renderEmpty("还没有可选好友", "打开档案后，这里列出一对一会话。"));
    return;
  }
  let preview = { engines: [], default_engine: "grok_cli", consent: {} };
  let current = defaultAiEngine(preview);
  let useAi = false;
  const modeRow = el("div", { class: "engine-pick" });
  const localMode = el("button", { type: "button" }, ["本机原话"]);
  const aiMode = el("button", { type: "button", class: "primary" }, ["用 AI"]);
  modeRow.append(el("span", { class: "tiny muted" }, ["整理方式"]), localMode, aiMode);
  const pickerHost = el("div");
  const consentRow = el("label", { class: "readable hidden" });
  const check = el("input", { type: "checkbox" });
  consentRow.append(check, document.createTextNode("批准本次云端分析"));
  panel.append(modeRow, pickerHost, consentRow);
  function syncFriendMode() {
    localMode.classList.toggle("primary", !useAi);
    aiMode.classList.toggle("primary", useAi);
    pickerHost.classList.toggle("hidden", !useAi);
    consentRow.classList.toggle("hidden", !useAi);
    if (useAi) consentRow.replaceChildren(check, document.createTextNode(consentLabel(current, preview)));
  }
  localMode.addEventListener("click", () => {
    useAi = false;
    syncFriendMode();
  });
  aiMode.addEventListener("click", () => {
    useAi = true;
    syncFriendMode();
  });
  try {
    preview = await api("/api/profiles/preview", { method: "POST", body: JSON.stringify({ kind: "friend" }) });
    current = defaultAiEngine(preview);
    pickerHost.replaceChildren(
      aiBackendPicker(preview, "friend-ai", current, (engine) => {
        current = engine;
        syncFriendMode();
      })
    );
    syncFriendMode();
  } catch (err) {
    panel.appendChild(el("p", { class: "muted" }, [String(err.message || err)]));
  }
  const list = el("div", { class: "friend-list" });
  const report = el("div");
  const runBtn = el("button", { type: "button", class: "primary hidden" }, ["生成这份好友画像"]);
  panel.append(list, runBtn, report);
  let selected = null;
  let gen = 0;
  for (const convo of convos) {
    const btn = el("button", { type: "button", class: "person" }, [convo.display_name || convo.conversation_id]);
    btn.addEventListener("click", async () => {
      selected = convo;
      check.checked = false;
      runBtn.classList.remove("hidden");
      const my = ++gen;
      const scope = { friend_sender_ids: [convo.conversation_id], conversation_id: convo.conversation_id };
      report.replaceChildren(el("p", {}, [`已选择 ${convo.display_name || "TA"}，正在查看范围…`]));
      try {
        const next = await api("/api/profiles/preview", { method: "POST", body: JSON.stringify({ kind: "friend", scope }) });
        if (my !== gen) return;
        preview = next;
        paintCoverageStats(report, preview);
        report.insertBefore(el("h2", {}, [`关于 ${convo.display_name || "TA"}`]), report.firstChild);
        syncFriendMode();
      } catch (err) {
        if (my !== gen) return;
        report.replaceChildren(renderEmpty("无法预览", String(err.message || err)));
      }
    });
    list.appendChild(btn);
  }
  runBtn.addEventListener("click", async () => {
    if (!selected) return;
    if (useAi && !current.available) {
      report.replaceChildren(renderEmpty("这个 AI 后端还不能用", "到档案与设置里检查 Grok CLI 或 BYOK。"));
      return;
    }
    if (useAi && !check.checked) {
      report.replaceChildren(renderEmpty("需要先批准这次上传", "勾选批准后才会把对方发言发给所选 AI 后端。"));
      return;
    }
    const my = ++gen;
    const scope = { friend_sender_ids: [selected.conversation_id], conversation_id: selected.conversation_id };
    report.replaceChildren(el("p", {}, ["正在根据对方发言整理…"]));
    try {
      const payload = {
        kind: "friend",
        subject_person_id: selected.conversation_id,
        scope,
        engine: useAi ? current.id : "local_explicit",
      };
      if (useAi) {
        const ticket = await api("/api/profiles/consent", {
          method: "POST",
          body: JSON.stringify({ kind: "friend", scope, engine: current.id, approve_remote: true }),
        });
        payload.approve_remote = true;
        payload.consent_ticket = ticket.ticket_id;
      }
      payload.background=true;
      const task=await api("/api/profiles/runs", {method:"POST",body:JSON.stringify(payload)});
      check.checked=false;
      const done=await waitForAnalysis(task,report);
      const run=await api(`/api/profiles/runs/${done.run_id}`);
      if (my !== gen) return;
      report.replaceChildren();
      report.appendChild(el("h2", {}, [`关于 ${selected.display_name || "TA"}`]));
      renderRunObservations(report, run, "当前文字太少，先不下结论。");
    } catch (err) {
      if (my !== gen) return;
      report.replaceChildren(renderEmpty("无法生成", String(err.message || err)));
    }
  });
};

// Long-running work stays on the local server when the user changes pages.
async function waitForAnalysis(task, host) {
  host.replaceChildren();
  const status = el('p', {class:'notice'}, ['任务已创建，正在处理…']);
  const progress = el('progress', {max:'100', value:'0'});
  const cancel = el('button', {type:'button'}, ['取消任务']);
  host.append(status,progress,cancel);
  cancel.addEventListener('click', async()=>{
    cancel.disabled=true;
    try { await api(`/api/insights/tasks/${task.job_id}/cancel`,{method:'POST',body:'{}'}); }
    catch(err){status.textContent=err.message;cancel.disabled=false;}
  });
  while (true) {
    task=await api(`/api/insights/tasks/${task.job_id}`);
    status.textContent=(task.phase || task.state)+(task.remote?' · 已发出的模型请求不能撤回':'');
    progress.value=task.progress || 0;
    if(task.state==='ready')return task;
    if(['failed','blocked','cancelled'].includes(task.state))throw new Error(task.state==='cancelled'?'任务已取消。':(task.error?.message || '任务中断，请重新开始。'));
    await new Promise(resolve=>setTimeout(resolve,750));
  }
}
window.waitForAnalysis=waitForAnalysis;

async function renderTaskHistory(host) {
  const details=el('details',{class:'task-history'});details.append(el('summary',{},['最近任务与中断恢复']));host.append(details);
  try {
    const data=await api('/api/insights/tasks');
    if(!data.tasks.length){details.append(el('p',{class:'muted'},['还没有任务。']));return;}
    const labels={queued:'等待开始',running:'正在处理',ready:'已完成',failed:'未完成',blocked:'已中断',cancelled:'已取消'};
    for(const task of data.tasks){
      const row=el('div',{class:'task-row'});row.append(el('span',{},[`${task.created_at} · ${labels[task.state] || task.state}`]));
      if(task.state==='ready' && task.run_id){
        const view=el('button',{type:'button'},['查看报告']);view.onclick=async()=>{const run=await api(`/api/profiles/runs/${task.run_id}`);const report=el('div');row.append(report);renderRunObservations(report,run,'没有可展示原话。');view.disabled=true;};row.append(view);
      } else if(['running','queued'].includes(task.state)){
        const follow=el('button',{type:'button'},['查看进度']);follow.onclick=async()=>{const progress=el('div');row.append(progress);try{await waitForAnalysis(task,progress);progress.replaceChildren(el('p',{},['任务已完成，请刷新查看报告。']));}catch(e){progress.textContent=e.message;}};row.append(follow);
      } else if(task.can_restart){
        const restart=el('button',{type:'button'},['重新整理（本机）']);restart.onclick=async()=>{restart.disabled=true;const progress=el('div');row.append(progress);try{const next=await api(`/api/insights/tasks/${task.job_id}/restart`,{method:'POST',body:'{}'});const done=await waitForAnalysis(next,progress);const run=await api(`/api/profiles/runs/${done.run_id}`);progress.replaceChildren();renderRunObservations(progress,run,'没有可展示原话。');}catch(e){progress.textContent=e.message;}};row.append(restart);
      } else if(task.remote && task.state!=='ready')row.append(el('p',{class:'muted'},['云端任务不会自动重发；请重新预览并批准。']));
      details.append(row);
    }
  }catch(e){details.append(el('p',{class:'muted'},[e.message]));}
}

async function renderRecovery(host, context) {
  const section=el('details',{class:'recovery-panel'});section.append(el('summary',{},['找回旧版笔记与学习记录']));host.append(section);
  const hint=el('p',{class:'muted'},['请选择确认属于当前账号的旧记录。只导入缺失项目；冲突保留当前版本，旧库不会删除。']);section.append(hint);
  try{
    const data=await api('/api/insights/recovery');
    if(!data.candidates.length){section.append(el('p',{},['没有找到可恢复的旧记录。']));return;}
    for(const candidate of data.candidates){
      const row=el('section',{class:'entry'});row.append(el('h3',{},[`旧记录 ${candidate.label}`]),el('p',{},[`笔记 ${candidate.counts.notes} · 学习条目 ${candidate.counts.learning_items} · 报告 ${candidate.counts.profile_runs}`]));
      const matches=context.identity && JSON.stringify([...candidate.self_sender_ids].sort())===JSON.stringify([...context.identity.self_sender_ids].sort());
      if(!matches){row.append(el('p',{class:'notice'},['本人身份未确认或不匹配，不能合并。']));section.append(row);continue;}
      const check=el('input',{type:'checkbox'});row.append(el('label',{},[check,' 我确认这是当前账号的旧记录，并同意只导入缺失项目。']));
      const recover=el('button',{type:'button'},['导入缺失记录']);recover.disabled=true;check.onchange=()=>recover.disabled=!check.checked;
      recover.onclick=async()=>{recover.disabled=true;try{const result=await api('/api/insights/recovery',{method:'POST',body:JSON.stringify({candidate_id:candidate.candidate_id,fingerprint:candidate.fingerprint,confirm_recovery:true})});row.append(el('p',{},[`已导入 ${Object.values(result.added).reduce((a,b)=>a+b,0)} 条记录。${result.note}`]));}catch(e){row.append(el('p',{class:'notice'},[e.message]));recover.disabled=false;}};
      row.append(recover);section.append(row);
    }
  }catch(e){section.append(el('p',{},[e.message]));}
}
