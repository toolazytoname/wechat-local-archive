const setupState = {
  boot: null,
  selectedAccount: null,
  jobId: null,
  materials: [],
};

function setupEl(tag, attrs, children) {
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

function showSetup() {
  window.archiveUI?.close(false);
  document.getElementById("setup").classList.remove("hidden");
  document.getElementById("archive").classList.add("hidden");
}

function hideSetup() {
  document.getElementById("setup").classList.add("hidden");
  document.getElementById("archive").classList.remove("hidden");
}

function renderStages(compat) {
  const box = document.getElementById("setup-compat");
  box.replaceChildren();
  box.appendChild(setupEl("h2", {}, ["支持判断"]));
  const stages = compat?.stages || {};
  const labels = {
    environment_detected: "已检测环境",
    adapter_candidate: "可能的读取适配",
    key_acquisition_verified: "公开实机验收：取钥",
    codec_verified: "公开实机验收：编解码",
    export_verified: "公开实机验收：导出",
  };
  for (const [key, label] of Object.entries(labels)) {
    const ok = !!stages[key];
    box.appendChild(setupEl("p", { class: ok ? "ok" : "warn" }, [`${ok ? "是" : "否"} · ${label}`]));
  }
  const adapter = compat?.adapter || {};
  box.appendChild(
    setupEl("p", { class: "hint" }, [
      `微信 ${adapter.wechat_version || "未安装"} / build ${adapter.wechat_build || "—"} / ${adapter.machine || ""}。${adapter.notes || ""}`,
    ]),
  );
  const fingerprint = adapter.fingerprint_support?.binding;
  box.appendChild(setupEl("p", { class: "hint" }, [fingerprint
    ? `安装包指纹 ${fingerprint.bundle_sha256.slice(0, 16)}… · 绑定系统、版本与读取器；相同版本号不代表相同模块。`
    : "尚无与当前版本匹配的完整安装包指纹；不会把旧取钥报告当作本次验证。"]));
  if (!compat?.supported_for_guided_read) {
    box.appendChild(
      setupEl("p", { class: "warn" }, [
        "当前组合尚未完成独立实机验收。仅明确列出的候选 build 可在逐项授权后实验读取；不会自动启动副本或取钥。也可直接打开已有档案。",
      ]),
    );
  }
}

const outputState = { selected: "default", locations: [] };
const showGB = n => `${((n || 0) / (1024 ** 3)).toFixed(1)} GiB`;
async function loadOutputLocations() {
  const data = await api("/api/setup/output-locations");
  outputState.locations = data.locations || [];
  const select = document.getElementById("setup-output-select");
  select.replaceChildren();
  for (const location of outputState.locations) {
    const option = setupEl("option", { value: location.destination_id }, [
      `${location.name} · ${location.available ? `剩余 ${showGB(location.free_bytes)}` : "离线 / 已改变"}`,
    ]);
    option.disabled = !location.available;
    select.appendChild(option);
  }
  const chosen = outputState.locations.find(item => item.destination_id === outputState.selected);
  // Never silently redirect a disconnected destination to the default.
  if (!chosen) outputState.selected = "default";
  select.value = outputState.selected;
  renderOutputLocation();
  return data;
}
function renderOutputLocation() {
  const item = outputState.locations.find(x => x.destination_id === outputState.selected);
  document.getElementById("setup-output-status").textContent = item?.available
    ? `最终档案：${item.path}（剩余 ${showGB(item.free_bytes)}）`
    : "选定位置不可用。请重新连接磁盘或明确选择其他位置，再创建新任务。";
}
async function loadStoragePlan() {
  const box = document.getElementById("setup-storage-status");
  if (!setupState.selectedAccount) {
    box.textContent = "选择账号后估算空间；这里只读文件大小，不读取正文。";
    return;
  }
  box.textContent = "正在估算数据库导出空间…";
  try {
    const plan = await api("/api/setup/storage-plan", { method: "POST", body: JSON.stringify({
      account_id: setupState.selectedAccount, destination_id: outputState.selected,
    }) });
    box.textContent = `${plan.estimate_satisfied ? "空间估算通过" : "空间估算不足，请释放空间或更换磁盘"}：本机工作约 ${showGB(plan.work_estimate_bytes)}，最终档案约 ${showGB(plan.output_estimate_bytes)}${plan.same_filesystem ? "（同一磁盘需合计预留）" : "（分属不同磁盘）"}。另需 App 副本与账号配置空间。${plan.note}`;
  } catch (error) { box.textContent = `空间估算不可用：${error.message}。不要将未检测当作空间足够。`; }
}

function renderEnv(env) {
  const box = document.getElementById("setup-env");
  box.replaceChildren();
  box.appendChild(setupEl("h2", {}, ["环境"]));
  const lines = [
    `系统 ${env.mac_ver || ""} ${env.machine || ""}`,
    env.wechat_present ? `微信 ${env.wechat_version || "?"} build ${env.wechat_build || "?"}` : "未找到 /Applications/WeChat.app",
    env.wechat_running ? "微信正在运行" : "微信未运行",
    env.xwechat_root_exists ? "已看到本地数据目录" : "未看到 xwechat_files",
    env.disk?.low_space ? "磁盘空间不足 2GB" : `可用空间约 ${Math.round((env.disk?.free_bytes || 0) / 1e9)} GB`,
  ];
  const tools = env.tools || {};
  lines.push(`工具 sqlcipher=${tools.sqlcipher ? "有" : "无"} lldb=${tools.lldb ? "有" : "无"}`);
  for (const line of lines) box.appendChild(setupEl("p", { class: "hint" }, [line]));
  const help = setupEl("details", {}, [setupEl("summary", {}, ["缺少工具或权限？查看安装说明（不会自动安装）"])]);
  help.appendChild(setupEl("p", { class: "hint" }, ["只打开已有档案不需要 LLDB 或 SQLCipher。首次读取需要你自行安装依赖；以下命令仅展示，不会执行、索取密码或关闭系统保护。安装会联网下载，请先阅读官方说明。"]));
  for (const [label, command, url] of [
    ["Apple 命令行工具（含 LLDB）", "xcode-select --install", "https://developer.apple.com/xcode/resources/"],
    ["已安装 Homebrew 时安装 SQLCipher", "brew install sqlcipher", "https://formulae.brew.sh/formula/sqlcipher"],
  ]) {
    help.appendChild(setupEl("p", {}, [label]));
    help.appendChild(setupEl("code", {}, [command]));
    help.appendChild(setupEl("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, [" 官方说明（外部网站）"]));
  }
  help.appendChild(setupEl("p", { class: "hint" }, ["未安装 Homebrew：请从 brew.sh 阅读官方安装说明，不运行来源不明的解密二进制。权限不足时只给启动本工具的终端所需权限；不要修改微信容器权限，不要关闭 SIP。安装后点击“重新检测环境”。"]));
  const refresh = setupEl("button", { type: "button" }, ["重新检测环境"]);
  refresh.addEventListener("click", () => loadEnvironment().catch(error => { document.getElementById("setup-status").textContent = error.message; }));
  help.appendChild(refresh);
  box.appendChild(help);
}

async function loadEnvironment() {
  const env = await api("/api/setup/environment");
  renderEnv(env);
  renderStages(env.compatibility);
  return env;
}

async function loadAccounts() {
  const box = document.getElementById("setup-accounts");
  box.classList.remove("hidden");
  const data = await api("/api/setup/accounts");
  box.replaceChildren();
  box.appendChild(setupEl("h2", {}, ["选择账号目录"]));
  box.appendChild(setupEl("p", { class: "hint" }, ["目录名不是身份。请你自己确认哪一个是要读取的账号。"]));
  if (!data.accounts || !data.accounts.length) {
    box.appendChild(setupEl("p", { class: "warn" }, [`没有发现账号：${data.status}`]));
    return data;
  }
  for (const acc of data.accounts) {
    const btn = setupEl("button", { type: "button", class: "account-choice" }, [
      setupEl("strong", {}, [acc.dir_name]),
      setupEl("span", {}, [
        `${acc.has_db_storage ? "发现本机记录" : "未发现本机记录"} · ${acc.db_file_count} 个数据文件`,
      ]),
    ]);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
      const chosen = outputState.locations.find(x => x.destination_id === outputState.selected);
      if (!chosen?.available) {
        document.getElementById("setup-status").textContent = "先选择一个可用的档案保存位置。";
        return;
      }
      setupState.selectedAccount = acc.account_id;
      await loadStoragePlan();
      document.getElementById("setup-status").textContent =
        `已选择 ${acc.dir_name}。真实快照/取钥不会自动开始。`;
      const job = await api("/api/workflow/start", {
        method: "POST",
        body: JSON.stringify({
          account_id: acc.account_id,
          destination_id: outputState.selected,
          synthetic: false,
        }),
      });
      setupState.jobId = job.job_id;
      try { localStorage.setItem("wla-read-job", job.job_id); } catch (_) {}
      document.getElementById("setup-workflow").classList.remove("hidden");
      document.getElementById("setup-status").textContent =
        `${job.state}：${job.user_action || job.payload_public?.phase_detail || ""}`;
      renderWorkflow(job);
      } catch (error) {
        document.getElementById("setup-status").textContent = `未能创建任务：${error.message}`;
      } finally { btn.disabled = false; }
    });
    box.appendChild(btn);
  }
  return data;
}

function archiveLabel(item) {
  if (item.kind === "synthetic_demo") return "虚构示例 · 体验功能";
  const match = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(item.name || "");
  return match ? `${match[1]}年${match[2]}月${match[3]}日 ${match[4]}:${match[5]} 的档案` : (item.name || "本地聊天档案");
}
async function loadArchives() {
  const box = document.getElementById("setup-archives-list");
  box.classList.remove("hidden");
  const data = await api("/api/setup/archives");
  const real = data.archives.filter(item => item.kind !== "synthetic_demo").sort((a,b) => b.name.localeCompare(a.name));
  const preferred = real[0];
  setupState.preferredArchive = preferred?.source_id || null;
  document.getElementById("home-current-title").textContent = preferred ? Number(preferred.message_count).toLocaleString() + " 条聊天，已保存在本机" : "还没有导入聊天记录";
  document.getElementById("home-current-meta").textContent = preferred ? "最近保存：" + archiveLabel(preferred) : "可以先试用虚构示例；或点击下方“读取新消息 / 首次导入”检查环境。";
  for (const id of ["home-open", "home-export"]) document.getElementById(id).disabled = !preferred;
  box.replaceChildren(setupEl("h2", {}, ["选择已保存的档案"]));
  if (!real.length) box.appendChild(setupEl("p", {class:"hint"}, ["还没有真实档案。示例数据不会混入你的聊天。"]));
  for (const [index,item] of real.entries()) {
    const details = setupEl("div", {class:"archive-choice-text"}, [
      setupEl("strong", {}, [archiveLabel(item)]),
      setupEl("span", {}, [Number(item.message_count).toLocaleString() + " 条消息 · 本地档案"]),
    ]);
    const btn = setupEl("button", {type:"button",class:"archive-choice"}, [details,
      setupEl("span", {class:index === 0 ? "archive-badge" : "archive-arrow"}, [index === 0 ? "最近保存 · 打开 →" : "打开 →"])]);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { await openRegisteredArchive(item.source_id); }
      catch (error) { document.getElementById("setup-status").textContent = "未能打开档案：" + error.message; }
      finally { btn.disabled = false; }
    });
    box.appendChild(btn);
  }
}

async function openRegisteredArchive(sourceId) {
  const opened = await api("/api/setup/open-archive", {
    method: "POST",
    body: JSON.stringify({ source_id: sourceId }),
  });
  window.ARCHIVE_ID = opened.archive_id;
  hideSetup();
  if (window.startArchive) await window.startArchive();
}

let workflowTimer = null;
function renderWorkflow(job) {
  clearTimeout(workflowTimer);
  if (job.accepted || ["preflight", "snapshotting", "preparing_reader", "acquiring_key", "decrypting", "normalizing", "indexing", "validating"].includes(job.state)) {
    workflowTimer = setTimeout(async () => {
      try { renderWorkflow(await api(`/api/jobs?id=${encodeURIComponent(job.job_id)}`)); }
      catch (_) { document.getElementById("wf-status").textContent = "任务连接中断，重新打开工具查看状态。"; }
    }, 1000);
  }

  const box = document.getElementById("wf-status");
  const pub = job.payload_public || {};
  box.textContent = `${job.state} · ${job.user_action || pub.phase_detail || ""}${pub.destination_path ? ` · 本任务保存位置：${pub.destination_path}` : ""}`;
  if (job.state === "awaiting_sample_check") {
    const accept = setupEl("button", { type: "button" }, ["我已核对这份档案的小样本"]);
    accept.addEventListener("click", async () => {
      renderWorkflow(await api(`/api/workflow/${job.job_id}/advance`, {
        method: "POST", body: JSON.stringify({ command: "confirm_sample", confirm_sample: true }),
      }));
    });
    box.appendChild(accept);
  }
  if (pub.export_source_id) {
    const open = setupEl("button", { type: "button", class: "export-btn" }, ["打开这次快照的导出"]);
    open.addEventListener("click", () => openRegisteredArchive(pub.export_source_id));
    box.appendChild(document.createTextNode(" "));
    box.appendChild(open);
  }
}

async function loadMaterials() {
  const box = document.getElementById("setup-materials");
  box.classList.remove("hidden");
  const data = await api("/api/setup/materials");
  setupState.materials = data.materials || [];
  box.replaceChildren();
  box.appendChild(setupEl("h2", {}, ["已有快照"]));
  if (!setupState.materials.length) {
    box.appendChild(setupEl("p", { class: "hint" }, ["还没有已登记的 idle 快照。"]));
    return;
  }
  setupState.selectedMaterial = null;
  for (const item of setupState.materials) {
    const pick = setupEl("input", { type: "radio", name: "snapshot-choice" });
    pick.addEventListener("change", () => { setupState.selectedMaterial = item.source_id; });
    const label = setupEl("label", {}, [pick, item.source_id]);
    box.appendChild(label);
    box.appendChild(setupEl("p", { class: "hint" }, [
      `${item.source_id} · 解密=${item.has_decrypted ? "有" : "无"}`,
    ]));
  }
}

function bindSetup() {
  if (setupState.bound) return;
  setupState.bound = true;
  document.getElementById("back-setup").addEventListener("click", () => { showSetup(); loadArchives().catch(() => {}); });
  document.getElementById("setup-hide-read").addEventListener("click", () => { document.getElementById("setup-read-panel").classList.add("hidden"); document.getElementById("setup-read").focus(); });
  for (const [id, exporting] of [["home-open", false], ["home-export", true]]) {
    document.getElementById(id).addEventListener("click", async () => {
      if (!setupState.preferredArchive) return;
      const button = document.getElementById(id); button.disabled = true;
      try {
        await openRegisteredArchive(setupState.preferredArchive);
        if (exporting) { document.getElementById("open-export").click(); const select = document.getElementById("ex-scope"); select.value = "all"; select.dispatchEvent(new Event("change")); }
      } catch (error) { showSetup(); document.getElementById("setup-status").textContent = "未能打开档案：" + error.message; }
      finally { button.disabled = false; }
    });
  }
  const advanceJob = async (command, extra = {}) => {
    if (!setupState.jobId) throw new Error("先选择账号。");
    const result = await api(`/api/workflow/${setupState.jobId}/advance`, {
      method: "POST", body: JSON.stringify({ command, ...extra }),
    });
    renderWorkflow(result);
    return result;
  };
  const safeAction = (id, action) => document.getElementById(id).addEventListener("click", async () => {
    const button = document.getElementById(id);
    button.disabled = true;
    try { await action(); }
    catch (error) { document.getElementById("wf-status").textContent = error.message || "任务失败"; }
    finally { button.disabled = false; }
  });
  const outputAction = (id, action) => document.getElementById(id).addEventListener("click", async () => {
    const button = document.getElementById(id);
    button.disabled = true;
    try { await action(); }
    catch (error) { document.getElementById("setup-output-status").textContent = error.message || "位置检测失败"; }
    finally { button.disabled = false; }
  });
  document.getElementById("setup-output-select").addEventListener("change", async event => {
    outputState.selected = event.target.value;
    renderOutputLocation();
    await loadStoragePlan();
  });
  outputAction("setup-choose-output", async () => {
    document.getElementById("setup-output-status").textContent = "请在 macOS 系统窗口选择文件夹；取消不会改变位置。";
    const result = await api("/api/setup/choose-output", { method: "POST", body: "{}" });
    if (result.cancelled) {
      renderOutputLocation();
      document.getElementById("setup-output-status").textContent += " · 已取消选择，位置未改变。";
      return;
    }
    outputState.selected = result.location.destination_id;
    await loadOutputLocations();
    await loadStoragePlan();
  });
  outputAction("setup-refresh-output", async () => { await loadOutputLocations(); await loadStoragePlan(); });
  safeAction("wf-prepare", async () => {
    if (!["wf-preserve", "wf-copy", "wf-key", "wf-enter", "wf-library"].every(id => document.getElementById(id).checked)) {
      throw new Error("先逐项确认保全、共享数据、取钥、人工进入和副本例外。");
    }
    await advanceJob("consent", { confirm_preservation: true, confirm_debug_copy: true, confirm_key_capture: true, confirm_enter_wechat: true });
    const grant = await advanceJob("grant_live_operations", { phrase: "ALLOW_LIVE_WECHAT_STEPS" });
    if (grant.state === "blocked") return;
    await advanceJob("prepare_reader", { confirm_live_step: true, confirm_library_exception: true });
  });
  safeAction("wf-acquire", () => advanceJob("acquire_key", {
    confirm_live_step: true, confirm_library_exception: document.getElementById("wf-library").checked,
  }));
  safeAction("wf-cancel", () => advanceJob("cancel"));

  document.getElementById("setup-read").addEventListener("click", async () => {
    const status = document.getElementById("setup-status");
    const button = document.getElementById("setup-read");
    button.disabled = true;
    document.getElementById("setup-read-panel").classList.remove("hidden");
    document.getElementById("setup-read-panel").scrollIntoView({block: "start"});
    status.textContent = "正在只读检测环境…";
    try {
      const env = await loadEnvironment();
      document.getElementById("setup-read-notice").textContent = env.compatibility?.adapter?.live_operations_eligible
        ? "这个版本可进入实验读取流程，尚不代表全部验证通过。已有档案可以直接打开。"
        : "当前微信版本尚未完成读取验证，不能自动读取新消息。你仍可在上方打开、搜索和导出已有档案。";
      await loadOutputLocations();
      await loadAccounts();
      await loadMaterials();
      document.getElementById("setup-workflow").classList.remove("hidden");
      status.textContent = "只读检测完成。请选择保存位置和账号；不会自动开始读取。";
    } catch (error) { status.textContent = `环境检测未完成：${error.message}`; }
    finally { button.disabled = false; }
  });
  document.getElementById("setup-archives").addEventListener("click", loadArchives);
  document.getElementById("setup-demo").addEventListener("click", () => openRegisteredArchive("demo"));
  document.getElementById("wf-consent").addEventListener("click", async () => {
    if (!setupState.jobId) {
      document.getElementById("wf-status").textContent = "先选择账号。";
      return;
    }
    const job = await api(`/api/workflow/${setupState.jobId}/advance`, {
      method: "POST",
      body: JSON.stringify({
        command: "consent",
        confirm_preservation: document.getElementById("wf-preserve").checked,
        confirm_debug_copy: document.getElementById("wf-copy").checked,
        confirm_key_capture: document.getElementById("wf-key").checked,
        confirm_enter_wechat: document.getElementById("wf-enter").checked,
      }),
    });
    renderWorkflow(job);
  });
  document.getElementById("wf-continue").addEventListener("click", async () => {
    if (!setupState.jobId) {
      document.getElementById("wf-status").textContent = "先选择账号。";
      return;
    }
    const sourceId = setupState.selectedMaterial || "";
    if (!sourceId) {
      document.getElementById("wf-status").textContent = "没有可继续的快照。";
      return;
    }
    const job = await api(`/api/workflow/${setupState.jobId}/advance`, {
      method: "POST",
      body: JSON.stringify({
        command: "continue_from_materials",
        source_id: sourceId,
        confirm_preservation: document.getElementById("wf-preserve").checked,
        confirm_snapshot_account: document.getElementById("wf-snapshot-account").checked,
        display_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    });
    renderWorkflow(job);
    if (job.payload_public && job.payload_public.export_source_id && job.state === "ready") {
      await openRegisteredArchive(job.payload_public.export_source_id);
    }
  });
}

window.showSetup = showSetup;
window.hideSetup = hideSetup;
window.renderSetupBoot = function renderSetupBoot(boot) {
  setupState.boot = boot;
  const recovered = Object.values(boot.runtime?.scratch_cleanup || {});
  const removed = recovered.reduce((sum, item) => sum + (item.removed || 0), 0);
  const unresolved = recovered.reduce((sum, item) => sum + (item.errors || 0) + (item.unknown || 0), 0);
  if (removed || unresolved) {
    document.getElementById("setup-status").textContent = `已回收 ${removed} 个超过 24 小时且无进程持锁的临时目录；原始快照、密钥和正式导出未删除。${unresolved ? " 部分目录未能确认或回收，已保留；可用 cleanup-scratch 只读检查。" : ""}`;
  }
  renderStages(boot.compatibility || {});
  bindSetup();
  loadArchives().catch(error => { document.getElementById("home-current-title").textContent = "暂时无法读取档案列表"; document.getElementById("setup-status").textContent = error.message; });
  loadOutputLocations().then(loadStoragePlan).catch(error => { document.getElementById("setup-output-status").textContent = error.message; });
  let previous = null;
  try { previous = localStorage.getItem("wla-read-job"); } catch (_) {}
  if (previous) {
    api(`/api/jobs?id=${encodeURIComponent(previous)}`).then(job => {
      setupState.jobId = job.job_id;
      document.getElementById("setup-workflow").classList.remove("hidden");
      renderWorkflow(job);
    }).catch(() => { try { localStorage.removeItem("wla-read-job"); } catch (_) {} });
  }
};
