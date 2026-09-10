# 指定版本 Mac 微信：引导式本地读取与导出实施计划

日期：2026-09-09。基线：`2f3a5247da2da5e4653286b5a2a63a21a921c940`。

本计划只含公共代码事实、合成复现和产品要求，不包含真实聊天、账号、密钥或本机档案路径。用户要求由另一模型实施；本轮只重新评估并安排任务，没有启动实施代理或执行微信取钥操作。

## 1. 目标与交付标准

**目标：为明确支持的 Mac 微信版本提供从环境检查、选账号、授权读取，到全量/群/私聊筛选与导出的完整引导。**

不是“已有export-dir的viewer再换皮”，不是所有微信版本通用，也不是手机或RMFH备份解码器。

v0.2 beta的用户流程：

```text
安装/启动本地工具（没有档案也能打开）
 → 选择「读取这台Mac的微信」或「打开已有本地档案」
 → 检测版本/build/架构、依赖、账户候选、磁盘和权限
 → 用户选择账户/数据目录、理解读取方式、按阶段确认
 → 安全快照 → 获得经验证的本地读取材料 → 解密/校验
 → 规范化 → 索引 → 样本核对
 → 全量/指定群/指定私聊/多选 + 时间与类型过滤
 → 预览数量与范围 → 本地导出 → 打开结果/质量报告
```

所有live-db结果保留`source_kind=live-db`和`backup2_coverage=unverified`。

### 第一版做什么

- Apple Silicon Mac优先；只对有证据的版本/build/架构组合启用对应adapter，未知组合明确为未验证。
- 两条入口：引导式Mac读取、已有档案导入。已有密钥文件可以是高级入口，**不能变成普通用户的必备条件**。
- 全量、单会话、多选会话；群/私聊筛选、时间范围、文字/系统/媒体占位策略。
- 分析输出：JSONL、CSV、Markdown；阅读输出：可离线打开的安全HTML（推荐纳入beta）。
- 可理解的进度、失败诊断、取消、重试、来源与计数核对。
- 微信式布局沿用现有代码，优先把消息解释和导出流程做好，不重建前端框架。

### 暂不承诺

Windows、Intel实机兼容、任意版本自动取钥、丢失记录恢复、RMFH备份2、云端AI、所有媒体完整恢复、正式签名公证的原生DMG。若最后只有CLI+手工密钥导入，不算本目标完成。

## 2. 本轮重评估的现状

远端HTTPS main与本地HEAD均为上述基线。`git status`中tracking提示可能因本地origin/main未fetch滞后，不等于远端缺提交。本轮未fetch、push、重写历史。

### 已有成果，必须复用

- 已有本机live-db成功记录；不要重新把项目当成未取到密钥。敏感成功证据只在本机gitignored笔记/档案，不抄进public docs。
- `sqlcipher4.py` / `sqlcipher_cli.py`：逐页HMAC、截断拒绝、官方CLI的WAL合并。
- `key_capture.py`：已区分resolved/hit/captured/verified，并有自建KDF/idle程序测试。
- `livedb_export.py` / `export_run.py`：联系人映射、分库读取、来源定位、JSONL/CSV/月度Markdown。
- `preview.py`：已有发送者前缀拆分、XML/媒体分类；`models.py`已有原始payload相关字段。上轮问题不能原样认定仍未修。
- `loopback.py`：强制127.0.0.1、Host guard；server已有CSP；新版动态DOM不用原来的innerHTML拼接。
- `static/`：微信式气泡与列表、搜索定位、时区日期、导出抽屉、格式/日期/数量预览。
- `pyproject.toml`：静态资源已列package-data；仍需真实wheel安装smoke，而不只测源码目录存在。
- 重跑 `.venv/bin/python -m unittest discover -s tests -v`：**40项通过、0跳过、22.824秒**。

### 当前仍不是引导式工具的原因

- `serve --export-dir`仍要求已有索引；没有新用户空状态/setup入口。
- 没有compatibility registry、账户发现UI、支持阶段/授权状态机。
- 取钥、快照、解密、索引是独立函数/CLI，没有完整且安全可恢复的工作流。
- 配置仍要求手填账户，数据根默认绑定源码/安装包位置；已有相对配置存在路径基准歧义。
- 快照函数发现热数据仅打标而非拒绝；不能自动把单次lsof未见句柄视为一致性证明。
- 现有导出HTTP是同步查全表到内存、按秒命名文件、没有job/取消/持久状态。
- 已有索引只保存部分字段，直接导出索引不等于原始完整结构化档案。
- 新增POST操作只有Host guard，没有独立会话token/Origin与内容类型策略；将来加入读账户/启动副本必须先封闭这个边界。
- 没有新用户干净环境和第二账号端到端验收。

### 三个合成实证，应首先修

1. **空选择扩大范围**：`static/app.js:selectedIds()`对无当前会话/无featured返回`[]`，server `_write_slice`与`_count_slice`把空ids当所有会话。实际合成空ids输出4条全部demo记录。
2. **日期起点漏记录**：存储`+00:00`和浏览器`.000Z`直接按字符串比较，等价瞬间不相等。demo中起点等于最早时间，应4条，实际3条。
3. **同秒覆盖**：将合成两次导出时间固定到同一秒，第一次4条、第二次0条，两个结果path相同，第一次文件最后变成0字节。

这些复现只用临时demo，不碰真实档案。先写回归测试再修。

## 3. 架构边界（增量改，不推倒重来）

```text
runtime：默认数据根、端口、私有会话、权限、启动器
onboarding：向导视图/环境报告/用户确认
compatibility + adapters：按版本/build/架构决定可用读取能力
jobs/workflow：执行、暂停等待用户、取消、恢复、错误分类
snapshot/key/decrypt：复用现有模块，统一安全前后置条件
archive/normalization：规范消息、附件引用、索引与来源链
export_service：同一套范围定义驱动CLI/API/预览/输出
viewer：阅读、选择、导出，不直接执行shell
```

建议新模块名是实现指导而非必须照搬：`runtime.py`、`compatibility.py`、`discovery.py`、`adapters/macos_xwechat.py`、`workflow.py`、`jobs.py`、`export_service.py`。前端可拆为`static/setup.js`、`static/jobs.js`等，保持无必要框架迁移。

## 4. 排期与依赖

| 里程碑 | 任务 | 依赖 | 完成意味着什么 |
| --- | --- | --- | --- |
| M0 | T01安全与范围契约 | 无 | 无静默扩大全量、日期漏边界、覆盖文件；写操作鉴权已建立 |
| M1 | T02首次启动、检测与账户选择 | M0 | 没有配置/索引的新用户能走到明确支持判断及选账号 |
| M2 | T03快照/取钥/解密引导状态机 | M1 | 指定支持组合下，从无已有密钥到小样本可读，人工步骤有明确界面 |
| M3 | T04统一规范化、异步导出与完整性 | M0，集成验收依赖M2 | 全量/群/私聊导出可靠，可取消、重跑，来源与数量可核对 |
| M4 | T05消息卡片与离线阅读 | M3 | 不展示媒体XML，有清楚占位和离线阅读输出 |
| M5 | T06安装、E2E、兼容性与发布清理 | M1–M4 | 新用户在非开发目录实际完成流程；若缺实机证据则仅开发预览 |

**给下一模型的首轮指令：先完成M0 + M1，提交代码、合成测试与demo录屏/截图，再继续M2。不要先做原生壳，不要先跑真实取钥。**

粗略工作量（规划而非承诺）：M0 1–3个工程日；M1 2–4日；M2 4–8日加真实验证等待；M3 3–5日；M4 2–4日；M5 3–5日。熟悉项目者全程约3–6周级别；另一Mac/账号的验证可用性会影响周期。AI生成代码快不替代端到端实证。

## 5. 详细任务卡

### T01 / M0 — 先修范围、时间、文件安全和本地写接口

主要修改：`archive_server.py`、`static/app.js`、新增`export_service.py`与测试。

- 使用显式范围结构：`scope.kind=all|conversations`，选会话时ids不能为空、必须存在、不能自动退回all；群/私聊/featured最终均转为已确认ID。UI禁用无效按钮，server同样拒绝。
- QuerySpec统一预览与导出：scope、UTC起止、显示时区、消息类型、raw/analysis模式、格式。时间规范为整数epoch毫秒或统一格式后比较，明确采用`[since,until)`或其他约定，并让UI文案一致；校验起止顺序、非法时区、DST歧义。
- UUID/随机job目录，exclusive创建，临时文件完成校验后原子发布；并发同秒不得覆盖，默认不覆写已有导出。
- `POST`严格验证长度、JSON对象schema、类型、Content-Type，缺失/负值/非法Content-Length不能导致无限读取或服务崩溃；统一错误码，不回显秘密。
- 保留127.0.0.1/Host/CSP；建立随机本地session与Origin/CSRF校验。不要接受任意路径/任意命令的browser API；客户端使用已注册的source_id/output_id，不上传私密原件到远端。CLI作为可信本地入口与浏览器边界区分。
- 日志不记录查询正文、任意完整querystring、昵称或密钥。所有动态文本安全渲染；CSV公式单元格提供安全显示模式，不破坏canonical JSONL。

验收：三项上述复现变绿；未知/空ids失败；外部Origin/Host、不带会话凭据请求被拒；畸形POST、路径穿越、同秒并发、时间跨日/DST均有HTTP或单元测试。`all`只由用户显式选择。

### T02 / M1 — 无档案也能启动，检测与选择账户

主要修改：CLI/runtime/config/discovery/compatibility、server启动上下文、setup前端。

- 增加`launch`（或等价）入口，不要求`--export-dir`；启动本地界面，端口冲突时说明或安全选择空闲loopback端口，不结束其他服务。
- beta默认运行数据放用户可写的Application Support子目录，不放site-packages；保留现有checkout的`data/`兼容，**不自动搬迁/删除现有资料**。相对配置统一以config文件所在目录解释；first-run有输出目录选择与容量提示。
- 只读检测Mac版本、架构、WeChat.app路径、版本号、build、必要模块指纹、正在运行状态、依赖工具与可见数据根；不读取正文、密钥、整个钥匙串或无关账号。
- 发现候选账号时不自动按文件名截断推定身份；用户确认后建立本地account_id映射。无/单/多账号及用户选错目录均有状态；不再让live-db路线依赖backup_set或必须先有Backup目录。
- compatibility结果区分：environment_detected / adapter_candidate / key_acquisition_verified / codec_verified / export_verified。已有档案导入不因本机微信版本不支持而被禁止。
- 不支持组合只允许无破坏诊断和档案导入，明确缺少证据；不能默认执行调试/重签来“试试看”。

验收：空配置/无档案/无微信/多账号/未知build/工具缺失/空间不足均可解释且不崩溃；用户不用编辑JSON就能完成路径与账户选择；HTTP/API不输出完整联系人。

### T03 / M2 — 把成功读取方法做成可观察、可取消的引导

主要修改：workflow/jobs、livedb_snapshot/key_capture/decrypt、Mac adapter与前端任务页。

状态契约建议：

```text
created → preflight → awaiting_account → awaiting_consent
 → awaiting_wechat_exit → snapshotting → snapshot_verified
 → preparing_reader → awaiting_user_action → acquiring_key
 → key_verified → decrypting → normalizing → indexing
 → awaiting_sample_check → ready
```

终态/异常：cancelled、failed、blocked；等待用户不是假失败，也不能自动跳过。每个阶段显示当前动作、原因、可取消性、用户最小操作、证据摘要和脱敏错误码。

- 保全策略先于实验副本启动，说明应用副本不是数据隔离；明确保护原始应用/账号数据/媒体的范围和不能覆盖的状态。密钥本地0600、目录0700，不通过浏览器/API/日志回传。
- 快照：检查进程和文件占用失败要fail-closed，不把`lsof`失败当无占用；检测所有相关进程而非仅固定进程名；快照前后源目录/文件状态核对，检测新WAL/文件变化，热快照不得进入正式解密链；记录manifest哈希。
- 依赖已登录会话的顺序必须清楚：不能为了快照强退账号。正常退出进程由用户配合；重启/副本步骤根据批准的方法执行。
- 把先前“停在进入微信确认页”的经验产品化：awaiting_user_action显示应看到什么、如何手动进入、是否需要重新授权。不要靠固定坐标盲点或无限等待90秒，也不要把无库句柄自动归为缺权限。
- 取钥驱动复用现有独立测试；按实际断点状态/参数长度/HMAC筛选候选，拒绝猜测算法，超时后准确定位阶段。只清理本任务PID与临时对象，不按名称kill其他进程。
- 候选通过至少contact与两个message样本后才批量；各库逐页认证、WAL合并和结构完整性分别有结果。FTS等派生库失败若允许排除，需分类证明不是正文源，并显式报告，不掩盖失败。
- 索引/导出只读派生副本；真实原库不写PRAGMA、不checkpoint、不恢复覆盖。退出/失败后报告原应用校验、调试进程已结束、可否打开正常微信。
- 已有密钥可作为本地高级分支，但不能替代普通用户从零授权读取的验收。

验收：自建测试adapter跑通整条状态机与每个失败阶段；真实支持组合下无现成密钥的受控小样本流程有证据。**模拟成功不算真实取钥成功；旧账号已有密钥重用不算新用户验收。**

### T04 / M3 — 统一档案与导出，不再从缩略索引冒充完整导出

主要修改：archive_index/export_service/models/quality/export_run/cli/jobs。

- 定义canonical消息schema与schema_version；原始payload、可读正文、媒体结构、真实来源定位分开。检索索引是派生层，可重建；raw JSONL应来自完整canonical存储，不是SELECT *的缩略viewer表。
- 每个输出带manifest：job/run、source_kind、backup2_coverage、scope、时间/类型过滤、snapshot id+哈希、schema/parser版本、输入/输出/失败/跳过计数、媒体状态。CSV/MD也需sidecar manifest；未知旧来源明确unknown，不伪造当前snapshot_id。
- `complete`由来源处理账本决定，分开records_complete、attachments_complete、coverage_verified；导出“可读文字”需显示被排除数量，不能暗示原始全量。
- 任务后台运行：POST创建job，轮询/SSE二选一；导出流式写、批次进度、取消/错误回收、重跑隔离。无需先引入Redis/Celery。
- 预览与实际导出使用同一个QuerySpec和相同的不可变index版本，避免预览后索引重建造成计数漂移。
- 索引构建在新临时文件完成验证后切换，读写协调；不能在正在服务时直接unlink当前index/WAL/SHM。
- 支持当前、全量、多选、群/私聊筛选，选择真实ID而非备注名；显示确认范围与条数；成功后提供受控打开文件位置/下载，不允许浏览器任意打开本机路径。

验收：50万条以上合成数据不整批装入list，任务可取消；同秒多任务不覆盖；计数预览与文件行数一致；两账号本地ID冲突不合并；错误/不支持类型保留；CLI/API输出语义一致；旧档案可读或有显式迁移步骤且不覆写原件。

### T05 / M4 — 阅读体验与媒体策略

主要修改：preview/content/models、static和离线HTML writer。

- 继续现有微信式灰白/浅绿布局，保持档案工具身份，不假装腾讯官方客户端。不重新采用装饰型档案柜。
- 文本、系统、图片、语音、视频、链接/文件、引用、合并转发/未知类型有明确renderer与类型状态。文字聊天里正常的`name:\ntext`不应仅凭正则误删前缀，来源/类型共同决定封装拆分。
- 原XML默认不进入气泡/AI导出，保存在明确高级入口；CDN token、内部AES字段不可当可读卡片展示。
- v0.2基础承诺是准确正文与媒体卡片/缺失状态。实际附件提取另列能力：通过本地映射验证才提供图片预览/下载；语音播放、视频和OCR/转录不能凭文件扩展名承诺。
- 离线HTML不依赖localhost或外网脚本；已取得媒体用相对受控路径，XML安全转义、禁止自动远程加载；未取得附件明确说明。
- 修导出抽屉异步错误、重复点击、focus/键盘、空态、小屏导航、筛选时区说明；展示所选会话与实际输出范围。

验收：丰富合成消息demo包含前缀XML、链接标题CDATA、引用、缺媒体、长文字、emoji、同秒消息、跨日；E2E确认无XML泄漏、无脚本执行、无外部资源请求；搜索定位不跨错会话。

### T06 / M5 — 安装、兼容性证据与发布

- 构建wheel，在干净venv、非仓库cwd安装启动，校验静态资源、默认数据根、依赖提示、来源选择和demo端到端。不是只assert仓库static文件存在。
- 为beta提供bootstrap/启动脚本与图形化缺依赖指引。自动安装依赖/修改系统配置先得到用户确认，固定已测试范围；不把开发者的.venv或本机SQLCipher当已交付依赖。
- 至少一轮新的授权账户/独立环境测试首次读取，报告版本/build/架构；若资源不具备，交付developer preview和明确阻塞，不宣称新用户beta已验收。
- UI/API自动测试覆盖T01–T05；CI只使用合成资料。Mac特有真实测试为本地opt-in，普通unittest不得启动真实微信或读取真实密钥。
- current-tree扫描与history扫描分开。上轮修复HEAD的个人标识不等于旧提交删除；仓库owner自行批准历史清理/重写策略。不要自动force push或把私有回归证据放docs。
- README从“打开已有档案”升级为新用户流程，写支持矩阵、准确的依赖与手动确认步骤，说明副本风险和清理方式。旧review-report是历史基线，应附整改状态链接，不删掉来冒充从未有缺陷。

验收：一个未参与开发的人按README可启动到环境诊断；在明确支持的环境完成首次读取与三个范围导出；失败能回到原微信且不丢数据；未支持版本清楚拒绝取钥；安装包可重复构建。

## 6. 兼容性和日期特别说明

“支持4.1.13”太粗，必须记录build。评估当天本机版本元数据显示4.1.13/build269630/arm64/macOS26.6.2，早期笔记记录过build269579。**本轮没有重新取钥，不能据此推定两个build都已通过首次读取。**

下一模型应把成功运行证据与当时的应用版本/关键二进制哈希关联：证据缺失的阶段标unverified，不能补写为已验证。registry在实现前是空设计，不能先给269630填verified以让UI过关。

## 7. 授权与数据边界

- 用户当前要求是“规划交接”，不是重新取钥/重签授权。本文件不是操作授权。
- 下一模型可以在用户交付实施任务后写代码、运行合成测试、做无敏感内容的demo。每次真实微信操作按workflow阶段向用户说明并确认。
- 不自动重签主应用、关闭SIP/Gatekeeper/AMFI、退出登录/扫码、安装未知二进制、改容器权限或恢复备份。
- 实验副本签名/调试例外属于高敏阶段，用户明确批准具体计划后才运行；不复用别轮对另一副本的授权。
- 真实chat/key/联系人只留本机；public报告使用合成数据。`data/`和本机ops已有源不迁移、不删除、不纳入Git。

## 8. 第一轮交付要求

交接模型先交M0+M1的代码和证据：

- 三个范围/时间/覆盖回归通过。
- 空档案启动 → 环境检测 → 账号选择 → 支持/未支持解释的真实demo（使用合成发现结果或明确只读环境数据）。
- 本地写接口安全测试通过。
- 更新测试输出和remaining-work清单，说明M2尚未完成。
- 不只写新方案、不宣布整产品完成、不再主要花时间改颜色。

全目标达到后再给总验收；某一阶段必须用户配合时停止在明确等待状态，同时继续不依赖该操作的其他实现，不要伪造权限或把未支持说成成功。
