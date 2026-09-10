# 微信本地档案工具 — Developer Preview

这是本地预览包，不是已完成新用户实机验收的正式版，也不是腾讯官方软件。
工具可阅读已有档案，并按全量、群、私聊、多选、日期和类型导出。
首次读取受精确 build/指纹和逐阶段授权限制；没有已普遍验证的兼容性承诺。

## 安装

需要你已安装 Python 3.11+。在解压后的目录运行：

```sh
zsh scripts/install-macos.command
```

阅读来源和联网说明，输入 `yes` 才继续。安装器会使用包内 wheel，pip 可能下载依赖。
它不会安装系统工具、关闭保护或操作微信；新环境自检通过后才切换，失败保留旧版。
之后运行：

```sh
zsh scripts/launch-macos.command
```

打开终端打印的 `127.0.0.1` 地址。可以先选“试用虚构示例”，体验12条虚构消息。
不要把真实聊天、密钥或原始快照放到公共仓库或发到网上。

[安装、离线模式与回退说明](docs/install-guide.md)

## 重要边界

- 保留 `source_kind=live-db`、`backup2_coverage=unverified`。
- 不解码 RMFH「聊天 2」备份，不承诺手机全部历史完整。
- 分析版仍含姓名与正文，不是匿名化；附件二进制目前不随消息导出。
- 不为运行预览关闭 Gatekeeper/SIP/AMFI，不自动重签原厂微信、退出登录或恢复数据。
- 原生选择/TCC、实体外接盘、独立Mac/新用户首次读取和公开发布验收仍有缺口。
- `bundle-manifest.json` 和 `SHA256SUMS` 记录包内文件哈希；这不是代码签名、公证或信任根。

[源代码与完整验收文档](https://github.com/toolazytoname/wechat-local-archive)

## 新版首页与本机入口

首页现在突出打开聊天与导出给AI。已配置本机入口的用户双击“微信聊天档案.app”即可；公开包不会包含某人的档案路径或桌面配置。维护者可以用 `scripts/create-macos-shortcut.py` 配合本机私有配置生成入口。

参见 [普通用户指南](docs/consumer-guide.md) 和 [工程笔记](docs/engineering-notes.md)。
