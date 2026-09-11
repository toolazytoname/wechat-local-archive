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
  panel.appendChild(el("p", {}, [`身份检查：${({consistent:"已找到一致的本人标记",missing:"尚未找到本人标记",conflict:"本人标记需要核对"})[audit.verification_state] || "待确认"}`]));
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
  const roleDetails=el("details",{class:"ai-options"});
  roleDetails.append(el("summary",{},["会话用途（可选）：收藏、工作或排除"]));
  panel.append(roleDetails);
  const convos = await conversationChoices();
  const roles = {};
  for (const role of data.conversation_roles || []) roles[role.conversation_id] = role.purpose;
  for (const convo of convos) {
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
    roleDetails.appendChild(row);
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
  panel.appendChild(el("h2", {}, ["AI 服务"]));
  panel.appendChild(el("p", { class: "muted" }, ["配置一个 AI 服务即可；两个都可以配好，生成时用当前默认。密钥只写本机，不会回显。"]));
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
  const grokCmd = field("Grok 程序位置", { type: "text", value: grok.command || "", placeholder: "/Users/…/.grok/bin/grok" });
  const grokModel = field("Grok 模型", { type: "text", value: grok.model || "grok-4.6" });
  const byokUrl = field("自定义 AI 服务地址", { type: "text", value: byok.base_url || "", placeholder: "https://token.weichao.site/v1" });
  const byokModel = field("自定义 AI 模型名称", { type: "text", value: byok.model || "gpt-6-astra" });
  const byokFile = field("API 密钥文件（高级，可留空）", { type: "text", value: "", placeholder: "留空则使用已保存的密钥或下面粘贴的密钥" });
  const byokKey = field("API 密钥（已保存时可留空）", { type: "password", value: "", placeholder: byok.has_api_key ? "已保存，留空不改" : "粘贴密钥，保存后不会再显示" });
  panel.append(grokCmd.wrap, grokModel.wrap, byokUrl.wrap, byokModel.wrap, byokFile.wrap, byokKey.wrap);
  const save = el("button", { type: "button", class: "primary" }, ["保存 AI 设置"]);
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
      status.textContent = `已保存。默认使用 ${saved.default === "byok" ? "自定义 AI" : "Grok"}，可以测试连接了。`;
    } catch (err) {
      status.textContent = String(err.message || err);
    }
  });
  const test=el("button",{type:"button"},["测试已保存的连接（仅发送虚构文字）"]);
  test.onclick=()=>testAiConnection({id:defaultSelect.value},status,test);
  panel.append(el("div",{class:"inline-actions"},[save,test]), status);
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
  if (engine.id === "grok_cli") return engine.available ? `Grok（${engine.model || "默认模型"}）` : "Grok（未安装）";
  if (engine.id === "byok") return engine.available ? `自定义 AI（${engine.model || engine.host || "已配置"}）` : "自定义 AI（未配置）";
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
  box.appendChild(el("span", { class: "tiny muted" }, ["AI 服务"]));
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
  if (run.status === "insufficient") {
    const reason = (run.result || {}).insufficient_reason;
    if (reason === "thin_or_greeting_only") return "资料多是寒暄或短句，不足以整理有上下文的观察";
    return "资料不足";
  }
  if (run.status === "partial") return "仅部分内容可核对，不是画像归纳完成";
  if (run.status === "completed") return "已整理范围内观察；原话可核对，归纳仍待你确认";
  return run.status || "";
}

function dimensionLabel(dimension) {
  return ({
    stated_plans: "明确提过的计划",
    stated_priorities: "在意的事情",
    working_habits: "做事与习惯",
    communication_preferences: "交流方式",
    recurring_topics: "常聊的话题",
    stated_by_friend: "对方的表达",
    scoped_observation: "范围内观察",
  })[dimension] || "范围内观察";
}

function supportLabel(obs) {
  if (obs.support === "excerpt" || obs.basis === "explicit_excerpt" || obs.verification === "excerpt") {
    return "可核对原话";
  }
  return "待核对归纳";
}

function groupObservations(observations) {
  const groups = [];
  const index = {};
  for (const obs of observations || []) {
    if (obs.review_state === "excluded") continue;
    const key = obs.dimension || "scoped_observation";
    if (index[key] == null) {
      index[key] = groups.length;
      groups.push({ dimension: key, items: [] });
    }
    groups[index[key]].items.push(obs);
  }
  return groups;
}

function renderRunObservations(body, run, emptyNote) {
  const exportBtn=el('button',{type:'button',class:'primary'},['导出这份报告']);
  exportBtn.onclick=async()=>{exportBtn.disabled=true;try{const result=await api('/api/insights/exports',{method:'POST',body:JSON.stringify({kind:'profile',run_id:run.run_id})});const actions=el('div',{class:'inline-actions'});const reveal=el('button',{type:'button'},['在访达中显示']);reveal.onclick=()=>api('/api/insights/reveal',{method:'POST',body:JSON.stringify({delivery_id:result.delivery_id})});const openHtml=el('button',{type:'button'},['打开离线网页']);openHtml.onclick=()=>api('/api/insights/reveal',{method:'POST',body:JSON.stringify({delivery_id:result.delivery_id,open_html:true})});actions.append(reveal,openHtml);body.prepend(el('p',{class:'notice'},['已导出 Markdown、JSON 和离线网页。']),actions);}catch(e){body.prepend(el('p',{class:'notice'},[e.message]));}finally{exportBtn.disabled=false;}};
  body.append(exportBtn);
  if (run.is_stale || run.stale) {
    body.appendChild(el("p", { class: "notice" }, [runStatusLabel(run)]));
  }
  const result = run.result || {};
  if (result.sampled_count != null || result.candidate_count != null) {
    body.appendChild(el("p", { class: "tiny muted" }, [
      `候选 ${result.candidate_count || 0} 条，实际取样 ${result.sampled_count || 0} 条，写出观察 ${result.observation_count != null ? result.observation_count : (run.observations || []).length} 条` +
      (result.excerpt_count != null || result.grounded_count != null
        ? `（可核对原话 ${result.excerpt_count || 0} · 待核对归纳 ${result.grounded_count || 0}）`
        : "") +
      "。观察数不是处理消息数。",
    ]));
  }
  body.appendChild(el("p", { class: "tiny muted" }, [
    "报告分层：标题是范围内观察；展开后才是可核对原话。归纳含义仍需你确认。",
  ]));
  if (run.status === "insufficient") {
    const reason = result.insufficient_reason === "thin_or_greeting_only"
      ? "当前范围内多为寒暄、短回复或无实质内容，不能整理成有上下文的画像。可以扩大时间范围，或换一段有具体计划/讨论的对话。"
      : "当前范围内可读发言太少，不能生成观察。";
    body.appendChild(renderEmpty("资料不足", reason));
    return;
  }
  if (run.status === "partial" && !(run.observations || []).length) {
    const rejected = result.rejected_count ? `有 ${result.rejected_count} 条因证据无效、寒暄过短或结论不被原文支持而未写入。` : "云端结果未通过原文核对，所以没有写成画像。";
    body.appendChild(renderEmpty("没有可核对的观察", rejected + " 这不是画像归纳完成。"));
    return;
  }
  const groups = groupObservations(run.observations || []);
  if (!groups.length) {
    body.appendChild(el("p", { class: "notice" }, [emptyNote]));
  }
  for (const group of groups) {
    body.appendChild(el("h2", { class: "profile-chapter" }, [dimensionLabel(group.dimension)]));
    for (const obs of group.items) {
      const entry = el("section", { class: "entry" });
      entry.appendChild(el("h3", {}, [obs.statement]));
      entry.appendChild(el("p", { class: "tiny muted" }, [
        `${supportLabel(obs)} · ${dimensionLabel(obs.dimension)}`,
      ]));
      const evidenceDetails=el("details",{class:"observation-evidence"});
      evidenceDetails.append(
        el("summary",{},["查看可核对原话与说明"]),
        el("p",{class:"tiny muted"},[(obs.caveats || []).join(" ")])
      );
      for(const evidence of obs.evidence || [])evidenceDetails.append(el("blockquote",{},[evidence.quote || ""]));
      entry.append(evidenceDetails);
      const ev = (obs.evidence || [])[0];
      if (ev && ev.record_uid) {
        const jump = el("button", { type: "button", class: "link" }, ["在聊天中查看"]);
        jump.addEventListener("click", async () => {
          jump.disabled=true;
          try {
            const convo=(await conversationChoices()).find(c=>c.conversation_id===ev.conversation_id);
            if(!convo)throw new Error('原会话不在当前档案中，请重新打开档案。');
            showProductPage("chat");
            await window.openConvo(convo, true, ev.record_uid);
          } catch(error){entry.append(el('p',{role:'alert'},[friendlyError(error)]));}
          finally{jump.disabled=false;}
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
}

// Consumer flow: choose a person, review the actual outgoing sample, then approve once.
function friendlyError(error) {
  const messages = {
    identity_unresolved: '请先确认档案里哪个人是你，再生成画像。',
    needs_engine: '还没有可用的 AI 服务。请先到设置中配置并测试连接。',
    needs_consent: '这次确认已失效。请重新点击生成，核对发送内容后再确认。',
    scope_changed: '资料或人物选择已变化。请重新点击生成，确认新的范围。',
    remote_http: 'AI 服务暂时没有返回结果。可以先测试连接，或切换服务后重试。',
    remote_timeout: 'AI 响应超时，任务已停止。请稍后重试或切换服务。',
    remote_auth: 'AI 登录或密钥已失效。请到设置更新后测试连接。',
    remote_rate_limit: 'AI 服务额度不足或请求过于频繁。请检查额度或稍后重试。',
    remote_invalid: 'AI 返回了无法读取的结果。没有保存为报告，请重试。',
    collection_required: '请先选择一个存放收藏的群聊或会话。',
    context_changed: '身份或排除设置已变化。请重新生成，不会沿用旧的批准。',
    interrupted: '上次任务因服务重启而中断。请重新生成。',
    task_timeout: '任务耗时过长，已停止。可以重新开始。',
  };
  return messages[error?.code] || error?.message || '操作未完成，请重试。';
}
function showActionError(host, error) {
  host.replaceChildren(el('p', {class:'notice error-notice',role:'alert'}, [friendlyError(error)]));
}
function settingsButton(text='设置 AI 服务') {
  return el('button', {type:'button',class:'link',onclick:()=>showProductPage('settings')}, [text]);
}
async function conversationChoices() {
  // app.js's lexical `state` was never window.state. Fetch the authoritative list instead.
  return api('/api/conversations');
}
async function testAiConnection(engine, host, button) {
  button.disabled=true;host.replaceChildren(el('p',{role:'status'},['正在用一条虚构文字测试连接，不发送你的聊天…']));
  try {
    const result=await api('/api/insights/engines/test',{method:'POST',body:JSON.stringify({engine:engine.id,confirm_test:true})});
    if(!result.ok) {const err=new Error(result.message);err.code=result.code;throw err;}
    host.replaceChildren(el('p',{class:'success-note',role:'status'},['连接成功，可以生成。测试没有使用你的聊天。']));
  } catch(error){showActionError(host,error);host.append(settingsButton());}
  finally{button.disabled=false;}
}
function createApprovalDialog(engine, preview, title) {
  const dialog=el('dialog',{class:'approval-dialog','aria-label':'确认本次 AI 分析'});
  const close=el('button',{type:'button',class:'link'},['暂不发送']);
  const approve=el('button',{type:'button',class:'primary'},['同意发送并开始生成']);
  const c=preview.consent || {};
  dialog.append(el('p',{class:'eyebrow'},['发送前确认']),el('h2',{},[title]),
    el('p',{},[`本次发送 ${c.upload_count || 0} 条文字摘录，约 ${(c.estimated_chars || 0).toLocaleString()} 字。`]),
    el('p',{class:'muted'},[`接收服务：${engine.host || '所选 AI 服务'} · ${engine.model || engineLabel(engine)}`]),
    el('p',{class:'muted'},['不发送图片、文件或微信密钥。分析是有限抽样，不是全量聊天分析。已发送的内容无法撤回。']),
    el('div',{class:'dialog-actions'},[close,approve]));
  document.body.append(dialog);
  return new Promise(resolve=>{
    let finished=false;
    const finish=value=>{if(finished)return;finished=true;dialog.close();dialog.remove();resolve(value);};
    close.onclick=()=>finish(false);approve.onclick=()=>finish(true);
    dialog.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
    dialog.showModal();close.focus();
  });
}

async function mountProfileComposer(host, {kind,scope={},name='我'}) {
  const marker=el('section',{class:'profile-composer'});host.replaceChildren(marker);
  const intro=el('div',{class:'profile-start'});
  const summary=el('p',{class:'scope-summary'},['正在读取可用资料…']);
  const runButton=el('button',{type:'button',class:'primary'},[kind==='self'?'生成我的画像':`生成好友画像`]);
  runButton.disabled=true;
  const options=el('details',{class:'ai-options'}),optionsSummary=el('summary',{},['AI 服务']);options.append(optionsSummary);
  const info=el('p',{class:'muted small-note'},['从聊天中整理有依据的观察，不是人格鉴定；收藏内容不会自动当作你的观点。']);
  intro.append(summary,info,el('div',{class:'profile-start-actions'},[runButton]),options);
  const feedback=el('div',{class:'action-feedback','aria-live':'polite'}),report=el('div',{class:'profile-report'});
  marker.append(intro,feedback,report);
  let preview,current,busy=false,checkingTasks=true;
  function isCurrent(){return marker.isConnected && host.contains(marker);}
  function sync() {
    current=current || defaultAiEngine(preview);
    const c=preview.coverage || {},count=kind==='self'?c.self_count:c.other_count;
    summary.textContent=kind==='self'?`资料范围：当前档案中的本人发言，排除收藏和手动排除的会话（${(count || 0).toLocaleString()} 条）`:`已选择 ${name} · 仅分析这段私聊中对方的发言`;
    info.textContent=`AI 本次抽取 ${preview.consent?.upload_count || 0} 条文字，不是全量分析。结论须结合原文理解，不是人格鉴定。`;
    optionsSummary.textContent=`AI 服务：${engineLabel(current)} · 更换或检查`;
    runButton.disabled=busy || checkingTasks || !current.available || !preview.consent?.upload_count;
    for(const control of options.querySelectorAll("input,button"))control.disabled=busy || (control.tagName==="INPUT" && !findEngine(preview,control.value).available);
  }
  async function followTask(task) {
    busy=true;sync();
    if(report.querySelector('.getting-started'))report.replaceChildren();
    try {
      const done=await waitForAnalysis(task,feedback,isCurrent);
      if(!isCurrent())return;
      const run=await api(`/api/profiles/runs/${done.run_id}`);
      if(!isCurrent())return;
      feedback.replaceChildren(el('p',{class:'success-note',role:'status'},['画像已保存，可以查看下方报告。']));
      report.replaceChildren(el('h2',{},['本次画像']));renderRunObservations(report,run,'本次没有找到足够的原文依据。');
    } catch(error){if(isCurrent()){showActionError(feedback,error);feedback.append(settingsButton('检查 AI 设置'));}}
    finally{busy=false;if(isCurrent())sync();}
  }
  async function prepare() {
    preview=await api('/api/profiles/preview',{method:'POST',body:JSON.stringify({kind,scope})});
    if(!isCurrent())return false;
    if(preview.needs_identity){
      summary.textContent='第一次使用，请先确认哪些发言属于你。';
      const context=await loadContext();
      const identity=el('button',{type:'button',class:'primary'},['确认记录中的本人身份']);
      identity.onclick=async()=>{identity.disabled=true;try{await api('/api/insights/context',{method:'POST',body:JSON.stringify({accept_consistent_self:true})});await mountProfileComposer(host,{kind,scope,name});}catch(e){showActionError(feedback,e);identity.disabled=false;}};
      feedback.append(context.needs_identity_confirmation?settingsButton('去确认本人身份'):identity);
      return false;
    }
    return true;
  }
  try {
    if(!await prepare())return;
    current=defaultAiEngine(preview);sync();
    const picker=aiBackendPicker(preview,`profile-${kind}`,current,engine=>{current=engine;sync();feedback.replaceChildren();});
    const test=el('button',{type:'button'},['测试连接（仅发送虚构文字）']);
    test.onclick=()=>testAiConnection(current,feedback,test);
    options.append(picker,el('div',{class:'inline-actions'},[test,settingsButton()]));
    if(!current.available){feedback.append(el('p',{class:'notice'},['先连接 AI 服务，才能生成画像。']),settingsButton());options.open=true;}
    else if(!preview.consent?.upload_count)feedback.append(el('p',{class:'notice'},['这份资料暂时没有可分析的文字。可以先在聊天页面检查记录。']));
    const listed=await api(`/api/profiles/runs?kind=${kind}`);
    if(!isCurrent())return;
    const latest=(listed.runs || []).find(r=>r.engine_id!=='local_explicit' && (kind==='self' || (r.scope?.conversation_id===scope.conversation_id)));
    if(latest){const run=await api(`/api/profiles/runs/${latest.run_id}`);if(isCurrent()){report.append(el('h2',{},['最近的画像']));renderRunObservations(report,run,'本次没有找到足够的原文依据。');}}
    else report.append(el('div',{class:'getting-started'},[el('h2',{},[kind==='self'?'从你的聊天中，读懂自己':`为 ${name} 生成第一份画像`]),el('p',{class:'muted'},['生成后可查看观察、回到原聊天核对，也可以导出报告。不会自动发送任何记录。'])]));
    const taskList=await api('/api/insights/tasks');
    if(!isCurrent())return;
    const active=taskList.tasks.find(t=>t.remote && t.kind===kind && ['queued','running'].includes(t.state) && sameProfileScope(t.scope,scope));
    checkingTasks=false;
    if(active)followTask(active);else sync();
  } catch(error){if(isCurrent())showActionError(feedback,error);}
  runButton.onclick=async()=>{
    if(busy || checkingTasks || !current?.available)return;
    busy=true;sync();feedback.replaceChildren(el('p',{role:'status'},['正在确认本次发送范围…']));
    try {
      if(!await prepare())return;
      if(!findEngine(preview,current.id).available)throw Object.assign(new Error(),{code:'needs_engine'});
      current=findEngine(preview,current.id);sync();
      feedback.replaceChildren();
      if(!await createApprovalDialog(current,preview,kind==='self'?'生成我的画像':`生成 ${name} 的画像`))return;
      if(!isCurrent())return;
      const ticket=await api('/api/profiles/consent',{method:'POST',body:JSON.stringify({kind,scope,engine:current.id,approve_remote:true})});
      const task=await api('/api/profiles/runs',{method:'POST',body:JSON.stringify({kind,scope,engine:current.id,approve_remote:true,consent_ticket:ticket.ticket_id,background:true})});
      await followTask(task);
    } catch(error){if(isCurrent()){showActionError(feedback,error);feedback.append(settingsButton('检查 AI 设置'));}}
    finally{busy=false;if(isCurrent())sync();}
  };
}
window.renderSelfPanel=async function(){
  const panel=document.getElementById('panel-self');
  panel.replaceChildren(el('div',{class:'heading'},[el('div',{},[el('h1',{},['我的画像']),el('p',{class:'muted'},['从聊天中发现兴趣、习惯与反复在意的事情。'])])]));
  const composer=el('div');panel.append(composer);
  await mountProfileComposer(composer,{kind:'self'});
  if(panel.contains(composer))renderTaskHistory(panel,'self');
};
window.renderFriendPanel=async function(){
  const panel=document.getElementById('panel-friend');
  panel.replaceChildren(el('div',{class:'heading'},[el('div',{},[el('h1',{},['好友画像']),el('p',{class:'muted'},['先选一位好友，只分析对方在私聊中的发言。'])])]));
  const layout=el('div',{class:'friend-workspace'}),aside=el('section',{class:'friend-picker','aria-label':'选择好友'}),report=el('section',{class:'friend-detail'});
  const search=el('input',{type:'search',placeholder:'搜索好友昵称或备注','aria-label':'搜索好友'}),list=el('div',{class:'friend-list'});
  aside.append(el('h2',{},['选择好友']),search,list);layout.append(aside,report);panel.append(layout);
  report.append(el('div',{class:'getting-started'},[el('h2',{},['你想了解谁？']),el('p',{class:'muted'},['在左侧搜索或选择好友，再生成画像。不会把群聊中的其他人混进来。'])]));
  let selected=null;
  try {
    const convos=(await conversationChoices()).filter(c=>c.conversation_type==='private');
    if(!panel.contains(layout))return;
    const paint=()=>{
      const q=search.value.trim().toLocaleLowerCase();
      const matches=convos.filter(c=>`${c.display_name || ''} ${c.conversation_id}`.toLocaleLowerCase().includes(q));list.replaceChildren();
      if(!matches.length){list.append(el('p',{class:'muted'},[convos.length?'没有找到这个好友，试试备注或昵称。':'这份档案没有一对一聊天记录。']));return;}
      for(const c of matches){
        const button=el('button',{type:'button',class:'person'+(selected===c.conversation_id?' selected':''),'aria-pressed':String(selected===c.conversation_id)},[
          el('span',{class:'person-avatar','aria-hidden':'true'},[(c.display_name || '?').slice(0,1)]),
          el('span',{class:'person-info'},[el('strong',{},[c.display_name || c.conversation_id]),el('small',{class:'muted'},[`${(c.message_count || 0).toLocaleString()} 条记录`])])]);
        button.onclick=()=>{selected=c.conversation_id;paint();mountProfileComposer(report,{kind:'friend',name:c.display_name || '这位好友',scope:{friend_sender_ids:[c.conversation_id],conversation_id:c.conversation_id}});};list.append(button);
      }
    };
    search.oninput=paint;paint();
  } catch(error){showActionError(list,error);}
};

function sameProfileScope(left={}, right={}) {
  const normalized=value=>JSON.stringify(Object.fromEntries(Object.keys(value).sort().map(k=>[k,Array.isArray(value[k])?[...value[k]].sort():value[k]])));
  return normalized(left)===normalized(right);
}
function updateTaskHistoryState(task) {
  const labels={queued:'等待开始',running:'正在处理',ready:'已完成',failed:'未完成',blocked:'已中断',cancelled:'已取消'};
  for(const row of document.querySelectorAll('.task-row[data-task-id]')) {
    if(row.dataset.taskId===task.job_id)row.querySelector('.task-state').textContent=`${new Date(task.created_at).toLocaleString('zh-CN')} · ${labels[task.state] || task.state}`;
  }
}
async function waitForAnalysis(task, host, isCurrent=()=>host.isConnected) {
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
    if(!isCurrent())throw Object.assign(new Error('页面已切换，任务仍在后台运行。'),{code:'view_changed'});
    task=await api(`/api/insights/tasks/${task.job_id}`);
    updateTaskHistoryState(task);
    status.textContent=(task.phase || '正在准备')+(task.remote?' · 等待 AI 返回可能需要几分钟':'');
    progress.value=task.progress || 0;
    if(task.state==='ready')return task;
    if(['failed','blocked','cancelled'].includes(task.state))throw Object.assign(new Error(task.state==='cancelled'?'任务已取消。':(task.error?.message || '任务中断，请重新开始。')),{code:task.error?.code});
    await new Promise(resolve=>setTimeout(resolve,750));
  }
}
window.waitForAnalysis=waitForAnalysis;

async function renderTaskHistory(host, kind) {
  const details=el('details',{class:'task-history'});details.append(el('summary',{},['生成记录']));host.append(details);
  try {
    const data=await api('/api/insights/tasks');
    const tasks=data.tasks.filter(t=>t.remote && (!kind || t.kind===kind));
    if(!tasks.length){details.append(el('p',{class:'muted'},['还没有 AI 生成记录。']));return;}
    const labels={queued:'等待开始',running:'正在处理',ready:'已完成',failed:'未完成',blocked:'已中断',cancelled:'已取消'};
    for(const task of tasks){
      const row=el('div',{class:'task-row','data-task-id':task.job_id});row.append(el('span',{class:'task-state'},[`${new Date(task.created_at).toLocaleString('zh-CN')} · ${labels[task.state] || task.state}`]));
      if(task.state==='ready' && task.run_id){
        const view=el('button',{type:'button'},['查看报告']);view.onclick=async()=>{view.disabled=true;try{const run=await api(`/api/profiles/runs/${task.run_id}`);const report=el('div');row.append(report);renderRunObservations(report,run,'没有可展示原话。');}catch(e){row.append(el('p',{role:'alert'},[friendlyError(e)]));view.disabled=false;}};row.append(view);
      } else if(['running','queued'].includes(task.state)){
        const follow=el('button',{type:'button'},['查看进度']);follow.onclick=async()=>{follow.disabled=true;const progress=el('div');row.append(progress);try{const done=await waitForAnalysis(task,progress);const run=await api(`/api/profiles/runs/${done.run_id}`);progress.replaceChildren(el('p',{class:'success-note'},['任务已完成，报告如下。']));renderRunObservations(progress,run,'没有可展示原话。');}catch(e){progress.textContent=friendlyError(e);}};row.append(follow);
      } else if(task.remote && task.state!=='ready')row.append(el('p',{class:'muted'},[friendlyError(task.error)]));
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
