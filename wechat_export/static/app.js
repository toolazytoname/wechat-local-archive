const state = {
  meta: null,
  convos: [],
  current: null,
  offset: 0,
  total: 0,
  lastDay: "",
  gen: 0,
};

const $ = (id) => document.getElementById(id);

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error("request failed");
  return res.json();
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

function bubble(msg) {
  const row = el("div", { class: "row" + (msg.is_self ? " self" : "") });
  const wrap = el("div");
  if (!msg.is_self) {
    wrap.appendChild(el("div", { class: "who" }, [msg.sender_display_name || ""]));
  }
  if (msg.readable) {
    wrap.appendChild(el("div", { class: "bubble" }, [msg.text || msg.preview || ""]));
  } else {
    wrap.appendChild(el("div", { class: "card" }, [msg.preview || `[${msg.media_kind || "消息"}]`]));
  }
  row.appendChild(wrap);
  return row;
}

async function openConvo(c, reset, around) {
  const my = ++state.gen;
  state.current = c.conversation_id;
  if (reset) {
    state.offset = 0;
    state.lastDay = "";
    $("thread").replaceChildren();
  }
  $("chat-title").textContent = c.display_name || c.conversation_id;
  $("chat-sub").textContent = `${fmtTime(c.first_timestamp_utc)} — ${fmtTime(c.last_timestamp_utc)}`;
  renderConvos();
  const readable = $("readable").checked ? 1 : 0;
  let url = `/api/messages?conversation_id=${encodeURIComponent(c.conversation_id)}&offset=${state.offset}&limit=80&readable=${readable}`;
  if (around) url += `&around=${encodeURIComponent(around)}`;
  const data = await api(url);
  if (my !== state.gen) return;
  state.total = data.total;
  state.offset = data.offset;
  const thread = $("thread");
  for (const msg of data.messages) {
    const day = dayKey(msg.timestamp_utc);
    if (day && day !== state.lastDay) {
      thread.appendChild(el("div", { class: "day" }, [day]));
      state.lastDay = day;
    }
    const node = bubble(msg);
    node.dataset.uid = msg.record_uid;
    thread.appendChild(node);
  }
  state.offset += data.messages.length;
  const old = thread.querySelector(".more");
  if (old) old.remove();
  if (state.offset < state.total) {
    const more = el("button", { class: "more", type: "button" }, [`继续 ${state.offset}/${state.total}`]);
    more.addEventListener("click", () => openConvo(c, false));
    thread.appendChild(more);
  }
  if (around) {
    const hit = thread.querySelector(`[data-uid="${CSS.escape(around)}"]`);
    if (hit) hit.scrollIntoView({ block: "center" });
  }
}

async function boot() {
  state.meta = await api("/api/meta");
  state.convos = await api("/api/conversations");
  renderMeta();
  renderConvos();
  const want = new URLSearchParams(location.search).get("c");
  const featured = Object.keys(state.meta?.targets || {});
  const initial =
    state.convos.find((c) => c.conversation_id === want) ||
    state.convos.find((c) => featured.includes(c.display_name)) ||
    state.convos[0];
  if (initial) await openConvo(initial, true);
  $("conv-q").addEventListener("input", renderConvos);
  $("readable").addEventListener("change", () => {
    const c = state.convos.find((x) => x.conversation_id === state.current);
    if (c) openConvo(c, true);
  });
  let t = null;
  $("msg-q").addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(runSearch, 200);
  });
  $("open-export").addEventListener("click", () => $("drawer").classList.remove("hidden"));
  $("ex-close").addEventListener("click", () => $("drawer").classList.add("hidden"));
  $("ex-preview").addEventListener("click", previewExport);
  $("ex-go").addEventListener("click", doExport);
  $("open-rail").addEventListener("click", () => $("rail").classList.add("open"));
  $("close-rail").addEventListener("click", () => $("rail").classList.remove("open"));
}

function selectedIds() {
  const scope = $("ex-scope").value;
  if (scope === "current") return state.current ? [state.current] : [];
  if (scope === "featured") {
    const names = Object.keys(state.meta?.targets || {});
    return state.convos.filter((c) => names.includes(c.display_name)).map((c) => c.conversation_id);
  }
  return [];
}

function isoLocal(id) {
  const v = $(id).value;
  if (!v) return null;
  return new Date(v).toISOString();
}

async function previewExport() {
  const ids = selectedIds();
  const q = new URLSearchParams();
  for (const id of ids) q.append("conversation_id", id);
  const since = isoLocal("ex-since");
  const until = isoLocal("ex-until");
  if (since) q.set("since", since);
  if (until) q.set("until", until);
  if ($("ex-readable").checked) q.set("readable", "1");
  const data = await api(`/api/export/preview?${q}`);
  $("ex-count").textContent = `数量预览：${data.count} 条`;
}

async function doExport() {
  $("ex-result").textContent = "正在导出…";
  const result = await api("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_ids: selectedIds(),
      since: isoLocal("ex-since"),
      until: isoLocal("ex-until"),
      readable_only: $("ex-readable").checked,
      format: $("ex-format").value,
    }),
  });
  $("ex-result").textContent = `已写出 ${result.count} 条 → ${result.path}`;
}

async function runSearch() {
  const q = $("msg-q").value.trim();
  const box = $("search-hits");
  if (q.length < 2) {
    box.classList.add("hidden");
    box.replaceChildren();
    return;
  }
  const scoped = state.current ? `&conversation_id=${encodeURIComponent(state.current)}` : "";
  const hits = await api(`/api/search?q=${encodeURIComponent(q)}${scoped}`);
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

boot().catch(() => {
  $("meta-line").textContent = "索引未就绪：python -m wechat_export index";
});
