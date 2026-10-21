# 走路动画检查与修正 · 2026-10-21

原默认素材 `atlas-v3.png` 的 walk 行只有四帧，多个姿势都接近同一迈腿阶段，缺少明显的收腿／交错过渡，循环接点跳变。桌面渲染还存在站姿说话时沿 WALK 指令平移、鼠标跟随移动却播放 IDLE、聊天时站定仍播放 WALK 的不一致。远程轮询会重复覆盖本地碰撞转向，使边界处可能来回翻面。

修正：

- 新增透明的八帧 `assets/blue/walk-v4.png`，只替换 walk，原 atlas 和其他动作保留。
- 清单使用同尺寸 420×436 的逐帧矩形，并按头部位置调整切片起点，避免上下行大小不一致或切到邻帧。保持 8 FPS，完整周期从 0.5 秒变成 1 秒。
- SpriteBank 支持动画单独指定 sheet，继续执行路径、像素预算和切片边界检查。
- 身体与显示动作一致；站姿说话时不再横向滑动，鼠标跟随显示走路，聊天停住时显示 idle。迈步进度随实际水平距离推进，转身不重置步态。
- 相同旧方向快照不再撤销本地边界转向；新方向和断线重连仍接收核心状态。

验证：`tests/test_ui.py tests/test_remote_ui.py tests/test_life.py`，**22 passed / 15.17s**。覆盖独立动画图集、不影响其他动作、路径越界拒绝、说话／聊天暂停、鼠标跟随、步态时钟、边界方向同步及现有 UI、物理回归。

`python tools/preview_walk.py` 生成 `qa-output/walk-preview.html`，本地对照原四帧与新八帧，支持暂停、逐帧、慢放和位移；不连接正式核心、微信或 API。动画仍是有限帧的手绘 Sprite，不宣称骨骼插值或每秒 60 张不同姿势。

## 生成方式和最终提示词

使用内置 image_gen，未使用 CLI 或另一个付费 SDK。首轮以用户已认可的 atlas 为角色参考；首轮候选因转脸、缺少 passing pose 而未接入。最终 asset 是对首轮候选的定向修正，保留透明通道。最终提示词如下：

> Precise animation correction of this 8-frame 4-column 2-row transparent sprite sheet. Keep the exact approved character, rendering style, costume, image grid, character scale and transparent background. This is a WALK IN PLACE cycle, not a set of independent poses. CRITICAL FIX 1: all eight frames must use the SAME right-facing head/face and torso orientation as frame 1 (upper left). The lower row must NOT turn to face the viewer. Keep head, eyes, body root, apron top identical in position, size, angle. CRITICAL FIX 2: current feet are spread apart in almost every frame and there is no passing pose, which makes the loop look like sliding. Replace the LEGS/SHOES below the skirt with a genuine alternating step: frame1 near leg forward/far leg back contact; frame2 near leg planted almost vertical, far leg knee bent and trailing shoe lifted; frame3 BOTH LEGS CLOSE TOGETHER under the skirt, far knee passing in front of near leg, one lifted shoe; frame4 far foot forward approaching contact, near foot behind; frame5 far leg forward/near leg back contact; frame6 far leg planted almost vertical, near knee bent and trailing shoe lifted; frame7 BOTH LEGS CLOSE TOGETHER under the skirt, near knee passing in front, opposite leg order of frame3; frame8 near foot forward approaching contact and looping to frame1. The two tucked passing poses in cells 3 and 7 are mandatory and should clearly differ from the wide split stance. No running, both feet never simultaneously airborne, no giant strides. Keep skirt and hair consistent, almost motionless upper body, tiny bob only. Precisely align all upper bodies relative to their equal cells and the grounded shoe baseline across all frames. Keep transparent margins around every complete sprite so no cell touches adjacent cell. No labels or grid marks. Preserve alpha transparency.
