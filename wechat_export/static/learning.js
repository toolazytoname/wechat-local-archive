window.renderLearningPanel = async function renderLearningPanel() {
  const panel = document.getElementById("panel-learning");
  panel.replaceChildren();
  const heading = el("div", { class: "heading" }, [
    el("div", {}, [el("h1", {}, ["稍后读"]), el("p", { class: "muted" }, ["把存下来的东西，变成真正学到的东西。"])]),
  ]);
  const actions = el("div", { class: "actions" });
  const importBtn = el("button", { type: "button", class: "primary" }, ["整理收藏群"]);
  const exportBtn = el("button", { type: "button" }, ["导出学习资料"]);
  actions.append(importBtn, exportBtn);
  heading.appendChild(actions);
  panel.appendChild(heading);
  const status = el("p", { class: "muted", id: "learning-status" }, ["指定收藏学习用途的会话后，再整理条目。"]);
  const list = el("div", { class: "library-list" });
  panel.append(status, list);

  async function refresh() {
    const data = await api("/api/learning/items");
    list.replaceChildren();
    if (!data.items.length) {
      list.appendChild(renderEmpty("还没有学习条目", "把一个群标成收藏学习，再点整理。不会自动访问网页。"));
      return;
    }
    status.textContent = `${data.count} 个条目 · 收藏不等于认同作者`;
    for (const item of data.items) {
      const btn = el("button", { type: "button", class: "article" });
      btn.appendChild(el("h3", {}, [item.display_title]));
      btn.appendChild(el("p", { class: "meta" }, [`${contentStateLabel(item.content_state)} · ${readingStateLabel(item.reading_state)} · 来源 ${item.sources.length}`]));
      btn.addEventListener("click", () => openLearningItem(item.item_id));
      list.appendChild(btn);
    }
  }

  importBtn.addEventListener("click", async () => {
    status.textContent = "正在离线整理…";
    try {
      const result = await api("/api/learning/imports", { method: "POST", body: "{}" });
      status.textContent = `扫描 ${result.scanned} 条，新增 ${result.items_created}，沿用 ${result.items_reused}`;
      await refresh();
    } catch (err) {
      status.textContent = String(err.message || err);
    }
  });
  exportBtn.addEventListener("click", async () => {
    const result = await api("/api/insights/exports", { method: "POST", body: JSON.stringify({ kind: "learning" }) });
    status.textContent = `已写出 ${result.item_count} 个条目的学习包。`;
    const reveal=el('button',{type:'button'},['在访达中显示']);reveal.onclick=()=>api('/api/insights/reveal',{method:'POST',body:JSON.stringify({delivery_id:result.delivery_id})});status.append(reveal);
  });
  await refresh();
};

function contentStateLabel(state) {
  if (state === "has_body") return "有正文";
  if (state === "has_attachment") return "有附件";
  if (state === "attachment_missing") return "附件未找到";
  if (state === "title_only") return "仅标题";
  return state || "未知";
}

function readingStateLabel(state) {
  if (state === "read") return "已读";
  if (state === "reading") return "阅读中";
  if (state === "archived") return "已归档";
  return "未读";
}

function mediaSrc(uid, download) {
  const extra = download ? "&download=1" : "";
  return `/api/media?uid=${encodeURIComponent(uid)}&archive_id=${encodeURIComponent(window.ARCHIVE_ID || "")}${extra}`;
}

function renderLearningMedia(panel, item) {
  const attachments = item.attachments || [];
  const media = el("div", { class: "reader-media" });
  let hasAvailableAttachment = false;
  let hasMissingAttachment = false;
  const mediaKinds = { image: true, video: true, file: true, voice: true };
  for (const att of attachments) {
    const kind = att.kind || item.kind || "";
    if (att.status !== "available" || !att.record_uid) {
      if (mediaKinds[kind] || mediaKinds[item.kind]) {
        hasMissingAttachment = true;
      }
      continue;
    }
    const mime = att.mime || "";
    if (kind === "image" || mime.startsWith("image/")) {
      const img = el("img", { class: "media-thumb", alt: item.display_title || "图片", src: mediaSrc(att.record_uid) });
      media.appendChild(img);
      media.appendChild(el("a", { href: mediaSrc(att.record_uid, true), download: att.filename || "image" }, ["保存图片"]));
      hasAvailableAttachment = true;
    } else if (kind === "video" || mime.startsWith("video/")) {
      media.appendChild(el("video", { controls: "controls", class: "media-thumb", src: mediaSrc(att.record_uid) }));
      media.appendChild(el("a", { href: mediaSrc(att.record_uid, true), download: att.filename || "video" }, ["保存视频"]));
      hasAvailableAttachment = true;
    } else if (kind === "voice" || mime.startsWith("audio/")) {
      media.appendChild(el("audio", { controls: "controls", src: mediaSrc(att.record_uid) }));
      media.appendChild(el("a", { href: mediaSrc(att.record_uid, true), download: att.filename || "audio" }, ["保存音频"]));
      hasAvailableAttachment = true;
    } else {
      media.appendChild(el("a", { href: mediaSrc(att.record_uid, true), download: att.filename || "attachment" }, [att.filename || "下载文件"]));
      hasAvailableAttachment = true;
    }
  }
  if (hasMissingAttachment && !hasAvailableAttachment) {
    media.appendChild(el("p", { class: "hint" }, ["本机还没有这份附件，不会自动去网上抓。"]));
  }
  let hasExternalLink = false;
  for (const source of item.sources || []) {
    if (source.saved_comment) media.appendChild(el("p", { class: "tiny" }, [`收藏备注：${source.saved_comment}`]));
    const raw = source.original_url;
    if (!raw) continue;
    let destination = null;
    try {
      if (typeof raw === "string" && !/[\s\\\x00-\x1f\x7f]/u.test(raw)) {
        const parsed = new URL(raw);
        if (["https:", "http:"].includes(parsed.protocol) && parsed.hostname && !parsed.username && !parsed.password) destination = parsed;
      }
    } catch (_err) {}
    if (!destination) continue;
    const button = el("button", { type: "button", class: "open-original-link" }, ["打开原链接"]);
    button.addEventListener("click", () => {
      if (window.confirm(`即将在浏览器打开外部网站：${destination.host}\n网站可能收到你的 IP 并要求登录。本工具不会自动抓取正文。是否继续？`)) {
        window.open(destination.href, "_blank", "noopener,noreferrer");
      }
    });
    media.appendChild(button);
    hasExternalLink = true;
  }
  if (hasAvailableAttachment || hasMissingAttachment || hasExternalLink || media.childNodes.length) {
    panel.appendChild(media);
  }
  return {
    hasAvailableAttachment,
    hasMissingAttachment,
    hasExternalLink,
  };
}

async function openLearningItem(itemId) {
  showProductPage("reader");
  const panel = document.getElementById("panel-reader");
  panel.replaceChildren(el("p", {}, ["正在打开…"]));
  const item = await api(`/api/learning/items/${itemId}`);
  panel.replaceChildren();
  const back = el("button", { type: "button", class: "link" }, ["← 返回稍后读"]);
  back.addEventListener("click", () => showProductPage("learning"));
  panel.appendChild(back);
  panel.appendChild(el("h1", {}, [item.display_title]));
  panel.appendChild(el("p", { class: "tiny muted" }, [`${contentStateLabel(item.content_state)} · 作者观点不等于我的观点`]));
  const mediaState = renderLearningMedia(panel, item);
  const body = (item.contents && item.contents.length && item.contents[item.contents.length - 1].body) || "";
  const hasReadableBody = Boolean(body);
  if (hasReadableBody) {
    panel.appendChild(el("div", { class: "reader-paper" }, [el("p", {}, [body])]));
    const sumBtn = el("button", { type: "button" }, ["摘录已有正文"]);
    const sumBox = el("div");
    sumBtn.addEventListener("click", async () => {
      try {
        const summary = await api(`/api/learning/items/${itemId}/summaries`, { method: "POST", body: "{}" });
        const claims = JSON.parse(summary.claims_json || "[]");
        sumBox.replaceChildren();
        for (const claim of claims) sumBox.appendChild(el("p", {}, [claim.text]));
      } catch (err) {
        sumBox.textContent = String(err.message || err);
      }
    });
    panel.append(sumBtn, sumBox);
    renderLearningAnalysis(panel,item);
  } else if (!mediaState.hasAvailableAttachment) {
    panel.appendChild(el("div", { class: "notice" }, ["当前没有正文，也不能根据标题编摘要。可以粘贴已取得的正文。"]));
    const area = el("textarea", { rows: "8", placeholder: "粘贴已取得的正文" });
    const save = el("button", { type: "button", class: "primary" }, ["保存正文"]);
    save.addEventListener("click", async () => {
      await api(`/api/learning/items/${itemId}/content`, { method: "POST", body: JSON.stringify({ text: area.value }) });
      openLearningItem(itemId);
    });
    panel.append(area, save);
  }
  const note = el("textarea", { rows: "4", placeholder: "我的笔记，保存在本机" });
  if (item.notes && item.notes[0]) note.value = item.notes[0].user_text;
  const noteBtn = el("button", { type: "button" }, ["保存笔记"]);
  const noteStatus = el("p", { class: "tiny muted" }, [""]);
  noteBtn.addEventListener("click", async () => {
    const payload = { text: note.value };
    if (item.notes && item.notes[0]) {
      payload.note_id = item.notes[0].note_id;
      payload.revision = item.notes[0].revision;
    }
    const saved = await api(`/api/learning/items/${itemId}/notes`, { method: "POST", body: JSON.stringify(payload) });
    item.notes = [saved, ...(item.notes || []).filter((row) => row.note_id !== saved.note_id)];
    note.value = saved.user_text;
    noteStatus.textContent = "已保存到本机";
  });
  const readBtn = el("button", { type: "button", class: "primary" }, [item.reading_state === "read" ? "已读" : "标记已读"]);
  readBtn.addEventListener("click", async () => {
    await api(`/api/learning/items/${itemId}`, { method: "PATCH", body: JSON.stringify({ reading_state: "read", revision: item.revision }) });
    item.revision += 1;
    item.reading_state = "read";
    readBtn.textContent = "已读";
    readBtn.disabled=true;
  });
  panel.append(el("h3", {}, ["我的笔记"]), note, noteBtn, noteStatus, readBtn);
}

window.openLearningItem = openLearningItem;

async function renderLearningAnalysis(panel,item) {
  const area=el('section',{class:'summary-draft'});panel.append(area);
  area.append(el('h2',{},['归纳与复习']),el('p',{class:'muted'},['本机摘录不会上传。AI归纳需要逐次批准；结果是学习草稿，不把文章观点当作你的观点。']));
  const current=item.contents?.[item.contents.length-1]?.content_id;
  for(const summary of item.summaries || []){
    if(summary.content_id!==current)continue;
    const claims=JSON.parse(summary.claims_json || '[]');
    area.append(el('h3',{},[summary.status==='draft'?'AI学习草稿 · 请核对':'本机正文摘录']));
    for(const claim of claims){area.append(el('p',{},[claim.text || '']));if(claim.quote)area.append(el('blockquote',{},[claim.quote]));}
  }
  for(const card of item.review_cards || []){
    if(card.content_id!==current)continue;
    const details=el('details',{class:'review-card'});details.append(el('summary',{},[card.question]),el('p',{},[card.answer]));
    if(card.user_state==='draft')details.append(el('small',{class:'muted'},['AI生成，答案需核对原文。']));
    area.append(details);
  }
  const open=el('button',{type:'button'},['用 AI 整理核心观点与复习题']);area.append(open);
  open.onclick=async()=>{
    open.disabled=true;
    const controls=el('div');area.append(controls);
    try{
      const preview=await api(`/api/learning/items/${item.item_id}/summary-preview`,{method:'POST',body:'{}'});
      let engine=defaultAiEngine(preview);
      const check=el('input',{type:'checkbox'}),label=el('label',{class:'readable'}),run=el('button',{type:'button',class:'primary'},['批准并开始整理']);
      const sync=()=>{check.checked=false;run.disabled=true;label.replaceChildren(check,document.createTextNode(` 批准发送本篇正文 ${preview.chars} 字，最多 ${preview.request_count} 次模型请求，到 ${engine.host || '所选服务'}（${engine.model || engineLabel(engine)}）；不发送聊天、附件或密钥。`));};
      controls.append(aiBackendPicker(preview,'learning-ai',engine,e=>{engine=e;sync();}),label,run);sync();
      check.onchange=()=>run.disabled=!check.checked || !engine.available;
      run.onclick=async()=>{
        run.disabled=true;check.checked=false;
        try{
          const ticket=await api(`/api/learning/items/${item.item_id}/summary-consent`,{method:'POST',body:JSON.stringify({engine:engine.id,approve_remote:true})});
          const task=await api(`/api/learning/items/${item.item_id}/summaries`,{method:'POST',body:JSON.stringify({mode:'remote',engine:engine.id,approve_remote:true,consent_ticket:ticket.ticket_id})});
          await waitForAnalysis(task,controls);await openLearningItem(item.item_id);
        }catch(e){controls.append(el('p',{class:'notice'},[e.message]));}
      };
    }catch(e){controls.textContent=e.message;open.disabled=false;}
  };
}
