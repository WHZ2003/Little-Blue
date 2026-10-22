# 记忆迁移副本

`memory.ps1` 将已保存的数据导出成 ZIP，也可校验和导入。它是一个时间点的副本；
两台设备导入后各自成长，不会自动同步或合并。

## 在当前设备导出

在 `D:\DaFeiYu` 的 PowerShell 中运行：

```powershell
.\memory.ps1
```

默认从正在运行的 Docker 核心导出，生成 `backups\memory-日期-时间.zip`。
使用 SQLite backup API 把包括 WAL 中已提交数据在内的内容制成一致快照，
不直接复制正在写入的数据库文件。不会停止核心或触发模型请求。
桌面尚未提交的草稿、尚未保存的生命状态不在副本中。
多个联系人/群数据库逐个快照，彼此之间不保证同一毫秒。

导出独立本地模式的数据：

```powershell
.\memory.ps1 -LocalOnly
# 或指定某一份数据目录
.\memory.ps1 -DataDir 'D:\somewhere\LittleBlue' -Archive '.\backups\my-copy.zip'
```

## 在另一台设备恢复

先安装相同或兼容版本的大肥鱼和本功能，把 ZIP 复制过去。以下目标目录必须尚不存在：

```powershell
.\memory.ps1 -Action Inspect -Archive '.\backups\my-copy.zip'
.\memory.ps1 -Action Import -Archive '.\backups\my-copy.zip' -DataDir '.\data\migrated-pet'
.\.venv\Scripts\python.exe -m little_blue --data-dir '.\data\migrated-pet' --panel
```

这启动的是导入的本地副本。原有 `start.ps1` 仍连接原 Docker 核心，不会自动切换。
可以用这个显式启动命令先检查记忆，再决定是否替换日常使用的实例。
导入失败时不会覆盖已有目录；若磁盘在最终写入时故障而留下部分目录，
不要启动它，换一个新的目标目录重新导入。

需要让恢复的数据运行在 Docker 中，可在目标设备导入一个新的数据卷：

```powershell
# 构建包含 memory_transfer 模块的镜像。
docker compose build core
# 名称自行选择，必须是新卷；下面命令不会把它切成日常核心。
docker volume create little-blue-migrated
docker run --rm --network none --mount 'type=volume,src=little-blue-migrated,dst=/data' --mount "type=bind,src=$((Resolve-Path '.\backups').Path),dst=/transfer,readonly" little-blue-core:local python -m little_blue.memory_transfer import --archive /transfer/my-copy.zip --data-dir /data/restored
```

要正式使用这个卷，核心的 `/data` 挂载须改为 `little-blue-migrated`，
`LITTLE_BLUE_DATA_DIR` 须设为 `/data/restored`。先停止旧核心后切换，保留旧卷作为回退。
本工具不会自动替换当前运行的核心或删除旧卷。

## 副本包含什么

当前格式为 v2，仍可读取旧 v1 副本。已启用 Hermes 时还包含其 `state.db` 一致性快照、
`SOUL.md`、持久记忆和受限类型的技能文件；不包含 Hermes 配置、令牌、日志。
运行该部分知识需要兼容的 Hermes harness，详见 [Hermes harness](hermes-harness.md)。

- 主数据库和已有的独立联系人/群数据库，保持隔离。
- 长期记忆、聊天记录、事实版本与撤销记录、成长设定与依据。
- 当前事项、已保存的生命状态、虚拟手机/小游戏进度、向量索引和使用预算。
- 格式版本、UTC 导出时间、每个数据库的 SHA-256、字节数和主要表记录数量。

不包含 `.env`、API key、服务认证令牌、微信登录凭据、浏览器资料、模型文件和日志。
聊天正文里如果曾粘贴过秘密，仍属于聊天记录，不会自动删改。ZIP 未加密，含私人记忆，
应作为私人备份保存。校验值可发现损坏，不是来源签名；只导入自己信任的副本。

导入后默认关闭联网、工具和环境感知，清除旧设备的通道连接信息。
未完成任务标记中断、未发出的桥接消息不重发，旧提醒保留记录但不再自动触发。
这些变化只作用于导入副本，不修改导出的原件或原设备。
新设备按需配置 API key、重新授权联网、绑定微信并重设需要继续的提醒。
模型权重需通过 `setup.ps1` 或 `tools/prepare_memory_model.py` 准备；缺失时仍可关键词检索。

第一版归档格式为 `little-blue-memory` v1，上限为 101 个数据库、合计 1 GiB。
只做独立副本恢复，不合并两台设备的分叉历史。导入至少应使用与导出相同版本的应用；
归档版本校验不代表任意新旧应用数据库结构都兼容。

## 本机验证（2026-10-21）

迁移专项测试 10 项通过，完整测试 297 项通过。已从真实 Docker 核心导出，
恢复到 `data/migration-check-2026-10-21`，比较了记忆、对话、成长和事实记录，
结果一致；SQLite 完整性检查及导入副本的离线启动检查通过。
本地应用退出时仍出现已有的 asyncio 管道清理警告，退出码为 0，恢复数据完整；
本次未修改运行时的异步工作进程。

`backups/DaFeiYu-portable-2026-10-21.zip` 是交付用外层迁移包，包含内层
`memory.zip`、只依赖 Python 标准库的 `memory_transfer.py` 和 `README.txt`。
先解压外层，再按 README 导入内层；不要将外层包直接传给 Import。
