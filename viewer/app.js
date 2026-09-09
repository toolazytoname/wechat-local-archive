const state = {
  meta: null,
  convos: [],
  current: null,
  offset: 0,
  total: 0,
  readable: true,
};

const $ = (id) => document.getElementById(id);

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleString("zh-CN", { hour12: false });
}

function dayKey(ts) {
  return ts ? ts.slice(0, 10) : "";
}

function renderMeta() {
  const m = state.meta;
  $("meta-line").textContent = m
    ? `${m.source_kind || "local"} · ${m.conversation_count} 会话 · ${m.readable_count} 条可读 / ${m.message_count}`
    : "";
}

function renderConvos() {
  const q = $("conv-q").value.trim();
  const pins = $("pins");
  const list = $("convos");
  pins.innerHTML = "";
  list.innerHTML = "";
  const featured = new Set(Object.keys(state.meta?.targets || {}));
  const filtered = state.convos.filter((c) => {
    if (!q) return true;
    return (c.display_name || c.conversation_id).includes(q);
  });
  for (const c of filtered) {
    const btn = document.createElement("button");
    const isPin = featured.has(c.display_name);
    btn.className = isPin ? "pin" : "convo";
    if (state.current === c.conversation_id) btn.classList.add("active");
    btn.innerHTML = `<strong>${c.display_name || c.conversation_id}</strong><span>${c.conversation_type} · ${c.message_count} 条</span>`;
    btn.addEventListener("click", () => openConvo(c));
    (isPin ? pins : list).appendChild(btn);
  }
}

function bubble(msg) {
  const row = document.createElement("div");
  row.className = "row" + (msg.is_self ? " self" : "");
  const body = (msg.readable ? msg.text : msg.preview) || msg.preview || "";
  const inner = msg.readable
    ? `<div class="body"></div>`
    : `<span class="chip"></span>`;
  row.innerHTML = `<div class="bubble"><div class="who"></div>${inner}</div>`;
  row.querySelector(".who").textContent = `${msg.sender_display_name || "未知"} · ${fmtTime(msg.timestamp_utc)}`;
  if (msg.readable) row.querySelector(".body").textContent = body;
  else row.querySelector(".chip").textContent = msg.preview;
  return row;
}

async function openConvo(c, reset = true) {
  state.current = c.conversation_id;
  if (reset) {
    state.offset = 0;
    $("thread").innerHTML = "";
  }
  $("chat-title").textContent = c.display_name || c.conversation_id;
  $("chat-sub").textContent = `${c.conversation_type} · ${fmtTime(c.first_timestamp_utc)} → ${fmtTime(c.last_timestamp_utc)}`;
  renderConvos();
  const readable = $("readable").checked ? 1 : 0;
  const data = await api(
    `/api/messages?conversation_id=${encodeURIComponent(c.conversation_id)}&offset=${state.offset}&limit=80&readable=${readable}`
  );
  state.total = data.total;
  let lastDay = "";
  const thread = $("thread");
  for (const msg of data.messages) {
    const day = dayKey(msg.timestamp_utc);
    if (day && day !== lastDay) {
      const sep = document.createElement("div");
      sep.className = "day";
      sep.textContent = day;
      thread.appendChild(sep);
      lastDay = day;
    }
    thread.appendChild(bubble(msg));
  }
  state.offset += data.messages.length;
  const old = thread.querySelector(".more");
  if (old) old.remove();
  if (state.offset < state.total) {
    const more = document.createElement("button");
    more.className = "more";
    more.textContent = `继续 ${state.offset} / ${state.total}`;
    more.addEventListener("click", () => openConvo(c, false));
    thread.appendChild(more);
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
}

async function runSearch() {
  const q = $("msg-q").value.trim();
  const box = $("search-hits");
  if (q.length < 2) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  const hits = await api(`/api/search?q=${encodeURIComponent(q)}`);
  box.classList.remove("hidden");
  box.innerHTML = hits
    .map(
      (h) =>
        `<button data-cid="${h.conversation_id}"><strong>${h.display_name || h.conversation_id}</strong> · ${fmtTime(h.timestamp_utc)}<br>${h.preview}</button>`
    )
    .join("") || "<p class='muted' style='padding:12px 20px'>没有命中</p>";
  for (const btn of box.querySelectorAll("button")) {
    btn.addEventListener("click", () => {
      const c = state.convos.find((x) => x.conversation_id === btn.dataset.cid);
      if (c) openConvo(c, true);
    });
  }
}

boot().catch((err) => {
  $("meta-line").textContent = "索引未就绪：先运行 python -m wechat_export index";
  console.error(err);
});
