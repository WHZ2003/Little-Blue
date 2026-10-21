# ds-local-pet 深入复用评估

日期：2026-10-20。结论：采用独立底层模块，保留 Little Blue 的生命/认知/记忆/权限主架构。已经完成局部运行验证，尚未替换正式应用模块。

## 证据与成熟度

本次重新查询上游，最新提交仍为 [`d78094b2cf04e1cc3be5d14a31cfedd8ed059579`](https://github.com/QCYTSN/ds-local-pet/commit/d78094b2cf04e1cc3be5d14a31cfedd8ed059579)，提交日期 2026-08-09。固定版本的 66 个源码、测试和许可文件存于 `.research/ds-local-pet/`，研究缓存不进入 Git；没有下载上游角色图片。

本机 Windows / Python 3.13 / PySide6 6.12 上，**32 项上游测试通过，10 项本地兼容性/边界探针通过**。上游同一提交的 [Windows CI](https://github.com/QCYTSN/ds-local-pet/actions/runs/31307735046) 已确认成功，其测试、素材校验、版本参数和导入检查步骤均成功；[发布构建](https://github.com/QCYTSN/ds-local-pet/actions/runs/31308343338) 的 portable bundle 和安装包构建也成功。

这些证据支持它是可用的 v0.2 桌宠底座，但不能证明长期稳定性、混合 DPI 或所有多屏场景。应修正初次评估由约 50 KB 主窗口耦合推及底层模块的判断：**整仓迁移不合适，但物理、交互、播放器和恢复模块可以独立复用。**

## 建议直接采用的模块

| 优先级 | 上游模块 | 相比当前实现的收益 | 实际接入边界 / 成本 |
|---|---|---|---|
| 第一批 | `pet/physics.py` / `ThrowPhysics` | 当前 Body 只有基础重力和边界；增加拖放惯性、限速、墙壁反弹、摩擦、落地冲击、平台碰撞和子步积分 | 标准库独立模块，移植成本低；保留现有走/跑/跳目标，抛掷期间让物理层独占位置更新。包含式 right/bottom 需由当前独占式边界减 1 转换 |
| 第一批 | `pet/interaction.py` / `InteractionController` | 当前仅拖动阈值和上半身点击；增加最近 160ms 释放速度、暂停后释放归零、持有时长和取消 | 抽取控制器，去掉同文件 FoodPanel；接入现有 Qt 鼠标事件，重设角色命中区域及镜像规则。代码抽取容易，宿主事件联调成本中等 |
| 第一批 | `animation/clip.py`, `player.py`, `state_machine.py`, `transitions.py` | 当前主要按时间取帧并手动覆盖 talk；增加 enter/body/exit、动作优先级、排队及有限动作结束 | 七种共有动作的 QPixmap 桥接已通过；保留 SpriteBank，补 Run/Jump，修复相位与 FPS 更新问题。宿主动作仲裁和素材过渡段需要适配，成本中等 |
| 第一批，可独立并入 | `app/recovery.py` / `CrashRecovery` | 当前有日志/状态保存但没有连续异常退出检测；增加会话标记、心跳、安全模式建议及异常报告 | 标准库模块、路径和时钟可注入，抽取成本低；宿主落实安全模式。在线错误需脱敏、报告限量，并复用现有日志配置 |
| 第二批 | `awareness/fullscreen_detector.py` | 当前主要检查主屏边界；上游按目标显示器判断，排除普通有边框最大化窗口 | 单文件复用，保留当前总感知开关；负坐标副屏的模拟测试通过，真实应用/缩放仍待验收 |
| 第二批 | `pet/surfaces.py` / `DesktopSurfaceTracker` | 窗口顶部停留、窗口移动跟随、消失后脱离；当前没有这些能力 | 接在物理层之后；先修感知入口和验证混合 DPI，再接宿主窗口。只维护候选/附着窗口，不能当作完整桌面遮挡碰撞引擎；联调成本中高 |
| 后续体验完善 | `app/single_instance.py` | 当前 QLockFile 只提示已有实例；上游 IPC 可激活已有窗口 | 独立 QtNetwork 模块，测试通过；使用 LittleBlue + 用户/规范化数据目录命名，与现有锁策略协调，额外测试进程竞争和多配置实例 |

物理模块的 [独立接口](https://github.com/QCYTSN/ds-local-pet/blob/d78094b2cf04e1cc3be5d14a31cfedd8ed059579/pet/physics.py) 是最值得保留原算法直接采用的部分。播放器的 [阶段实现](https://github.com/QCYTSN/ds-local-pet/blob/d78094b2cf04e1cc3be5d14a31cfedd8ed059579/animation/player.py) 也值得采用，但需要下述补丁；这比重新设计一个阶段播放器更节省工作。

## 选择性复用，避免两个系统争抢控制权

| 模块 | 值得采用 | 保留/调整边界 |
|---|---|---|
| `behavior/activity_director.py` | 近六次活动抑制、活动强度档位、活动前后小动作、独立 MicroMotionDirector | 当前 Life 已有 Utility AI、序列、冷却、人格和情绪。上游状态为 0–1、当前为 0–100，且上游没有同等 curiosity/social/emotion 模型；不并行运行两个顶层选择器。把近期历史逻辑接到 Life.utilities，微动作作为空闲时的表现层 |
| `behavior/scheduler.py` | 停留去抖、空闲/返回边沿、连续确认全屏、每日深夜事件 | 从当前获准的 Environment 产生事件；没有授权时不生成基于窗口身份的事件。保留认知层主动发言冷却和预算，避免每个环境事件都触发 API |
| `animation/effects.py` | `EffectPose` / `pose_for` 是纯函数；呼吸、落地压缩、轻微晃动和影子参数 | 可直接抽取并由 QPainter 应用；按我们的已绘制动作减小幅度，支持 reduced-motion，避免对逐帧跳跃重复施加位移。仅完成源码审查，未完成视觉验收 |
| `pet/renderer.py` | 锚点、图层变换、阴影绘制 | 独立程度高于主窗口，但绑定气泡与人物共用槽位、角色名称和两行文本；我们使用独立气泡。先抽取绘制方法，人物锚点必须与物理尺寸一致 |
| `tools/normalize_assets.py` | `_runtime_frames` 按整组帧使用同一比例、画布和足底基线，有助减少抖动 | 只抽取函数，保留逐动作锚点；不照搬角色路径表。仅审查源码，没有生成或修改本地图片 |

## 不建议替换的部分

- **主窗口和配置体系**：上游 `pet/window.py` 同时绑定配置、台词、状态、活动和渲染。整块接入会重做我们现有的 Agent UI、设置和信号连接。
- **素材加载器**：`asset_registry.py` 写死上游资源 ID、manifest 布局和独立左右素材，镜像函数始终返回 False。它缺少我们已实现的路径包含检查、图片像素预算和总帧数限制。保留 SpriteBank，增加播放器桥接即可。
- **生命状态和保存**：`PetState` / `PetStateStore` 的浮点状态与 JSON 保存无法替代当前 Needs/Emotion、SQLite、FTS5 和记忆/任务状态；不为统一上游接口牺牲这些能力。
- **感知/隐私整体**：`ContextSensor` 的标题开关不是禁止采集，`PrivacyPolicy` 是本地匹配过滤而非授权网关。只采用独立 detector，不替换 Permission Gateway。
- **移动控制器**：`MovementController` 直接操作 QWidget、屏幕和鼠标，使用上游 energy 标度，仅控制水平移动。优先保留当前走/跑控制，复用 ThrowPhysics；不要同时让两套控制器写位置。
- **更新器**：`app/updater.py` 有 Release 查询和 SHA-256，但绑定发布源/安装包名称，线程没有取消接口，下载缺少总时限/大小上限。留到正式发布阶段专项适配。
- **整套素材管线**：归一化函数可取，最大连通域清理可能误删独立装饰；验证脚本写死走路/落地素材、候选元数据与授权字段，不适合作为通用导入器。

## 实测发现的适配问题

1. **转向回到第 0 帧**：我们的 walk 为 8 FPS，推进 0.28 秒到第 2 帧后镜像转向，上游 elapsed 变为 0，画面回到第 0 帧。`start_with_enter=False` 只跳过起步段。移植时需在同动作 BODY 转向时保存/恢复周期进度，并增加回归测试。
2. **同资源 FPS 更新失效**：把 `frame_duration_ms` 从 125 改为 250，再调用 play 返回 False，仍使用 125。clip identity 不含时序信息。需加入时序/版本标识或显式更新接口；帧源身份也应区分 sheet 的每一帧。
3. **循环动作必须由宿主明确退出**：高优先级循环 SLEEPING 拒绝普通 IDLE 请求，update 不会自动结束它，force 转移可结束。属于接口协议；上游主窗口已有相应 force 逻辑，不能据此声称上游应用一定卡住。我们的 Life/物理完成事件也必须明确驱动退出。
4. **标题开关只清空输出**：Mock reader 提供合成标题，`capture(read_window_title=False)` 仍调用 reader 并把标题送入 classifier。未读取任何真实窗口标题。原样复用不符合我们“关闭即不采集”的约定。
5. **关闭平台仍读前台句柄**：`DesktopSurfaceTracker(enabled=False).observe_foreground()` 仍调用 foreground_handle，但不会调用矩形 reader。应把 enabled 检查移到入口最前面。
6. **规则读取失败回退空过滤**：缺失隐私规则文件后，不匹配的进程/标题返回非 private。它不能充当默认拒绝的授权层。

另外，`Win32WindowRectReader._logical_rect` 将全局坐标按窗口 DPI 统一相除，混合 DPI 多屏原点处理存在风险。这是静态审查判断，**没有真实多屏复现**。需要以各 QScreen 原点和缩放映射验证，不能仅靠窗口 dpi 除法推断正确。

CrashRecovery 检测两次连续异常退出后建议安全模式；不会修复数据库，也不负责检查或重放 Agent 任务。原始 traceback 可能包含在线请求内容，移植时应脱敏并限制报告数量。Qt 主线程异常钩子也不能取代现有后台任务异常处理。

## 本地验证记录

| 验证 | 结果 | 范围 |
|---|---|---|
| 上游 physics | 6 passed | 限速、地面反弹/硬着陆、墙壁反弹、轻放、平台降落、行走不改 y |
| 上游 surfaces | 2 passed | 假窗口移动/消失，无真实窗口读取 |
| 上游 recovery | 3 passed | 正常退出、两次连续异常、安全标记和报告 |
| 上游 activity_director | 9 passed | 冷却、低能量、隐私抑制发言、散步等待、午睡、模拟一小时、微动作等 |
| 上游 scheduler | 3 passed | 停留、idle/return/fullscreen 边沿、每日夜间事件 |
| 上游 interaction_regions | 2 passed | 身体区域分类，不代表我们的角色命中区域已适配 |
| 上游 animation_system 的状态机/播放器 | 6 passed | 优先级、动作结束/排队、阶段及防重影；排除 1 项 manifest 测试 |
| 上游 single_instance（独立进程运行 pytest） | 1 passed | 同一测试进程中两个实例对象经本地 IPC 通知；不是跨进程竞争启动压力测试 |
| `tools/audit_ds_local_pet.py` | 10 passed | 七种共有动作桥接、转向/FPS反例、受控唤醒、拖速/停顿/取消、30/60Hz物理边界、副屏全屏模拟、感知边界 |

首次 recovery 测试因沙箱临时目录 PermissionError 失败，将 TEMP/TMP 指向工作区 `.research/tmp` 后三项通过；这是执行环境问题，未修改上游代码。上游 PNG/manifest/UI 渲染套件没有在本机全量运行，不应把 32 项当作全仓测试。上游 CI 的成功是独立证据，不替代我们的集成测试。

10 项探针中名称含 `known_limit` 的测试用于**确认缺陷/边界存在**，通过不代表这些问题已经修好。PoC 验证了 QPixmap 和帧选择兼容性，没有宣称完成正式 UI、所有九个动作或性能验收；Run/Jump 还需扩展。物理 30/60Hz 测试验证边界及最终稳定落地，不是实时 FPS 基准。

复现（工作区根目录，已具备研究缓存及项目依赖）：

```powershell
New-Item -ItemType Directory -Force .research/tmp | Out-Null
$env:TEMP = Join-Path $PWD '.research/tmp'
$env:TMP = $env:TEMP
& .venv/Scripts/python.exe -m pytest -q .research/ds-local-pet/tests/test_physics.py .research/ds-local-pet/tests/test_surfaces.py .research/ds-local-pet/tests/test_recovery.py .research/ds-local-pet/tests/test_activity_director.py .research/ds-local-pet/tests/test_scheduler.py .research/ds-local-pet/tests/test_interaction_regions.py .research/ds-local-pet/tests/test_animation_system.py -k 'not AssetManifestTests'
& .venv/Scripts/python.exe -m pytest -q .research/ds-local-pet/tests/test_single_instance.py
& .venv/Scripts/python.exe tools/audit_ds_local_pet.py -v
```

研究源码可从报告首部固定提交获取对应目录；不要从漂移的 main 取代码复现本次结果。探针不属于默认应用 pytest 套件，不依赖用户密钥，不读取 .env，也不连接 DeepSeek。

## 具体迁移顺序与验收条件

1. **建立来源记录并采用物理/交互**：放入 `little_blue/vendor/ds_local_pet/`，内部 import 改为相对路径，保留许可证和上游 SHA/补丁说明；新增适配层接 Qt 事件和现有 Body。验收快速甩动、拖后停顿、取消拖动、边缘反弹、低能量休息，以及离线仍继续活动。
2. **采用阶段播放器/动作仲裁**：保留 SpriteBank，补 Run/Jump，修复相位与时序 identity；按明确规则仲裁 drag/fall/landing/interaction/talk/idle，Life 继续负责目标。验收转向不重启步态、说话不抢占拖动和落地、睡醒可退出、原九种动作不退化。真实 enter/exit 动画需要相应素材，播放器本身不会补出新帧。
3. **采用恢复和全屏检测**：将 recovery 接入主进程生命周期，落实安全模式并保留“未完成 Agent 任务重启后中断”的规则；detector 只在感知授权时调用。验收两次崩溃降级、干净退出复位、关闭感知时零读取、副屏全屏抑制打扰。
4. **最后加入窗口平台、微动作和归一化**：先解决混合 DPI/平台感知开关，补真实屏幕验证；微动作仅在允许打断的空闲状态触发。美术、碰撞与锚点共同验收。

这是一条增量迁移路线，无需 fork 整个上游应用。第一批实际采用 7 个源文件中的相应类/函数：physics、interaction、clip、player、state_machine、transitions、recovery；其余按收益逐步引入。不要为复用一个类把同文件无关 UI 一并带入。

## 许可与本轮变更

[LICENSE](https://github.com/QCYTSN/ds-local-pet/blob/d78094b2cf04e1cc3be5d14a31cfedd8ed059579/LICENSE) 和 [ASSET_LICENSE](https://github.com/QCYTSN/ds-local-pet/blob/d78094b2cf04e1cc3be5d14a31cfedd8ed059579/ASSET_LICENSE.md) 区分 MIT 代码与角色图像；[CREDITS](https://github.com/QCYTSN/ds-local-pet/blob/d78094b2cf04e1cc3be5d14a31cfedd8ed059579/CREDITS.md) 还记录了更早的 dafeiyu 项目来源（仓库名 `1190fasheqi/dafeiyu-pet`）。复制代码时保留完整来源链；不复制上游图像。

本轮修改仅为评估文档、独立探针和 `.gitignore`：后者补充 `.env.*`（保留 `.env.example` 例外），防止 `.env.txt` 一类密钥文件进入暂存。没有读取或输出密钥，也没有调用模型。当前应用只读取进程环境变量 `DEEPSEEK_API_KEY`，尚未自动加载 `.env`；单独创建该文件不等于应用已经获得密钥。
