# 接手模型实施合同

## 0. 当前状态（以本机复核为准）

规划起点提交：`14eb9891347d6d7ac477cc84b46b1a3983c9dbe6`。上一阶段本机348项单元测试及远端合成测试通过，这不是新功能的测试证据。不得重做取钥/导出底座来替代本次产品功能。

现有：Python本地HTTP服务、SQLite档案索引、静态JS/CSS、桌面入口、消息/卡片/网页链接、图片文件服务、四格式导出、含附件批量ZIP。没有已交付的画像引擎或学习资料库。已有服务仅监听127.0.0.1。安装版与checkout隔离，修改源码不会自动更新桌面运行版。

### 应复用的文件

| 文件 | 可复用能力 / 注意 |
|---|---|
| `archive_server.py` | 现有请求入口、范围绑定、分页；新路由应分模块，不继续堆巨型函数 |
| `archive_binding.py` / `archive_files.py` | 档案与源修订验证；派生笔记不能成为canonical的新组成部分 |
| `archive_index.py` | messages/conversations查询和索引更新；不要直接查询微信原库 |
| `livedb_export.py` / `models.py` | sender_id、is_self、record_uid；先实证缺失率与一致性 |
| `preview.py` | 可读、类型明确的analysis_record；它不是匿名化器 |
| `message_cards.py` / `link_urls.py` | 原网页目的地与卡片；safe_web_url不是网页取回的SSRF防护 |
| `recovered_media.py` / `bundle_media.py` | 附件映射、内容校验、独立复制/克隆、预览状态 |
| `ai_bundle.py` | 旧三范围导出及带附件ZIP；模块名为兼容保留，不再强化AI定位 |
| `jobs.py` / `workflow.py` | 取消、任务持久化；新增任务状态要覆盖崩溃恢复，不只加前端spinner |
| `export_service.py` / `output_locations.py` | 日期/范围、输出目录登记和原生选择；导出不能接任意路径 |
| `http_security.py` / `loopback.py` | Host、Origin、CSRF、CSP等；读写新接口必须沿用 |
| `static/index.html` / `app.js` / `styles.css` | 当前UI；保留聊天定位、媒体、导出交互再增加壳层 |
| `offline_html.py` | 离线HTML安全参考；新增学习包不能自动执行网页HTML |
| `privacy_audit.py` / `docs/public-asset-review.json` | 公开树隐私与合成资产审核 |
| `desktop.py` / `scripts/bootstrap.py` | wheel隔离安装与桌面单例；只重启精确归属的服务 |

不要盲目升级技术栈或迁移数据库。新前端建议原生ES modules；原有app脚本若迁移为模块，要先验证相关CSP/MIME与事件行为。没有必要引入云数据库、Redis、React重写或向量数据库，首版SQLite/FTS与结构化JSON足够作为存储方案。

## 1. 存储分层

原始来源始终只读。新增私有派生存储，例如：

```
data/insights/<archive_namespace>/insights.sqlite
  identity / people / conversation_roles / user_context
  profile_runs / observations / evidence / corrections
  learning_items / item_sources / content_versions / notes / review_cards
  jobs / chunk_checkpoints / optional_search_index

data/insights/<archive_namespace>/content/<content_id>.txt
 data/private/analysis-provider.json       配置与凭据单独0600，不输出
 data/deliveries/<new-run>/               新输出，不覆盖旧版
```

`archive_namespace`是本地注册的稳定不透明ID，不用账户名/显示名拼目录。HTTP的`archive_id`可能随绑定变化，不能直接当作跨重启持久主键。派生行同时保存`source_revision`；切换档案、源变更、身份/用途修订、排除证据、模型/提示版本变更都要使相关缓存失效。

SQLite迁移需要备份、schema_version、事务及回滚路径。每个派生数据库/内容文件0700目录、0600文件。不把真实笔记或报告加入Git，也不把它们写入公开demo。

### 最小模型（提案，具体字段名可保持一致语义后微调）

```text
PersonContext(person_id, sender_ids[], display_aliases[], relationship,
              source=user_provided, confirmed_at, revision)
Identity(account_namespace, self_sender_ids[], verification_state, revision)
ConversationRole(conversation_id, purpose, profile_excluded, revision)

ProfileRun(run_id, archive_namespace, source_revision, subject_person_id,
           kind=self|friend, scope_json, identity_revision, context_revision,
           engine_id, prompt_version, schema_version, coverage_json,
           status, processed_count, failure_count, created_at)
Observation(observation_id, run_id, dimension, statement,
            basis=user_context|explicit_fact|scoped_observation,
            context_scope, evidence_ids[], caveats[], review_state)
Evidence(evidence_id, source_revision, record_uid, conversation_id, sender_id,
         author_role, exact_quote_or_span, readable_body_hash,
         context_window_ids[], content_origin=self_authored|forwarded|quoted)
Correction(correction_id, observation_id, action, user_text,
           excluded_evidence_ids[], created_at)

LearningItem(item_id, kind, display_title, reading_state, content_state,
             topics[], user_note, canonical_url_key, created_at, revision)
ItemSource(item_id, source_revision, record_uid, fragment_id,
           saved_at, saved_comment, original_url)
ContentVersion(content_id, item_id, acquisition=chat_text|local_file|pasted|fetched,
               body_hash, available_extent, source_url, acquired_at,
               paragraph_ids[], parser_version, status)
Note(note_id, item_id, content_id, paragraph_id, quote_span, user_text, revision)
LearningSummary(summary_id, content_ids[], engine_version, claims[], citations[], status)
ReviewCard(card_id, item_id, content_id, question, answer, citation_ids[], user_state)
```

JSONL输出与UI采用统一DTO，不在某个页面自行从XML再提取人格信息。消息来源ID/角色、文章作者/保存者、用户补充这三类来源应在结构上区分，而不是靠prompt提醒。

## 2. 画像处理流程

1. **范围冻结**：绑定档案、时区、日期半开区间、本人/好友ID、会话用途、排除记录、用户补充版本。
2. **统计全范围**：本人/对方/未知身份数量，按月份/会话分布，未解码/非文本数量。统计不需要LLM。
3. **归属与类型**：只允许目标本人原创表达进入“关于目标的证据”；引用/转发/阅读库内容保留原归属；他人的话仅作上下文。
4. **分层材料**：按会话、时间和必要对话窗口组织；不同场景分别提炼，窗口和样本选择可复现。模型预算不够则缩小范围或透明抽样，不悄悄把前几千条叫全量。
5. **局部提炼**：输出结构化候选事实/观察及精确source IDs，不生成不可验证的长作文。明确否定、假设、玩笑或引用不能抽成事实。
6. **汇总**：合并相似观察，保留矛盾/变化的时间；不以重复条数覆盖来源多样性，不把家庭观察推广到全部人格。
7. **验证**：schema正确、UID存在、源修订一致、说话人正确、引文真实且位于允许范围，敏感推断/禁用评分拒绝；模型提出的值不能直接当可信数据库主键或文件路径。
8. **叙述**：只基于通过验证的条目生成章节；若支持不足就显示缺口，不编补齐。引用存在仅说明可追踪，不能单独证明推理正确；语义支持还需保守规则、复核与抽样人工验收。
9. **原子发布**：run完整后发布，失败/部分完成明确显示，旧版本仍可看；未经验证的中间内容不冒充正式报告。

不设置“聊天满1000条就能准确画像”之类科学性伪门槛。可设置工程最低样本阈值用于拒绝空任务，但必须写为资料量保护而非准确率承诺。

### 引擎约束

统一`AnalysisProvider`接口，区分`mock`、已配置本地引擎、具体远端引擎。mock只在合成测试/显式示例模式有效，返回记录必须带`synthetic=true`，真实运行不能fallback到mock。

- 本地引擎未配置：仍提供真实统计、学习库、学习原始资料包，明确“未生成解读”。不要为了成品感用模板套结论。
- 真正生成画像/语义摘要需要配置可运行引擎；这是一项交付门槛，不是可忽略的TODO。可以提供受控“导出证据包→导入结构化结果”备用方式，但不能把它叫自动生成。
- 云端引擎：本次任务批准明确的提供方/端点、范围、字段、是否附件、预算。用户“想要画像”不自动解除此前“不上传”的边界。密钥不传前端、不记录请求正文到普通日志、不上传raw-local-only或媒体凭据。
- 本地模型服务也需要明确配置的地址；不要把任意网页URL当本地模型地址。分开处理本地引擎允许列表和外部网页SSRF策略。
- 任务状态建议：draft / needs_engine / needs_consent / queued / extracting / analyzing / validating / completed / partial / cancelled / failed。记录真实计数，支持幂等、断点、并发限额、预算停止与取消后不再发起新请求。已发出去的远端请求不能保证撤回。
- 成本未知时显示字符/token估算而不是造美元金额。超预算先停止并保留进度，不擅自换服务商或上限。

## 3. 稍后读整理与正文获取

### 第一步：完全离线即可完成

从已指定收藏会话识别所有主消息类型和所有明确网页链接。每个来源记录UID、时间及片段身份；文章标题/URL存在不代表正文存在。初始条目可以是title_only、encrypted、missing等，不因不可读而丢弃。

保留原URL，只对已知追踪参数建立用于去重的另一个标准键。不能一刀切删query；某些文章的定位依赖参数。同一canonical URL可有不同正文版本；MD5/SHA内容相同可去重附件，但不得合并不同人的评论或不同收藏事件。

群内纯文本有时是用户笔记，有时是粘贴文章。无法判明就标为“保存文本，作者未确认”，不要自动把观点归用户或外部作者。相邻消息只是候选上下文，不无条件拼为一篇。

### 第二步：有边界的内容获取

首版顺序：已有消息正文 → 已验证本地文本类附件 → 用户粘贴正文 → 用户点击的单篇外部获取。不能默认抓取整个收藏群。

本地PDF/文档提取需要具体的解析器、资源限制和提取状态测试；不支持的格式提供打开/下载而不是假装解析。OCR/音视频转写先留清晰的未处理状态，不拖延整个学习库。

外部获取另建`content_fetcher.py`：
- 仅批准的http/https，拒绝userinfo、非法端口、危险scheme；不得沿用safe_web_url就声称安全。
- 拒绝localhost、私网、链路本地、云metadata与IPv4/IPv6特殊地址；DNS解析、实际连接及每跳重定向都验证，防DNS rebinding。代理环境默认禁用或单独审核。
- 不携带微信登录Cookie/本机浏览器身份，不执行页面脚本，不绕过登录/验证码/付费墙。失败给出粘贴正文或手工打开选项。
- 有超时、重定向上限、响应/解压后大小上限、类型限制；下载文件名由系统生成，不由URL指定路径。
- HTML抽取为安全正文/段落，不存带执行能力的页面供同源直接打开；保留作者/站点/取得时间/完整性证据。正文失败不只因title存在就返回success。
- 测试使用模拟DNS/连接或受控fixture，不能以放行真实内网地址来方便测试。

## 4. API与交互合同（建议路径）

继续要求现有档案绑定、Host/Origin/CSRF验证。状态改变用POST/PATCH/DELETE；GET不触发模型、网络抓取、写笔记或标记已读。目标person/item/run必须属于请求档案和范围。

```text
GET/POST  /api/insights/context                  身份、人物背景、会话用途
POST      /api/profiles/preview                  范围/证据覆盖/引擎与费用预览，无分析副作用
POST      /api/profiles/runs                     创建冻结范围的任务
GET       /api/profiles/runs/<id>                状态、覆盖、最终结果
POST      /api/profiles/runs/<id>/cancel         幂等取消
GET       /api/insights/evidence/<id>            受限上下文与原文跳转信息
POST      /api/profiles/observations/<id>/corrections
POST      /api/learning/imports                 从指定收藏会话增量整理
GET       /api/learning/items                   搜索/分页/筛选
GET/PATCH /api/learning/items/<id>               阅读状态、主题等；乐观版本锁
POST      /api/learning/items/<id>/content       粘贴或批准的本地导入
POST      /api/learning/items/<id>/fetch         已批准的单篇抓取
POST      /api/learning/items/<id>/summaries     基于已存在正文的分析任务
GET/POST  /api/learning/items/<id>/notes
POST      /api/learning/items/<id>/review-cards
POST      /api/insights/exports                 画像/学习包，使用登记目的目录
```

错误返回稳定code及可理解中文映射，不泄露本机路径或秘密。对不存在/跨档案对象统一安全响应。单个任务GET不把所有中间正文/请求载荷一股脑返回前端。

现有`openConvo(c, reset, around)`可作为原文定位底座。增加跨板块的导航适配，而不是在画像中复制第二个聊天渲染器。返回画像时保留章节位置、展开状态与证据抽屉。

## 5. 交付顺序与每阶段退出条件

不要把下面所有阶段合并成一次巨型提交。完成一个纵向功能就提交代码+测试+简短证据。

| 阶段 | 明确交付 | 退出条件 |
|---|---|---|
| P0 审计与身份 | 当前baseline、sender一致性统计、私有关系/用途配置、派生库迁移 | 不触碰原库；重名/未知身份不会串人；原源hash不变 |
| P1 新壳与状态页 | 三新增入口及响应式布局；空/加载/不足/失败页面；保留原聊天 | 原聊天搜索、定位、图片下载、导出回归；原型各页有实质实现而非死按钮 |
| P2 收藏群学习闭环 | 增量条目、重复来源、阅读器、笔记、阅读状态、离线学习导出 | 无LLM也能真实交付一份可学习资料；重复导入不复制条目或丢评论 |
| P3 引擎与安全任务 | provider接口、批准、预算、检查点、取消、输出校验 | mock与真实数据隔离；未批准0远端请求；失败不产出假报告 |
| P4 本人画像闭环 | 冻结范围→分层证据→验证→报告→引文→纠正→导出 | 实际可用引擎处理被批准的数据；收藏作者不变本人；家庭覆盖偏差可见 |
| P5 好友画像闭环 | 稳定ID选择、目标发言归属、用户关系背景、共同事项 | 换人/换档案不串内容；对方发言与本人推测分开；无评分和敏感猜测 |
| P6 学习深化 | 已有正文总结、主题综合、可编辑顺序、笔记关联、复习卡 | title_only不能正文总结；作者/本人/建议分开；摘要段落可定位 |
| P7 本机交付 | wheel、桌面入口、真实范围验收、文档、公开扫描、正常推送 | 安装版而非只checkout有效；私有数据不进Git；用户无需终端操作 |

推荐先做P2，因为它能把已有收藏变成可用学习资料，也先验证来源与内容状态。画像不用等所有加密图片恢复；反过来也不能用缺失附件中的信息凑画像。

### 建议新增文件（不是要求预先创建空模块）

```
wechat_export/insights/{store,identity,evidence,scope,providers,profile_pipeline,profile_validate}.py
wechat_export/learning/{importer,content,fetcher,notes,summaries,exporter}.py
wechat_export/insights_routes.py
wechat_export/static/{shell,profiles,learning,evidence}.js
```

避免让新模块读`/Applications/WeChat.app`或账号db_storage。它们只读注册档案，通过现有边界读取附件。实际字段/模块在小步实现中确认，不能为了凑目录写几十个未使用文件。

## 6. 必须验证的用例

### 画像与引用

- 我与好友同名、改备注名、未知sender、错误is_self、少量记录、只有图片/语音。
- 收藏文章宣称X、用户保存却反对X、用户引用别人、朋友替第三人说话、反问/否定/玩笑。
- 一个家庭会话远大于其他来源；旧爱好与新自述冲突；同一段话大量重复转发。
- 伪造record_uid、引用错人、引文不存在、引用在范围外、源修订变化、跨好友/跨档案引用。
- 敏感猜测/关系评分/诊断输出被拒绝，不只是前端藏起来。
- 用户纠正/排除后缓存失效，新报告不再用被排除材料；旧报告标明修订。

### 学习资料

- 同一文章多次收藏带不同备注；同一消息多链接；query参数确实区分文章；未知作者的纯文本。
- 只有标题、失效链接、登录页伪装HTTP200、部分正文、加密图片、PDF缺失、不支持格式。
- 标记已读不是获取正文的副作用；刷新/关闭后笔记仍在；内容更新不覆写笔记，旧摘录可追到旧版本。
- 无模型时可导出真实基础学习包；无正文时禁止正文摘要。
- 导出包内相对路径可解析、SHA256一致、含实际附件、不依赖服务端口、离线0自动HTTP请求。

### 安全与运行

- 云端拒绝/未批准时0网络；允许范围改变后旧批准不可复用；默认不传图片和raw字段。
- 本地模型、外部抓取、内容渲染三种信任边界分开；提示注入、JS/HTML、路径穿越、SSRF、重定向、DNS rebinding、超大压缩响应。
- 任务取消/超时/重启/重复点击/预算耗尽/旧结果晚返回；不能切档案后显示上一档案结论。
- 派生库/笔记删除不动原档案；原数据库与备份哈希不变；日志/公开报告无聊天正文与凭据。
- 1440/1280/1024/390/320尺寸，键盘导航、焦点返回、无横向溢出、长中文标题不挤按钮。

### 验证层级

1. `python -m unittest discover -s tests -v`：新增数据/归属/安全测试，旧用例不删。
2. 合成浏览器：完整生成/选择/证据/纠正/学习/笔记/导出路径，不只截图。
3. 离线包：关闭测试server再打开HTML，校验可读与零后台网络。
4. 隔离安装版：从wheel启动，不误导入checkout。
5. 本机真实数据：只用用户授权的范围与计算方式，留私有计数/哈希/截图证据，公开只有合成样例。若引擎不可用或缺具体云授权，诚实说明未完成，不能用mock冒充实测。

## 7. 下一模型的完成报告必须有

- 本次完成了哪个阶段，实际可点击入口在哪里；哪些仍不能做。
- 使用了哪个档案修订、何种引擎；多少记录处理/排除/未知；是否有远端请求。
- 一条画像观察回原文、一条好友身份正确、一篇学习条目到笔记/导出的验收证据（真实证据只留本机）。
- tests、浏览器、离线包、安装版、CI分别是什么结果，不混为一谈。
- 本机文档更新、公开树扫描、提交/推送状态；不重写历史、不上传私有分析。

**禁止的交付话术：**“功能已完成”但只有三个静态页；“全量画像”但只读了头1000条；“文章总结”但只拿到标题；“已安全脱敏”但仍含姓名/正文；“已更新桌面产品”但只改了checkout。
