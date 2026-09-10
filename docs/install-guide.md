# 安装、更新和回退（developer preview）

Python 3.11+ 是前置条件。读取 Mac 微信还需要环境页列出的 SQLCipher、Xcode/LLDB
等依赖与精确版本证据；安装本 Python 工具并不代表取钥依赖或兼容性已验收。

## 安装和再次启动

```sh
zsh scripts/install-macos.command
zsh scripts/launch-macos.command
```

安装会先显示来源、目标目录和联网行为。交互输入 `yes` 才继续；无终端输入时
默认取消。明确的批处理确认可用 `--yes`，不要把这个参数交给未审查的下载脚本。

- 从源码目录运行：pip 安装该目录，可能下载构建工具/依赖。
- 从预览包运行：自动使用其中唯一的 `dist/wechat_export-*.whl`，仍可能下载依赖。
- `--no-launch` 只安装和自检，不启动服务。
- `PYTHON=/path/to/python3.11` 可选择你已安装的解释器；不会自动安装 Python。
- 不调用 sudo，不安装 Homebrew/SQLCipher/Xcode，不改 Gatekeeper/SIP/AMFI，不操作微信。

## 更新失败不会覆盖旧环境

安装器在 `~/Library/Application Support/wechat-local-archive/versions/<id>`
原地创建独立 venv。环境不会在创建后搬家，因此其中的命令脚本仍指向正确解释器。
完成依赖检查和 12 条合成消息导出自检后，才原子切换 `current` 指针。

失败版本保留为私有、未激活状态；原 `current` 或旧 `venv/` 不重建、不删除。
日志和 `install-receipt.json` 保留在该版本目录。安装日志可能含本机路径，不要直接公开。
保留多个版本会占空间，当前没有自动清理旧版本的功能。

要回到回执记录的上一版本：

```sh
zsh scripts/install-macos.command --rollback --no-launch
```

仍需输入 `yes`；回退只是切换入口，不删除新/旧环境或聊天档案。
首个安装没有上一版本，不能回退。原有固定 `venv/` 被保留时，也能回退到该入口。

## 离线安装

使用**预先准备、适配当前 Python/macOS 架构的可信本地 wheels**：

```sh
zsh scripts/install-macos.command \
  --source /path/to/wechat_export-0.2.0-py3-none-any.whl \
  --offline --wheelhouse /path/to/compatible-wheels --no-launch
```

离线模式仅接受本地 wheel，不执行源码构建。pip 使用显式本地文件、`--no-index`
和 `--no-deps`，安装后执行 `pip check`。wheelhouse 应包含所需依赖的兼容 wheel，
不要混入同包多个版本或其他平台文件；缺失/冲突/不兼容会停止，不会转为在线安装。
本机验证用的依赖是 pycryptodome 3.23.0 与 zstandard 0.25.0（Python 3.14/arm64）；
这不是其他解释器/机器的兼容性证明。

## 指定测试安装位置

默认位置无需设置。可信本机 CLI 可用 `--app-support /path/to/install-root` 做隔离测试。
再次启动时设置同一位置：

```sh
WLA_INSTALL_ROOT=/path/to/install-root zsh scripts/launch-macos.command
```

安装位置不是浏览器可提交的任意路径接口，也不是账户档案导入功能。
不要选择同步/共享目录。已有源码 checkout 的 `data/` 不会自动迁移到安装版。

## 常见失败

- **Python 太旧或不存在**：自行安装/选择 Python 3.11+，没有自动修改系统。
- **下载失败、依赖冲突**：查看失败版本的私有 pip 日志；旧版本仍保留。
- **已有安装任务**：非阻塞安装锁拒绝并发更新，不抢占另一个任务。
- **current 被其他文件占用、指针或权限异常**：停止，不删除占用文件，也不 chmod 修复。
- **自检失败**：不激活新版本；合成自检不代表真实微信读取成功。
- **未知微信 build / 权限未通过**：这是读取阶段限制，不应通过重装、降级系统保护或改签主微信绕过。
