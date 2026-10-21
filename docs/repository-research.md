# 开发前仓库评估

后续深入审查见 [ds-local-pet 复用评估](ds-local-pet-reuse-audit.md)。初次关于整仓耦合的判断不适用于所有底层模块：物理、交互、动画阶段及崩溃恢复值得增量采用。现已通过 32 项上游测试、10 项适配/边界探针，并完成现有素材到上游播放器的最小桥接；正式应用尚未引入上游源码。下面保留初次评估背景，最新复用决策以深入报告为准。

检查日期：2026-10-20。通过 GitHub 仓库元数据、递归文件树、最新提交，以及实际源文件检查；没有直接合并三个项目，也没有复制它们的角色素材。

| 仓库 | 检查版本 | 维护状态 | 许可证与判断 |
|---|---|---|---|
| [QCYTSN/ds-local-pet](https://github.com/QCYTSN/ds-local-pet) | `d78094b2cf04e1cc3be5d14a31cfedd8ed059579` | 未归档；最新提交 2026-08-09 | 代码 MIT；角色图片另行声明，不包含在 MIT 授权中 |
| [sjmsltx/deepseek-desktop-pet](https://github.com/sjmsltx/deepseek-desktop-pet) | `07bfe7863f961877fff450f4e6dc58d3b0e369f0` | 未归档；最新提交 2026-09-21 | MIT；适合认知和治理思路参考 |
| [Litrudy/DesktopPet](https://github.com/Litrudy/DesktopPet) | `f3067755833ba13859a6d3a9fa904177c684c1ac` | 未归档；最新提交 2026-06-29，Initial desktop pet app | 文件树未发现 LICENSE；不直接复制代码/素材 |

## 可复用程度

主参考实际包含 `animation`、`behavior`、`awareness`、`pet`、`settings` 分层。检查 `animation/player.py` 可见帧时钟、动画阶段和渲染快照；`behavior/activity_director.py` 包含需求加权、冷却、近期行为去重和多阶段活动。窗口模块约 50 KB，与已有状态/资源格式紧密关联，整仓套用会带入本项目不需要的假设。其 [素材许可说明](https://github.com/QCYTSN/ds-local-pet/blob/d78094b2cf04e1cc3be5d14a31cfedd8ed059579/ASSET_LICENSE.md) 明确区分图片与代码。决定：借鉴分层和时间控制，自行实现可测试的生命核心，使用用户参考生成素材。

Agent 参考的 `deepseek_client.py` 将 HTTP 网络逻辑与 UI 分开；`tools_executor.py` 说明部分复杂工具仍依赖主窗口，`desktop_pet.py` 约 412 KB。它拥有较丰富测试与治理模块，但同步网络调用及宿主状态约定无法直接适配当前 asyncio 架构。决定：自行实现有限步 Agent、Pydantic 参数模型、一次性审批和持久任务状态。

动画参考的 `app/pet/animation_player.py` 是 QTimer + QPixmap 帧播放器，`behavior_scheduler.py` 主要是随机调度，并非 Needs 驱动生命系统。决定：仅参考模块职责；当前播放器采用独立绘制定时器和经过检查的 manifest，不复制未明确授权的实现。

这些结论是针对检查版本的工程判断；提交时间和仓库未归档不构成对长期维护质量的保证。当前项目没有引入上述仓库源码，因此没有把它们的 MIT 许可证错误地应用到用户素材。
