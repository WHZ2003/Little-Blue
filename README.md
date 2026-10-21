# DeepSeek · Agentic Desktop Pet

**可运行的 v0.1 Alpha。** 基于用户提供的蓝发鲸鱼女仆参考图，采用 PySide6 透明窗口和 2D Sprite 逐帧动画。生命模拟完全离线运行；DeepSeek 只在聊天、主动交流或反思事件发生时调用。

角色默认身份为 **DeepSeek**，「大肥鱼」是用户对她的亲昵戏称；设定与参考来源见 [角色身份](docs/character.md)。源码包名 `little_blue`、Docker 项目名和旧数据目录保留，以便继续读取既有记忆。Docker 核心支持可选的 ClawBot / iLink 微信文字通道，扫码绑定后启用；见 [微信接入说明](docs/wechat.md)。

支持两层身份卡：核心身份固定，成长层依据用户长期记忆学习称呼、表达和陪伴偏好。右键桌宠 → **身份与成长…** 查看修改依据、撤销或关闭自动更新；微信支持 `/identity`、`/identity 版本号`、`/growth off`、`/growth on`、`/growth undo`。自动整理计入 API 预算，最多每 6 小时一次，详细证据规则见角色身份说明。

记忆支持 **SQLite／FTS5 + 本地中文向量检索**，合并两路结果并去重，让不同说法也能找回相关经历。旧记忆自动补建索引，缺少模型时继续关键词检索。微信 `/memory` 查看进度，`/memory off` 关闭、`/memory on` 开启、`/memory rebuild` 重建；原文始终保留。模型准备、原理及验证见 [混合记忆说明](docs/hybrid-memory.md)。

微信支持社区表情包：`/memes` 查看列表，`/meme 087` 点发图片；联网聊天可少量自动配图，`/memes off` 关闭自动配图。安装、来源及限频见 [表情包说明](docs/memes.md)。

点击角色即可打开小聊天窗、交代任务、查看当前事项。说“我正在准备答辩”会记下，微信回复“做完了”也能更新；最近对话、当前事项和长期记忆由同一核心持久保存。小窗支持完成、晚点再问、关闭跟进与工具审批，详见 [日常陪伴与跨渠道续聊](docs/companionship.md)。

虚拟手机内置 **2048、浮漂钓鱼、小小水族箱**。小聊天窗点击“手机”或右键“她的手机…”打开；她能自主挑游戏，也能由你接管真实游玩。最高分、鱼类收藏、喂食成长和小窝布置会保存，桌面／微信用 `/phone` 查看摘要。游戏完全离线，不耗模型请求，详见 [虚拟手机说明](docs/virtual-phone.md)。

## 立即启动

新增**独立浏览器**：Docker 中运行她自己的 Chromium，低频自主阅读科普网页，真实标题、来源和摘录进入原有记忆。右键桌宠 → **她的浏览器…**，或运行 `browser.ps1`，可随时查看、接管和暂停。重启浏览器默认暂停，点击“让她自己逛”开启；详见 [独立浏览器说明](docs/independent-browser.md)。

有趣的浏览内容可偶尔通过微信分享：短话加真实链接，允许沉默、限频且受勿扰控制，`/sharing off` 可关闭。Windows 私聊／群聊桥接已准备核心接口和隔离记忆，但本机微信 4.1.15.56 的 UIA 发送者信息缺失，**尚未替换 iLink**；兼容性证据和剩余验证见 [Windows 桥接说明](docs/windows-wechat-bridge.md)。

打包版：打开 `dist/LittleBlue/LittleBlue.exe`。请保留同目录的 `_internal` 文件夹，不能只复制 EXE。

源码版（当前工作区已配置 `.venv`）：

```powershell
.\start.ps1
```

默认只显示桌宠和系统托盘图标。已配置核心令牌时，脚本默认连接 Docker 核心，以便与微信共享记忆；请先启动核心。需要独立本地模式可显式运行 `start.ps1 -LocalOnly`。需要完整设置时，右键桌宠选择「DeepSeek控制台」，或运行 `start.ps1 -Panel`。

- 点击桌宠：打开小聊天窗；再次点击、Esc 或“收起”隐藏。点击头部同时摸头。右键“查看状态”仍提供 5 秒状态卡。
- 按住鼠标移动：拖动；松开后受重力下落。长时间拖动会不高兴。
- 右键：控制台、摸头、跳跃等菜单；双击不会弹出控制台。
- 控制台关闭后仍继续生活；右键或系统托盘选择「保存并退出」结束。
- 「设置」可分别关闭主动交流、环境感知、联网和工具访问。
- 工作区的 `reference/` 是原始参考；实际使用 `assets/blue/manifest.json` 和用户确认的 `atlas-v3.png`。

## Docker 核心模式

生命模拟、认知、记忆和任务权限可在单个 Linux 容器运行，Windows 桌面端通过本机认证接口连接。已配置 Docker Desktop 时：

```powershell
.\start-core.ps1
.\start.ps1 -ContainerCore
```

容器会从根目录 `.env` 在运行时加载密钥，数据保存在独立命名卷。本机旧数据库没有自动迁移；`-LocalOnly` 可显式访问独立本地数据。详见 [容器边界、启动与验证](docs/container-core.md)。已有 EXE 尚未重新打包，请使用源码命令启动共享核心模式。

## DeepSeek 设置（本地模式）

密钥仅从 `DEEPSEEK_API_KEY` 环境变量读取，不写进源码、SQLite 或日志。通过 Windows 的「编辑账户的环境变量」界面配置后，重新启动应用，再在设置中开启联网认知。不要把密钥发到聊天或提交 Git。

默认模型 `deepseek-flash` 来自开发时核对的 [DeepSeek Chat Completions 官方文档](https://api-docs.deepseek.com/api/create-chat-completion/)，可以在设置修改。请求固定发送到 `https://api.deepseek.com/chat/completions`。联网会发送当前对话、内部状态、相关记忆和经过批准的工具结果。2026-10-20 已按用户授权完成一次真实模型身份回复验证；API 错误、取消与工具循环另以模拟传输测试。新安装默认离线，当前已绑定的核心已开启联网并持久化。

每次请求在发送前持久化预留预算，失败或取消也不退回。Token 预算采用 UTF-8 字节数加输出上限的保守估算，不代表实际计费。默认最多 60 次/日、60000 保守 Token/日，单次输出最多 768 Token。预算不足不影响桌宠生活。

## 当前功能

| 模块 | 已实现 |
|---|---|
| 身体 | 9 组动作，每组 4 帧；PNG 序列/图集导入；方向翻转；重力、跳跃、屏幕边界；拖动、摸头、可选鼠标跟随 |
| 生命 | 六项 Needs；稳定人格；渐变 Valence–Arousal 情绪；Utility 评分、带状态的行为树 Sequence、冷却和持续时间 |
| 认知 | asyncio 后台线程；事件驱动 DeepSeek 调用；有限步工具规划；取消、超时、预算；离线回复 |
| 主动交流 | 启动宽限、15 分钟间隔、每小时最多 3 次；勿扰和全屏抑制；可选工作时长提醒 |
| 记忆 | Working Memory；SQLite 经历/偏好/反思；FTS5 + 中文子串回退；显式保存用户偏好；手动或低频总结 |
| 权限 | 工具总开关；每次参数验证；文件、搜索、浏览器、提醒、锁屏逐次审批；审批期间可撤回；默认拒绝 |
| 感知 | 本地时间；可选 Windows 空闲时间、主屏全屏状态、鼠标；用户发起的单次本机截图 |
| 工程 | 持久状态、单实例锁、轮转日志、状态面板；重启将未完成任务标记 interrupted，不自动重跑；PyInstaller 配置 |

工具包含 `get_time`、`get_activity`、`web_search`、`create_reminder`、`read_file`、`browser_open`、`browser_read`、`system_action`。系统操作只支持经过确认的 Windows 锁屏，没有任意命令执行入口。主动交流与反思不能调用工具。

`browser_open` 用系统默认浏览器打开经批准的 HTTPS 页面。`browser_read` 是可选 Playwright 功能：使用隔离、无登录状态、禁用 JavaScript 的浏览器读取公开页面；不操作用户已登录的标签页。源码环境启用方式：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

本次轻量 EXE 不包含 Playwright/Chromium；其浏览器功能为打开页面。网页搜索依赖 Bing RSS 服务，服务变化或断网时会报告失败。

## 开发与测试

```powershell
conda env create -f environment.yml
conda activate little-blue
python -m little_blue --panel --debug
python -m pytest -q
python -m PyInstaller --noconfirm LittleBlue.spec
```

也可使用 Python 3.11+ 的普通 venv，运行 `python -m pip install -e ".[dev]"`。本机实际验证环境为 Windows 11 / Python 3.13.11 / PySide6 6.12.0，完整依赖快照在 `requirements-lock.txt`。普通命令安装所需 pip 若不可用，也可使用 `uv pip install --python .venv/Scripts/python.exe -e ".[dev]"`。

测试覆盖自主选择、5 小时模拟边界、情绪平滑、交互、物理、SQLite 重启、检索特殊字符、预算持久化、审批冻结和撤销、文件限制、网络错误、取消、工具循环、Qt 交互与网络等待时动画继续。

```powershell
python -m little_blue --data-dir .\data\smoke --panel --smoke-seconds 3
python tools\preview_ui.py
```

默认持久化目录 `%LOCALAPPDATA%/LittleBlue`，包含 `memory.sqlite3`、轮转日志与单实例锁。可使用 `--data-dir` 隔离测试。日志记录故障类别，不记录密钥和工具参数。聊天和用户记忆保存在本机数据库中。

## 当前验收边界

这是贯通主要模块的 Alpha，尚不是 Phase 6 的稳定发布版：

- 角色帧由内置 ImageGen 依据用户参考图生成，已检查切片和透明背景；4 帧动作仍需进一步美术精修，尤其走跑循环一致性。
- 30–60 FPS 是 UI 定时器目标；本机 10 秒离屏 Qt 基准在模拟网络等待期间测得平均 58.93 次绘制/秒。尚未对各种机器、实际桌面合成或整夜运行进行验证。
- 碰撞目前针对屏幕可用区域和任务栏边界，不把其他应用窗口当作平台；可在多显示器之间拖动。
- 环境感知默认关闭，不读取窗口标题。全屏识别当前针对主屏；手动勿扰始终有效。截图只按用户明确的保存操作执行，不上传给模型。
- 任务以最多 5 轮认知、每轮最多 4 个工具调用限制；总超时 240 秒，审批最长 120 秒。取消不回滚已经完成的外部操作。
- 提醒仅在应用运行时触发，退出期间到期的提醒会在下次启动后交付；勿扰期间延后交付。
- 已验证真实 DeepSeek 文字回复及微信收发记录。独立浏览器已通过真实 Chromium 页面读取与接管测试；旧网页搜索工具、锁屏和长期运行仍未完整实测，详细结果见 [验证记录](docs/validation.md)。
- 数据库的损坏修复、记忆编辑/删除 UI、系统原生 Focus Assist 集成、签名安装器和自动更新尚未实现。

仓库评估见 [docs/repository-research.md](docs/repository-research.md)，设计与阶段验收见 [docs/architecture.md](docs/architecture.md)，素材生成说明见 [docs/art-assets.md](docs/art-assets.md)。本项目不是 DeepSeek 官方产品。
