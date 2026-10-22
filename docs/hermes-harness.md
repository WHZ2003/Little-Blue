# 大肥鱼的 Hermes harness

## 已部署的行为

云端任务由真实的 `hermes-agent` `AIAgent.run_conversation()` 执行模型和工具循环。
大肥鱼继续负责桌宠、微信收发、人格上下文、原有记忆库、聊天记录、权限和 API 预算。
上游源码未修改，固定版本为
`55ee4acdd822b73fb9573a2321531ae3a9d0b3e0`，来自
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/tree/55ee4acdd822b73fb9573a2321531ae3a9d0b3e0)。

核心镜像：`little-blue-core:hermes-55ee4ac-final`。
Hermes 镜像：`dafeiyu-hermes:55ee4ac-final`，Python 3.14，使用上游 `pm.build_env` 安装锁定依赖。

当前启用 Hermes 的 memory、skills、session_search，以及可检索迁移前记忆和已确认聊天的
`dafeiyu_memory_search`。桌面任务可以在原有权限允许时使用映射后的大肥鱼工具。
微信聊天不获得电脑操作权限。没有启用 Hermes 自带微信网关、终端执行或浏览器。
技能文件可以保存和检索；当前没有通用脚本执行工具。

后续已接入[自主逛 B 站](bilibili.md)。自主阅读轮次使用单独的四个只读工具，
不提供私人聊天记忆工具；正常聊天轮次仍使用上述记忆能力。

每轮独立启动 Hermes 进程，加载持久记忆、技能和会话数据库，并传入大肥鱼的当前上下文。
不会在后台持续运行自主学习任务；自动标题生成、后台复盘及压缩均关闭。
每轮最多 5 次模型调用，输出上限沿用大肥鱼的 768 tokens，仍受每日请求和 token 预算约束。
失败会结束本次任务，不自动退回旧循环重放操作。

## 连接与数据

`core` 和 `hermes` 挂载同一个当前交接数据卷。Hermes 的 `HERMES_HOME=/data/hermes`：

- `SOUL.md`：每轮同步当前应用身份；首次替换已有身份时保留 `SOUL.before-app-refresh.md`。
- `memories/MEMORY.md`、`memories/USER.md`：Hermes 持久记忆。
- `skills/`：技能文件。
- `state.db`：Hermes 会话记录；原 `memory.sqlite3` 继续保存对用户可见的聊天及收发状态。

旧记忆不需要批量灌入 Hermes 提示词，通过上下文与检索工具继续使用。
上述 Hermes 文件在首次任务需要时创建；仅启动空闲服务不会生成会话。

DeepSeek 密钥只留在核心的环境中。Hermes 没有公网出口，通过内部网络访问核心的
`/harness/v1/chat/completions` 和 `/harness/tool`。核心签发每轮临时凭据，完成或取消即撤销。
服务间控制令牌由 `.secrets/hermes_token` 提供，与桌宠 API 令牌分开。
Hermes 不开放主机端口。桌宠继续用本机 SSH 隧道 `127.0.0.1:18765`，无需重绑微信。

Hermes 容器限制 512 MiB 内存，核心限制 1 GiB。云端合成测试的一次真实 DeepSeek 请求
约 13 秒完成，Hermes 请求后约 90 MiB；正式服务空闲时两容器合计约 268 MiB。
这些是单次观测，复杂任务的峰值和延迟需要另行测量。

## 备份与两端交接

`backup-cloud.ps1` 继续下载一致性备份。格式升级为 v2，同时兼容读取旧 v1 包。
新增 Hermes 会话 SQLite 快照、人格、记忆和受限类型的技能文件，带路径、体积和 SHA256 校验。
普通副本不包含 API key、Hermes 配置、日志或微信登录凭据。
多个数据库分别做一致性快照，不承诺在线导出时跨数据库的同一事务时间点。

完整交接仍须先排空消息、停止旧核心和 Hermes，再导出；接收端先启动 Hermes 后启动核心。
接收电脑需安装相同的 Hermes 配置和镜像。不得同时启动两个持有同一微信账号的核心。
当前本机仍为 retired，日常请使用 `start-cloud.cmd` 连接云端。

## 重建与验证

在 `.research/hermes-agent` 准备 manifest 中的精确上游版本，然后在项目目录执行：

```powershell
.\.venv\Scripts\python.exe tools/package_hermes.py
docker build -t dafeiyu-hermes:55ee4ac .research/hermes-build
docker build -t little-blue-core:hermes-55ee4ac .
.\.venv\Scripts\python.exe tools/check_hermes.py
.\.venv\Scripts\python.exe tools/check_hermes_app.py
docker tag dafeiyu-hermes:55ee4ac dafeiyu-hermes:55ee4ac-final
docker tag little-blue-core:hermes-55ee4ac little-blue-core:hermes-55ee4ac-final
.\.venv\Scripts\python.exe tools/package_hermes_release.py
```

发布包是 `backups/hermes-release.tar.gz`，不含私密数据。两个镜像需另行 `docker save` 上传。
不要覆盖云端 `.env`、`.secrets`、`.handoff` 和交接管理的 `compose.override.yaml`。
应先用包内 `canary.yaml` 的独立空卷验证；它不含微信绑定。

已验证：完整应用测试 311 项通过；最终记忆/交接/桥接测试 24 项通过；真实 Hermes
工具循环、持久记忆、核心桥接、取消后恢复、重启保留聊天、联合备份及云端 DeepSeek 请求均通过。
云端测试没有向真实微信联系人发送消息。

## 本次切换和回退

切换前通过 `/handoff` 暂停新收发并等待排空，旧核心退出后才启动新核心。
核对保留 43 条记忆、25 条聊天记录，微信凭据文件未改变，云端交接所有权未改变。
正式状态接口已返回 `harness=hermes`、`wechat=connected`、`online=true`。
重启后微信会话上下文需要用户新发一条消息刷新。

云端回退资料在 `/opt/dafeiyu/backups/before-hermes-20261022-080715`，包含旧配置、源码、
交接元数据和停止写入后的完整数据卷归档。这个归档包含登录信息，应保持私有。
原核心镜像仍保留。

如需回退，先排空并停止新核心及 Hermes，再恢复该目录的旧 compose 和源码；
保持当前数据卷，不要用旧归档覆盖升级后产生的聊天。仅在确认是同一设备、同一卷、
同一代交接所有权的维护重启时恢复 active 状态，再启动旧核心。不得清除 retired/released 围栏。
整个数据卷归档用于灾难恢复，不作为日常回退的默认覆盖源。
