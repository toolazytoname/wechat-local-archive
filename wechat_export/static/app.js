const state = {
  meta: null,
  convos: [],
  current: null,
  total: 0,
  hasOlder: false,
  oldest: null,
  loading: false,
  exporting: false,
  gen: 0,
  searchGen: 0,
  followTail: true,
  exportSelected: new Set(),
};

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const archiveScoped = /^\/api\/(meta|conversations|messages|search|export)([/?]|$)/.test(path);
  const requestArchive = window.ARCHIVE_ID;
  if (archiveScoped && requestArchive) headers["X-Archive-ID"] = requestArchive;
  if (window.CSRF) headers["X-CSRF-Token"] = window.CSRF;
  if (opts.method && opts.method !== "GET" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (data.code === "archive_changed" || data.code === "archive_source_changed") {
      window.archiveUI.close(false);
      showSetup();
      $("setup-status").textContent = "档案已切换或被修改。请重新选择档案并预览，旧标签页不会导出其他账号。";
    }
    throw new Error(data.error || "request failed");
  }
  if (archiveScoped && requestArchive !== window.ARCHIVE_ID) throw new Error("档案已切换，忽略旧请求结果。");
  return data;
}

window.api = api;

function clearArchiveError() {
  $("archive-error").classList.add("hidden");
  $("archive-retry").onclick = null;
}

function showArchiveError(error, retry) {
  $("archive-error-message").textContent = error.message || "读取失败，请重试。";
  $("archive-error").classList.remove("hidden");
  $("archive-retry").onclick = async () => {
    clearArchiveError();
    try { await retry(); } catch (next) { showArchiveError(next, retry); }
  };
}

function tz() {
  return state.meta?.display_timezone || "America/Los_Angeles";
}

function fmtTime(ts) {
  if (!ts) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: tz(),
    hour12: false,
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ts));
}

function dayKey(ts) {
  if (!ts) return "";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(ts));
}

function initial(name) {
  const s = (name || "?").trim();
  return s.slice(0, 1);
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (v != null) node.setAttribute(k, v);
    }
  }
  for (const child of children || []) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function renderMeta() {
  const m = state.meta;
  $("meta-line").textContent = m
    ? `${m.source_kind || "local"} · ${m.conversation_count} 会话 · ${m.readable_count}/${m.message_count}`
    : "";
}

function convoButton(c) {
  const featured = new Set(Object.keys(state.meta?.targets || {}));
  const isPin = featured.has(c.display_name);
  const btn = el("button", {
    class: (isPin ? "pin" : "convo") + (state.current === c.conversation_id ? " active" : ""),
    type: "button",
    dataset: { cid: c.conversation_id },
  }, [
    el("div", { class: "avatar" }, [initial(c.display_name)]),
    el("div", {}, [
      el("strong", {}, [c.display_name || c.conversation_id]),
      el("span", {}, [`${c.conversation_type} · ${c.message_count}`]),
    ]),
    el("div", { class: "time" }, [fmtTime(c.last_timestamp_utc)]),
  ]);
  btn.addEventListener("click", () => openConvo(c, true));
  return btn;
}

function renderConvos() {
  const q = $("conv-q").value.trim();
  $("pins").replaceChildren();
  $("convos").replaceChildren();
  const featured = new Set(Object.keys(state.meta?.targets || {}));
  for (const c of state.convos) {
    const name = c.display_name || c.conversation_id;
    if (q && !name.includes(q) && !c.conversation_id.includes(q)) continue;
    (featured.has(c.display_name) ? $("pins") : $("convos")).appendChild(convoButton(c));
  }
}

function settleMedia(node) {
  const follow = () => requestAnimationFrame(() => {
    if (state.followTail && node.isConnected) $("thread").scrollTop = $("thread").scrollHeight;
  });
  node.addEventListener("load", follow);
  node.addEventListener("loadedmetadata", follow);
  // Error handlers may replace the media node before the next frame.
  node.addEventListener("error", () => {
    const currentThread = node.closest("#thread");
    const generation = state.gen;
    requestAnimationFrame(() => {
      if (currentThread && generation === state.gen && state.followTail) currentThread.scrollTop = currentThread.scrollHeight;
    });
  });
}

function mediaCard(msg) {
  const kind = msg.media_kind || "消息";
  const recovered = msg.attachment;
  const available = recovered?.status === "available";
  const mediaURL = `/api/media?uid=${encodeURIComponent(msg.record_uid)}&archive_id=${encodeURIComponent(window.ARCHIVE_ID || "")}`;
  const wrap = el("div", { class: "card media-card" });
  wrap.appendChild(el("div", {}, [msg.preview || `[${kind}]`]));
  let card = null;
  try { card = msg.card_json ? JSON.parse(msg.card_json) : msg.card; } catch (_) {}
  if (card && typeof card === "object") {
    if (card.kind === "quote") {
      wrap.appendChild(el("blockquote", { class: "quote-preview" }, [
        el("strong", {}, [card.author || "引用消息"]),
        el("p", {}, [card.quoted_text || "引用内容未解析"]),
      ]));
    } else if (card.kind === "forwarded") {
      const items = Array.isArray(card.items) ? card.items.slice(0, 8) : [];
      for (const item of items) wrap.appendChild(el("p", { class: "forward-preview" }, [
        el("strong", {}, [item.author ? item.author + "：" : ""]), item.text || "[非文字消息]",
      ]));
      wrap.appendChild(el("div", { class: "hint" }, [
        card.item_count == null ? "转发详情未解析" : `${card.item_count} 条转发消息${card.truncated ? " · 仅预览前 8 条" : ""}`,
      ]));
    } else if (card.kind === "file") {
      wrap.appendChild(el("div", { class: "hint" }, [
        `${card.extension || "文件"}${card.size_bytes != null ? " · " + card.size_bytes + " 字节" : ""}${available ? " · 文件已保存到本机" : " · 尚未取得本地文件"}`,
      ]));
    } else if (card.kind === "link") {
      if (card.description) wrap.appendChild(el("p", {}, [card.description]));
      let destination = null;
      try {
        if (typeof card.url === "string" && !/[\s\\\x00-\x1f\x7f]/u.test(card.url)) {
          const parsed = new URL(card.url);
          if (["https:", "http:"].includes(parsed.protocol) && parsed.hostname && !parsed.username && !parsed.password) destination = parsed;
        }
      } catch (_) {}
      if (destination) {
        const button = el("button", {type: "button", class: "open-original-link"}, ["打开原链接"]);
        button.addEventListener("click", () => {
          if (window.confirm(`即将在浏览器打开外部网站：${destination.host}\n网站可能收到你的 IP 并要求登录。本工具不会上传整段聊天。是否继续？`)) {
            window.open(destination.href, "_blank", "noopener,noreferrer");
          }
        });
        wrap.appendChild(button);
        wrap.appendChild(el("div", {class: "hint"}, [destination.host + " · 点击后才访问外网"]));
      } else {
        wrap.appendChild(el("div", {class: "hint"}, ["原链接缺失或不是可打开的网页地址"]));
      }
    }
  }
  if (available) {
    const actions = el("div", {class:"attachment-actions"});
    if (recovered.mime?.startsWith("image/")) actions.appendChild(el("a", {href:mediaURL,target:"_blank",rel:"noopener noreferrer"}, ["查看大图"]));
    actions.appendChild(el("a", {href:mediaURL+"&download=1",download:recovered.filename || "attachment"}, [kind === "file" ? "下载文件" : "保存到本机"]));
    wrap.appendChild(actions);
    wrap.appendChild(el("div", {class:"hint"}, [recovered.representation === "preview" ? "预览图 · 原始图片或视频尚未恢复" : recovered.representation === "original_verified" ? "原文件已校验" : "已找到本地文件"]));
  }
  if (kind === "image" || (kind === "video" && available && recovered.mime?.startsWith("image/"))) {
    const img = el("img", {
      class: "media-thumb",
      loading: "lazy",
      alt: msg.media_title || "图片",
      src: `/api/media?uid=${encodeURIComponent(msg.record_uid)}&archive_id=${encodeURIComponent(window.ARCHIVE_ID || "")}`,
    });
    settleMedia(img);
    img.addEventListener("error", () => {
      img.replaceWith(el("div", { class: "hint" }, [recovered?.status === "missing" ? "本机没有找到可读取的图片；可能只剩加密原件" : "图片暂时无法显示"]));
    });
    wrap.appendChild(img);
  } else if (kind === "voice") {
    const audio = el("audio", { controls: "controls", src: `/api/media?uid=${encodeURIComponent(msg.record_uid)}&archive_id=${encodeURIComponent(window.ARCHIVE_ID || "")}` });
    settleMedia(audio);
    audio.addEventListener("error", () => {
      audio.replaceWith(el("div", { class: "hint" }, [msg.duration_ms ? `语音 ${Math.round(msg.duration_ms / 1000)}s，文件未取得` : "语音未取得"]));
    });
    wrap.appendChild(audio);
  } else if (kind === "video") {
    const video = el("video", { controls: "controls", class: "media-thumb", src: `/api/media?uid=${encodeURIComponent(msg.record_uid)}&archive_id=${encodeURIComponent(window.ARCHIVE_ID || "")}` });
    settleMedia(video);
    video.addEventListener("error", () => {
      video.replaceWith(el("div", { class: "hint" }, ["视频未取得"]));
    });
    wrap.appendChild(video);
  }
  return wrap;
}

function bubble(msg) {
  const row = el("div", { class: "row" + (msg.is_self ? " self" : "") });
  const wrap = el("div", {class: "message-wrap"});
  const avatar = el("div", {class: "message-avatar", "aria-hidden": "true"}, [msg.is_self ? "我" : initial(msg.sender_display_name)]);
  if (!msg.is_self) row.appendChild(avatar);
  if (!msg.is_self) {
    wrap.appendChild(el("div", { class: "who" }, [msg.sender_display_name || ""]));
  }
  if (msg.readable) {
    wrap.appendChild(el("div", { class: "bubble" }, [msg.text || msg.preview || ""]));
  } else {
    wrap.appendChild(mediaCard(msg));
  }
  row.appendChild(wrap);
  if (msg.is_self) row.appendChild(avatar);
  row.dataset.uid = msg.record_uid;
  row.dataset.ts = msg.timestamp_utc || "";
  row.dataset.day = dayKey(msg.timestamp_utc);
  return row;
}

function paintMessages(messages, mode) {
  const thread = $("thread");
  const frag = document.createDocumentFragment();
  let lastDay = mode === "append" ? state.lastDay : "";
  const nodes = [];
  for (const msg of messages) {
    const day = dayKey(msg.timestamp_utc);
    if (day && day !== lastDay) {
      nodes.push(el("div", { class: "day" }, [day]));
      lastDay = day;
    }
    nodes.push(bubble(msg));
  }
  if (mode === "prepend") {
    for (const node of nodes) frag.appendChild(node);
    thread.insertBefore(frag, thread.firstChild);
  } else {
    for (const node of nodes) thread.appendChild(node);
    state.lastDay = lastDay;
  }
}

async function openConvo(c, reset, around) {
  window.archiveUI.close();
  clearArchiveError();
  const my = ++state.gen;
  state.followTail = !around;
  state.current = c.conversation_id;
  state.loading = true;
  state.searchGen += 1;
  $("search-hits").classList.add("hidden");
  $("search-hits").replaceChildren();
  if (reset) {
    state.lastDay = "";
    state.oldest = null;
    $("thread").replaceChildren();
  }
  $("chat-title").textContent = c.display_name || c.conversation_id;
  $("chat-sub").textContent = `${fmtTime(c.first_timestamp_utc)} — ${fmtTime(c.last_timestamp_utc)}`;
  renderConvos();
  const readable = $("readable").checked ? 1 : 0;
  let url = `/api/messages?conversation_id=${encodeURIComponent(c.conversation_id)}&limit=80&readable=${readable}`;
  if (around) url += `&around=${encodeURIComponent(around)}`;
  try {
    const data = await api(url);
    if (my !== state.gen) return;
    state.total = data.total;
    state.hasOlder = data.has_older;
    const thread = $("thread");
    if (reset) thread.replaceChildren();
    paintMessages(data.messages, "append");
    if (data.messages.length) {
      state.oldest = { ts: data.messages[0].timestamp_utc, uid: data.messages[0].record_uid };
    }
    state.loading = false;
    if (around) {
      const hit = thread.querySelector(`[data-uid="${CSS.escape(around)}"]`);
      if (hit) hit.scrollIntoView({ block: "center" });
    } else {
      thread.scrollTop = thread.scrollHeight;
    }
    syncExportButton();
  } catch (error) {
    if (my === state.gen) showArchiveError(error, () => openConvo(c, true, around));
  } finally {
    if (my === state.gen) state.loading = false;
  }
}

async function loadOlder() {
  if (!state.current || !state.hasOlder || state.loading || !state.oldest) return;
  const c = state.convos.find((x) => x.conversation_id === state.current);
  if (!c) return;
  state.loading = true;
  const thread = $("thread");
  const prevHeight = thread.scrollHeight;
  const readable = $("readable").checked ? 1 : 0;
  const generation = state.gen;
  try {
    const data = await api(
      `/api/messages?conversation_id=${encodeURIComponent(c.conversation_id)}&limit=80&readable=${readable}` +
        `&before_ts=${encodeURIComponent(state.oldest.ts)}&before_uid=${encodeURIComponent(state.oldest.uid)}`,
    );
    if (generation !== state.gen) return;
    paintMessages(data.messages, "prepend");
    if (data.messages.length) {
      state.oldest = { ts: data.messages[0].timestamp_utc, uid: data.messages[0].record_uid };
    }
    state.hasOlder = data.has_older;
    thread.scrollTop = thread.scrollHeight - prevHeight;
    state.loading = false;
  } catch (error) {
    if (generation === state.gen) showArchiveError(error, loadOlder);
  } finally {
    if (generation === state.gen) state.loading = false;
  }
}

async function startArchive() {
  state.gen += 1;
  clearArchiveError();
  state.current = null;
  state.oldest = null;
  state.loading = false;
  $("thread").replaceChildren();
  $("search-hits").replaceChildren();
  state.exportSelected.clear();
  $("ex-conversations").replaceChildren();
  $("ex-timezone-note").textContent = `未带偏移的时间按 ${Intl.DateTimeFormat().resolvedOptions().timeZone} 解释；重复的夏令时时间必须带偏移。区间包含开始、不包含结束。`;
  state.meta = await api("/api/meta");
  state.convos = await api("/api/conversations");
  renderMeta();
  renderConvos();
  const want = new URLSearchParams(location.search).get("c");
  const featured = Object.keys(state.meta?.targets || {});
  const initialConvo =
    state.convos.find((c) => c.conversation_id === want) ||
    state.convos.find((c) => featured.includes(c.display_name)) ||
    state.convos[0];
  if (initialConvo) await openConvo(initialConvo, true);
  $("conv-q").oninput = renderConvos;
  $("readable").onchange = () => {
    const c = state.convos.find((x) => x.conversation_id === state.current);
    if (c) openConvo(c, true);
  };
  let t = null;
  $("msg-q").oninput = () => {
    clearTimeout(t);
    t = setTimeout(() => runSearch().catch(error => showArchiveError(error, runSearch)), 200);
  };
  $("open-export").onclick = () => { syncExportButton(); window.archiveUI.open("drawer"); };
  $("ex-close").onclick = () => window.archiveUI.close();
  $("ex-preview").onclick = async () => {
    try { await previewExport(); }
    catch (error) { $("ex-count").textContent = error.message || "无法预览"; }
  };
  $("ex-go").onclick = doExport;
  $("ex-scope").onchange = syncExportButton;
  $("open-rail").onclick = () => window.archiveUI.open("rail");
  $("close-rail").onclick = () => window.archiveUI.close();
  for (const event of ["onwheel", "ontouchstart", "onpointerdown", "onkeydown"]) {
    $("thread")[event] = () => { state.followTail = false; };
  }
  $("thread").onscroll = () => {
    if ($("thread").scrollTop < 48) loadOlder();
  };
  syncExportButton();
}

window.startArchive = startArchive;

function selectedIds() {
  const scope = $("ex-scope").value;
  if (scope === "selected") return [...state.exportSelected];
  if (scope === "groups") return state.convos.filter(c => c.conversation_type === "room" || c.conversation_id.endsWith("@chatroom")).map(c => c.conversation_id);
  if (scope === "private") return state.convos.filter(c => c.conversation_type === "private").map(c => c.conversation_id);
  if (scope === "current") return state.current ? [state.current] : [];
  if (scope === "featured") {
    const names = Object.keys(state.meta?.targets || {});
    return state.convos.filter((c) => names.includes(c.display_name)).map((c) => c.conversation_id);
  }
  return [];
}

function syncExportButton() {
  const scope = $("ex-scope").value;
  const chooser = $("ex-conversations");
  chooser.classList.toggle("hidden", scope !== "selected");
  if (scope === "selected" && !chooser.childElementCount) {
    for (const c of state.convos) {
      const check = el("input", { type: "checkbox" });
      check.checked = state.exportSelected.has(c.conversation_id);
      check.addEventListener("change", () => {
        if (check.checked) state.exportSelected.add(c.conversation_id);
        else state.exportSelected.delete(c.conversation_id);
        syncExportButton();
      });
      chooser.appendChild(el("label", {}, [check, c.display_name || c.conversation_id]));
    }
  }
  const ok = scope === "all" || selectedIds().length > 0;
  $("ex-go").disabled = !ok || state.exporting;
  $("ex-preview").disabled = !ok;
  if (!ok) $("ex-count").textContent = "请先选择会话，空选择不会导出全部";
}

function isoBound(id) {
  // Do not let Date normalize invalid dates or choose a DST fold silently.
  return $(id).value.trim() || null;
}

function exportPayload() {
  const scope = $("ex-scope").value;
  const body = {
    since: isoBound("ex-since", false),
    until: isoBound("ex-until", true),
    readable_only: $("ex-readable").checked,
    format: $("ex-format").value,
    mode: $("ex-mode").value,
    message_types: $("ex-type").value ? [$("ex-type").value] : [],
    display_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  if (scope === "all") {
    body.scope = { kind: "all" };
  } else {
    body.scope = { kind: "conversations", conversation_ids: selectedIds() };
  }
  return body;
}

async function previewExport() {
  const body = exportPayload();
  const q = new URLSearchParams();
  q.set("scope", body.scope.kind);
  q.set("display_timezone", body.display_timezone);
  for (const id of body.scope.conversation_ids || []) q.append("conversation_id", id);
  for (const type of body.message_types) q.append("message_type", type);
  if (body.since) q.set("since", body.since);
  if (body.until) q.set("until", body.until);
  if (body.readable_only) q.set("readable", "1");
  const data = await api(`/api/export/preview?${q}`);
  const counts = data.selection_accounting;
  const excluded = counts ? ` · 同范围 ${counts.candidate_count} 条，可读内容过滤排除 ${counts.excluded_unreadable_count} 条` : "";
  $("ex-count").textContent = `数量预览：${data.count} 条${excluded} · 区间 ${data.query?.interval || "[since,until)"}`;
}

async function doExport() {
  if (state.exporting) return;
  state.exporting = true;
  $("ex-result").textContent = "已排队…";
  $("ex-reveal").classList.add("hidden");
  $("ex-go").disabled = true;
  try {
    const created = await api("/api/export", {
      method: "POST",
      body: JSON.stringify(exportPayload()),
    });
    const jobId = created.job_id;
    $("ex-cancel").classList.remove("hidden");
    $("ex-cancel").onclick = async () => {
      try { await api(`/api/jobs/${jobId}/cancel`, { method: "POST", body: "{}" }); }
      catch (error) { $("ex-result").textContent = `取消请求未确认：${error.message}。任务可能仍在运行。`; }
    };
    $("ex-result").textContent = `任务 ${jobId.slice(0, 8)} 已排队，共 ${created.total} 条`;
    let job = created;
    while (true) {
      job = await api(`/api/jobs?id=${encodeURIComponent(jobId)}`);
      const written = job.payload_public?.written ?? job.progress ?? 0;
      const total = job.payload_public?.total ?? created.total;
      $("ex-result").textContent = `${job.state} ${written}/${total}`;
      if (job.state === "ready") {
        $("ex-result").textContent = `已写出 ${job.payload_public?.written || 0} 条；同时生成范围报告和附件清单。本次未导出附件二进制。`;
        $("ex-reveal").classList.remove("hidden");
        $("ex-reveal").onclick = async () => {
          try { await api(`/api/jobs/${jobId}/reveal`, { method: "POST", body: "{}" }); }
          catch (error) { $("ex-result").textContent = `文件已导出，但无法打开访达：${error.message}`; }
        };
        break;
      }
      if (job.state === "failed" || job.state === "cancelled") {
        $("ex-result").textContent = job.error?.error || job.state;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  } catch (err) {
    $("ex-result").textContent = String(err.message || err);
  }
  $("ex-cancel").classList.add("hidden");
  state.exporting = false;
  syncExportButton();
}

async function runSearch() {
  const generation = ++state.searchGen;
  const conversation = state.current;
  const q = $("msg-q").value.trim();
  const box = $("search-hits");
  if (q.length < 2) {
    box.classList.add("hidden");
    box.replaceChildren();
    return;
  }
  const scoped = state.current ? `&conversation_id=${encodeURIComponent(state.current)}` : "";
  const hits = await api(`/api/search?q=${encodeURIComponent(q)}${scoped}`);
  if (generation !== state.searchGen || conversation !== state.current || q !== $("msg-q").value.trim()) return;
  box.classList.remove("hidden");
  box.replaceChildren();
  if (!hits.length) {
    box.appendChild(el("p", { class: "hint" }, ["没有命中"]));
    return;
  }
  for (const h of hits) {
    const btn = el("button", { type: "button" });
    btn.appendChild(el("strong", {}, [h.display_name || h.conversation_id]));
    btn.appendChild(document.createTextNode(` · ${fmtTime(h.timestamp_utc)} `));
    btn.appendChild(document.createTextNode(h.preview || ""));
    btn.addEventListener("click", () => {
      const c = state.convos.find((x) => x.conversation_id === h.conversation_id);
      if (c) openConvo(c, true, h.record_uid);
    });
    box.appendChild(btn);
  }
}

async function boot() {
  const boot = await api("/api/bootstrap");
  window.CSRF = boot.csrf;
  window.ARCHIVE_ID = boot.archive_id;
  if (window.renderSetupBoot) window.renderSetupBoot(boot);
  if (boot.mode === "setup" || new URLSearchParams(location.search).get("home") === "1") {
    showSetup();
    return;
  }
  hideSetup();
  await startArchive();
}

boot().catch(error => {
  showSetup();
  $("setup-status").textContent = "无法启动本地界面，请确认服务仍在运行并刷新重试。";
});
