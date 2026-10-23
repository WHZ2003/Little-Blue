# Coopanion 本地桌宠与云端微信

2026-10-22 按用户选择部署官方 Windows x64 便携版 **v0.2.6**，直接接现有 DeepSeek Key，模型为 `deepseek-flash`。最初为独立试用实例；现已迁入原大肥鱼的记忆和聊天，并与既有云端微信服务同步。保留 Coopanion 自带性格；迁移后按用户明确要求修订口癖与短消息格式，云端聊天使用同一份快照。

## 打开与使用

在项目根目录双击 `start-coopanion.cmd`，或运行 `./start-coopanion.ps1`。当前已启动。

- 双击桌宠打开打字框，Enter 发送，Shift+Enter 换行。
- 右键桌宠或托盘图标进入设置；装扮、习惯、语音输入、对话、扩展都在应用内。
- 中文语音识别使用本地 SenseVoiceSmall / FunASR，模型已下载，后端已启动。按一下左 Alt，紧接着按住说话，松开发送；麦克风只在按键时打开。尚未用真实麦克风音频验收。
- 电脑操作已启用，权限为 `ask-each-turn`：每轮第一次截图或操作前询问。没有代用户批准截图、鼠标、键盘操作，尚未进行端到端操作验收。
- 从右键或托盘退出试用版。当前没有配置开机自启。

程序和独立数据都在 `.research/Coopanion/`，启动脚本固定使用其中 `data/`，不会误用其他实例。

| 内容 | 相对项目根目录的路径 |
|---|---|
| 主程序 | `.research/Coopanion/Coopanion.exe` |
| 应用配置 | `.research/Coopanion/data/home/companion/config.json` |
| 人格与记忆 | `.research/Coopanion/data/home/companion/workspace/` |
| 模型配置 | `.research/Coopanion/data/home/providers/deepseek/config.json` |
| 密钥 | `.research/Coopanion/data/home/providers/deepseek/.env` |
| 日志与运行记录 | `.research/Coopanion/data/` |

密钥从项目已有 `.env` 复制到本机 provider 的私有 `.env`，没有写入启动脚本或文档；`.research/` 整体被 Git 忽略。更换项目根目录的 Key 不会自动轮换此副本，可在应用设置里更新。分享源码时不要附带便携版的 `data/`。匿名使用统计已关闭。

## 阅读源码后的结论

阅读范围包括官方 README、开发文档，以及 seed、companion、persona、provider/connect/vendors、desktop-pet 的配置/World/聊天/权限/运行时、打包版 Cortico 的 Cormini 和 WebUI API 实现。

Coopanion 是 Electron 应用，底层使用 Cortico；Cortico 的 Cormini 管理对话、事件、上下文交接和文件工具。Coopanion 在其上加入鲸鱼桌宠、电脑操作、语音、记忆版本历史和设置界面。它本来就支持 DeepSeek，因此本次没有引入其他聊天模型，也没有改写 provider。

| 能力 | 实现与本次状态 |
|---|---|
| 桌宠、换装与动作 | 本地 Electron 窗口及 figure pack；已选择 whale / DeepSeek 配色，浏览器同源渲染预览正常，桌面窗口已连接 |
| 模型对话与工具选择 | Coo provider 直接访问 `https://api.deepseek.com`；真实回复已验证 |
| 长期记忆 | `CONSTITUTION.md` 为人格；`PARTNER.md` 为简短用户画像；其他文件按需读取。文件变更通过 Git 记录版本 |
| 上下文延续 | Cormini 管理会话阶段与交接笔记；部署时修改配置后须刷新当前会话前缀，单纯重启可能恢复旧上下文 |
| 语音输入 | 本地识别，再把文字交给 DeepSeek；无需另一个付费语音 API。识别后端启动已验证，真人语音未测试 |
| 电脑操作 | 本机截图、窗口、鼠标、键盘工具；关闭本机后不可用。工具已注册，应用内仍按每轮询问 |
| 主动事件 | 触摸、工具回执、闹钟等事件驱动；有 `alarm_set/list/cancel` 工具 |
| 扩展 | Cortico World / provider 扩展；不能把其他桌宠的 npm 插件直接当成兼容插件 |
| 网页与 B 站 | 当前原装工具表没有独立 `web_search` 或 B 站字幕工具；可按应用权限操作本机浏览器，原大肥鱼的网页/B站实现尚未移植 |
| 云端微信与记忆同步 | 已接入自定义 `dafeiyu-sync` World；本地与云端共享经历，微信只在云端连接。详见 [同步说明](companion-sync.md) |

官方来源：[项目与功能](https://github.com/Pal-AI-Lab/Coopanion/tree/v0.2.6)、[开发结构](https://github.com/Pal-AI-Lab/Coopanion/blob/v0.2.6/docs/DEVELOPMENT.md)、[记忆实现](https://github.com/Pal-AI-Lab/Coopanion/blob/v0.2.6/core/persona.ts)、[DeepSeek provider](https://github.com/Pal-AI-Lab/Coopanion/blob/v0.2.6/packages/cortico-provider-coo/src/vendors.ts)、[电脑权限](https://github.com/Pal-AI-Lab/Coopanion/blob/v0.2.6/packages/cortico-world-desktop-pet/src/cua/config.ts)。

## 已完成验证

1. 官方 v0.2.6 Windows 便携包的 SHA-256 与 GitHub release digest 一致：`9efdb5a18d889d964f09f3293cf9a6b612669a6f95c707851c93bbed2d52fc7e`。
2. 本地 API 报告 `deepseek-flash`、DeepSeek 官方端点、连接 ready；桌宠 `onDesktop=true`、`connected=true`。
3. 部署测试让模型写入 `deployment-check.md` 并回答。文件实际存在，Git 中有对应版本提交。
4. 发现恢复的旧会话仍描述 Coo 形象和关闭的电脑工具；调用框架原有 `/api/session/reload-prefix` 保留历史并刷新配置。复测回复已正确说明鲸鱼女仆形象、电脑操作需询问和微信历史未同步。
5. 本地语音模型约 228 MiB，下载完整，识别后端 running，麦克风 off。

## 本地与云端的分工

本地运行 Coopanion / Cortico，提供桌宠、麦克风、电脑操作和本地扩展；云端继续运行 Python / Hermes 聊天服务及唯一的微信连接。两端没有合并成一个模型会话，但通过记录同步延续同一段经历。云端使用本地当前人设卡的原文快照，不加载旧大肥鱼的风格示例、口吻后处理与自动人格成长。

本地默认每 5 秒上传新聊天和 Markdown 笔记、下载云端聊天/记忆/日记。断网时本地记录保留，重连补传；关机前尚未同步的内容，云端须等下次连接才能看到。原始导入记录位于 `workspace/shared/`，并可通过 `shared_memory_search` 检索。

关闭本地应用或电脑不会停止云端微信。微信中的电脑操作暂不转发到本机，需要在本地桌宠发起；离线时云端仍可聊天、查已同步记忆及公开资料，不应把网页读取失败误说成电脑关闭。

云端公开资料查询已经发布并启用；其原有 B 站和浏览器功能保持现状。本地原装工具尚未直接加入相同网页/B站工具。

当前 `CONSTITUTION.md` 尾部保留了试用部署时的旧环境备注，为遵守“不改人设卡”没有改动；新增 World 的环境说明与云端运行说明会指出共享记录已接通，以实际工具和状态为准。

## 升级与备份

便携版不自动升级。退出应用后备份整个 `.research/Coopanion/data/`，其中包含密钥，备份按私密数据保管。升级使用官方新版本便携包，先独立解压，再迁移 data 并核对连接、记忆、桌宠；不要覆盖正在运行的数据。

本次安装包保存在 `.research/coopanion-download/`，来源是 [官方 v0.2.6 发布页](https://github.com/Pal-AI-Lab/Coopanion/releases/tag/v0.2.6)。启动脚本只负责启动已部署实例，不会自动下载或覆盖配置。
