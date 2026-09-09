# 审计报告：wechat-local-archive

日期：2026-09-09。被审版本：`dccb8bd0ba75cc911fc8beca90d10153c9d81c4f`。

## 结论

**已有可用的本地数据库读取与结构化导出成果，但不建议把当前版本认定为隐私审查通过、强制仅本机访问或已完成的通用导出产品。**

本报告不包含任何真实聊天正文、密钥、联系人名称或私有档案内容。真实档案只在已有本机 checkout 中做只读聚合检查；其具体统计另存 gitignored 的本地评审，不写入本公开报告。

### 用户要求的四项

| 项目 | 结论 |
| --- | --- |
| 公开树没有密钥与真实聊天 | 当前已审树/本地可达历史未发现密钥文件或真实聊天正文；`data/`未跟踪。但**仍有个人账号标识、目标会话名及本机绝对路径**，隐私清理不通过，见F1。 |
| `source_kind=live-db`、`backup2_coverage=unverified` | **通过本轮标记核验**：CLI、现有本地manifest、全部JSONL记录及索引来源一致。不是重做解密或证明备份2完整。 |
| viewer只监听127.0.0.1 | 当前用户运行实例确实监听127.0.0.1；**代码不强制**，可被参数改为其他地址，见F2。 |
| unittest能过 | **通过：31项，0失败、0跳过，22.071秒**，含官方SQLCipher和自建KDF程序测试。测试通过没有覆盖本报告新发现的问题。 |

## 方法与边界

- HTTPS `git ls-remote`核验远端main与本地HEAD一致；本地初始工作树干净。SSH连接失败后改用HTTPS，未推送或更改远端。
- 审查61个Git跟踪文件、本地可达的1个提交、demo SQLite及两张公开截图。没有把gitignored `data/`当成公共仓库内容。
- 文本扫描仅报告路径/行号，不回显疑似秘密。未发现不是“数学上证明绝无任何敏感数据”；本次不覆盖未获取的独立PR refs、GitHub缓存或外部fork。
- 执行 `.venv/bin/python -m unittest discover -s tests -v`。
- 读取已有本机档案的manifest和索引聚合，并逐行扫描JSONL以核对来源/计数/状态，不打印任何正文。
- 用户8765实例的HTML/JS/CSS与checkout字节一致；视觉/交互与注入复现使用独立的**合成demo副本**，未向真实档案注入内容。
- 不重签/启动微信，不读密钥文件，不上传私密内容，不修改导出器或viewer。仅新增本审计文档及本地评审。

## Findings（按优先级）

### F1 — P1：公开树仍泄露个人标识，且妨碍其他用户使用

位置：`wechat_export/config.py:11–16,92–107`；`wechat_export/diagnose_copy.py:26`；`tests/test_export_pipeline.py`；`tests/test_preview.py`；demo `archive.sqlite`的meta。

配置与诊断代码硬编码操作者账号、账号目录、备份标识、本机用户路径；测试仍使用操作者真实目标会话名称。demo消息为四条合成记录、会话为Alice/Studio，公开截图也为此demo，但demo数据库的`meta.export_dir`保存了构建机器的绝对路径。

**不是发现了密码或真实聊天正文泄露**，而是公开脱敏没有完成。`data/`在.gitignore中并不能移除其他文件里的身份信息。

修复：使用用户目录推导+账户选择，必要配置缺失时明确询问/报错；测试全面改为虚构身份；demo构建不持久化本机绝对路径；增加跟踪树及历史敏感标识扫描。修复HEAD不抹掉旧提交，是否清理公开历史由仓库所有者另行决定，不自动force-push。

验收：当前树和用户指定的待发布历史均无个人标识；新用户未配置账号时不会默认使用原操作者账号。

### F2 — P1：所谓loopback-only只是默认值

位置：`wechat_export/cli.py:197–201,281–285`；`wechat_export/archive_server.py:147–155`。

CLI接收任意`--host`，server原样传给`ThreadingHTTPServer`，甚至在其他host上仍打印“loopback only”。使用mock socket构造器实证`0.0.0.0`被接受；本次没有实际向局域网绑定私密服务。

修复：按照仓库约定固定127.0.0.1，或只允许精确的127.0.0.1并拒绝其他值；同时在公开server函数与CLI边界验证。作为本地敏感数据服务，还应加Host验证、明确的会话访问控制策略及CSP，而非以绑定地址代替所有访问保护。

验收：0.0.0.0、::、非环回地址均在创建socket前失败；默认实例只监听127.0.0.1；覆盖负例测试。

### F3 — P1：会话名与搜索结果存在存储型脚本注入入口

位置：`viewer/app.js:51,144–149`；`wechat_export/archive_server.py:34–41`及静态响应。

会话名、会话ID、搜索摘要被直接拼接进`innerHTML`。聊天/昵称是非可信输入，正常消息气泡使用textContent并不能保护另外两个位置。服务没有CSP作为额外防护。

在合成demo页面把会话名设为带本地失败图片事件的测试字符串，仅令其设置DOM测试标记，**浏览器中确认事件脚本执行**。测试没有真实数据、没有外部请求。

修复：对动态文本一律使用DOM节点+textContent，ID通过dataset属性赋值；不要“只替换script标签”。设置适合本地工具的CSP（脚本/连接/图片等来源限制）作为纵深防御。

验收：合成恶意会话名、搜索摘要、引号/HTML等均显示为文本，不执行事件、不产生未经允许的网络请求。

### F4 — P1：带发送者前缀的XML被误归为可读文字

位置：`wechat_export/preview.py:8,22–34`；`wechat_export/archive_index.py`的preview/readable计算；`wechat_export/livedb_export.py:109`附近。

XML识别要求payload以`<`开头，但实际有“发送者前缀+换行+XML”的封装。以下**合成**输入返回readable=True：

```text
demo_sender:
<?xml version="1.0"?><msg><img /></msg>
```

本机只读聚合确认这不是边缘case。“只看可读文字”会继续展示大量非文字XML，同时影响搜索与喂AI的内容选择。README又把该开关描述为“collapse to chips”，实际`readable=1`的API会直接过滤非readable记录，并不是折叠保留。

修复：先按来源规则拆发送者封装，再做类型/子类型解析；将存档原文、可读正文、结构化媒体卡片分开；默认不把未知payload作为可读正文。安全XML解析不解析外部实体，原XML放独立的显式高级查看入口。

验收：带前缀/无前缀、压缩/未压缩的图片/语音/应用消息均有合成夹具；纯文本筛选不显示媒体XML；未知类型显示诚实占位；全量重建派生索引后计数一致。

### F5 — P2：非UTF-8原始payload实际被丢弃，却标为保留和ok

位置：`wechat_export/content.py:24–51`；`wechat_export/livedb_export.py:109–112,162,169`。

decode函数返回base64，但记录只存`{"raw_b64": true}`这个布尔标记，没有保存内容或可读取的外部对象引用，还写`raw_b64_kept`，非UTF-8记录可能仍为`parse_status=ok`。

用项目的合成二进制夹具复现：一条非UTF-8记录被标“已保留”、`ok`，实际没有base64内容。本轮真实档案聚合未发现这条特定失败分支，不把合成缺陷写成已证明当前真实档案发生丢失。

修复：保留可恢复的字节对象/内容寻址文件及哈希，或记录明确unsupported/partial；不能只存布尔值冒充原文保全。

### F6 — P2：完整性状态过宽，来源快照追溯未接通

位置：`wechat_export/export_run.py:113–138`；`wechat_export/livedb_export.py:93–96`；`wechat_export/cli.py:128,204–219`。

`export_status`主要由“records非空+目标是否消歧”决定，无法反映表因字段不兼容被跳过、payload处理失败等；合成二进制丢失case仍能输出complete。CLI传入`source_snapshot_id=None`，未将输入快照manifest与结果绑定。`verify`仍硬编码`live_db_text_decode_complete=False`，也不核验指定导出run。

来源标记live-db/unverified本身正确，但不能据`complete`推导所有类型、全部附件或原始历史完整。

修复：明确该状态是“选定来源记录导出”而不是“所有历史已恢复”；建立逐库/逐表处理与拒绝记录账本，将真实来源快照ID、哈希、解析版本贯穿输出；verify接受具体run并对账。

### F7 — P2：wheel不包含viewer资源

位置：`pyproject.toml`；`wechat_export/archive_server.py:14`。

隔离构建成功的wheel含Python代码，但**不含index.html、app.js、styles.css**。源码editable安装可用不能证明正常wheel安装后viewer可用。最初无隔离构建因本地缺setuptools失败，随后使用pip默认隔离构建成功，以上结论基于实际wheel列表。

修复：将静态资源作为包数据发布并使用资源API寻址。增加干净venv中从wheel安装、从非仓库目录启动demo并请求HTML/JS/CSS的smoke test。

### F8 — P2：viewer日期分组与显示时区不一致

位置：`viewer/app.js:18–25,88–95`。

日期分隔线直接截取UTC字符串，消息时间用浏览器本地时区。同一demo中分隔线显示次日，而气泡时间显示本地前一天；公开截图和浏览器都可复现。翻页时lastDay也重置，可能重复日期分隔。

修复：日期分组、消息时间与导出时间统一选择一个明确显示时区，保留UTC用于存储；增加跨午夜和DST测试。

## 额外产品/交互观察

- viewer没有导出按钮、时间范围、多选会话、格式选择或导出进度；server只有meta/conversations/messages/search读取API。它目前确实是浏览器，不是图形化导出器。
- CLI确实具备全量/指定conversation-id导出JSONL、CSV及月度Markdown，不应把项目贬成“只有一个页面”。但README没有把新用户的账号发现、授权读取、导出流程串成可操作的一条路。
- 媒体未提取；当前渲染仅文字/通用chip，没有图片、语音、视频、文件、引用等专用卡片。CSS无法凭空补出缺失资源。
- 搜索为全库检索，不随当前会话限域；点击命中后只是打开会话第一页，不定位命中记录。
- 请求无取消/请求版本检查，快速切换会话时可能混入旧响应；需要E2E覆盖。
- 小屏CSS直接隐藏会话栏而没有可替代导航。
- 大量留白、暖纸色、衬线标题、印章标志的视觉方向更像档案展示，不符合“像微信一样快速查阅与导出”的需求；属于产品方向问题，不只是美丑判断。

## 推荐修复顺序

1. F1/F2/F3：公开脱敏、强制loopback、消除注入；先于继续传播仓库。
2. F4/F5/F6：结构化消息、原始payload保全、真实完整性对账，重建可读索引。
3. 最小导出闭环：选择来源 → 全量/群/私聊 → 日期/类型 → 格式/媒体策略 → 数量预览 → 导出 → 文件位置与质量说明。
4. 微信式阅读布局：窄导航、紧凑会话列表、头像/群成员名、白/浅绿气泡、中性灰背景、一致时间线；默认媒体卡片而非XML。
5. F7/F8和跨环境发布、真实规模合成数据E2E；版本支持表区分“本机验证”与“其他版本待验证”。

## 参考

- MDN `Element.innerHTML`：动态HTML注入风险。`https://developer.mozilla.org/en-US/docs/Web/API/Element/innerHTML`
- Python `http.server`官方文档：服务的安全边界需应用自行控制。`https://docs.python.org/3/library/http.server.html`

这些文档用于解释风险；各具体缺陷均有本项目源码或本轮本地复现依据。
