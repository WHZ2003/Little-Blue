# Windows 微信桥接：准备完成，尚未切换

## 当前实测结论（2026-10-21）

Windows 安装版本 **4.1.15.56**。已确认登录后的主界面、指定群“米游团建🍚”、消息正文和 `@大肥鱼` 后的 U+2005 分隔符可读取。只读探针实际看到消息控件为 `ListItem / mmui::ChatTextItemView`，而非部分旧示例里的 `CheckBox`。

**当前 UIA 没有逐条发送者或收发方向信息。** 消息元素没有子控件，Legacy Description、Help、ItemStatus、AriaProperties、FullDescription、Value 均为空；正文不包含发送者前缀。不能把会话列表中最后一条预览的名字套用到所有消息，也不能把用户输入的姓名当作身份。UI 文本里的 @ 与协议级 mention ID 也不等价。

因此 WindowsUI 适配器的 `identity_supported=False`，不会报告可发送状态；没有替换正在工作的 iLink，没有向测试群发送消息，没有根据手机号、密码、昵称猜测或迁移用户身份。当前没有完成：私聊往返、群聊往返、非 @/引用 @ 负例、表情发送、真实锁屏、断线恢复及账号映射验证。不能把离线测试视为这些项目已通过。

## 已准备的核心

- 新 `/bridge` 接口只接受独立 bridge token，可报告健康、接收已规范化消息、领取一次发送许可、确认结果。该令牌不能读取 `/state`、调用管理 RPC 或电脑工具。
- `/rpc` 的 bridge 管理由本机 core token 授权。绑定必须显式指定机器人账号、原主人的新私聊联系人、群和成员标识，不通过显示名自动合并身份。
- 原主人私聊复用原 SQLite、话题摘要、成长设定和向量检索。其他联系人／每个群使用独立的 `/data/conversations/<hash>.sqlite3`，群内按 sender ID 区分成员；群内的主人也不会读到私聊库。
- 新群库当前使用 SQLite／FTS 检索；原主人的本地向量索引保持不变。群模型只收到本群历史、记忆、公开角色设定和当前情绪，不收到主人的身份卡、活动、浏览经历或私聊摘要。
- 首版只有聊天和注册表情，不接受任意工具、系统命令、文件路径、动态收件人。群必须在白名单、发送者已识别、且有已验证的 @ 证据。
- 收件箱持久去重并合并连续消息；发送许可只领一次，结果不明不重发，不写成成功记忆。未完成任务重启后不续发。
- 单群发言最短 30 秒、最多 6 次／小时，私聊最短 5 秒、最多 30 次／小时；媒体没有额外小时配额，仅保留 10 秒发送间隔。另有入站队列限流、消息过期和 15 秒 Windows 心跳期限。
- 切换前要求针对该账号和版本的完整实测依据及在线心跳；先停 iLink 并等待其任务结束，再启用 Windows。掉线时不自动切回 iLink，避免双重回复。恢复与暂停不补发旧消息。

## Windows 侧

`little_blue/windows_bridge.py` 提供客户机协议与实验性 UIA 适配器。只绑定预先打开并核对的聊天窗口、进程 PID 和启动时间；窗口变化、账号退出、锁屏或身份不可读时拒绝发送。核心检查上报版本与绑定版本一致；生产适配器还需从客户端实际读取版本和账号身份，不能只信任本地配置。没有登录自动化，不保存微信密码，不注入 DLL 或解密数据库。

目前该适配器**不能用于正式收发**，因为发送者能力没有通过；不要手工修改 `identity_supported` 或伪造 evidence 绕过检查。`sent` 的未来 UIA 判定最多表示本地出现发送气泡，不能等同于对方已读或服务器送达；超时按 unknown 处理。

准备命令（状态查询不会启动微信自动发送）：

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-bridge.txt
.\bridge.ps1 status
.\bridge.ps1 pause
```

后续适配器通过验证后，配置保存于已忽略的 `data/windows-bridge.json`。`binding` 包含 account、owner_peer、version、routes 和实测 evidence；本机窗口信息另列 pid、process_started、main_window、windows、self_name。不要在该文件放密码，也不要用手机号代替已核对的联系人身份。

`bind → run（仅心跳，未启用不收发）→ 完整实测 → activate` 才是切换流程；`ilink` 是显式回退。Windows 客户端与桥接必须同时可用，只有 Docker 在线无法维持微信收发。

## 研究依据与下一步

- [pywechat / pyweixin](https://github.com/Hello-Mr-Crab/pywechat)：研究快照 `8589baa049bb91d3a500602c167f07b2f8397a13`，LGPL-2.1；未复制其模块。旧监听示例依赖正文里的发送者前缀，与本机实测不符。
- [wx4py 的监听实现](https://github.com/evanhuang365/wxauto/blob/main/src/features/messaging/listener.py)：其说明同样指出 Qt UIA 不能稳定暴露发送者和方向，因此不能直接满足本项目成员隔离要求。
- [wxauto 安装说明](https://docs.wxauto.org/docs/install.html)：免费版文档列出最高 4.1.8.107；不以此推断本机 4.1.15.56 兼容。Plus 的 [Message 文档](https://docs.wxauto.org/docs/class/Message.html) 提供 sender / sender_info，但本机尚未安装、购买或验证，不能把文档声明当作实测通过。

下一步应先验证能提供可靠发送者元数据的适配器，再进行你指定的私聊和群测试；原主人的私聊联系人仍需显式确认。当前保留 iLink 是验证未通过的结果，不是已经完成迁移。

## 替代桥接继续研究（2026-10-21）

本轮仅查阅官方项目文档、PyPI 发布信息和公开源码；未安装或激活新桥接，未操作微信、购买授权或修改运行通道。**在查阅范围内，没有找到明确验证 Windows 微信 4.1.15.56 的完整方案。** “支持 4.1”、支持 4.1.15.50 与支持本机精确版本不是同一结论。此前的失败结论仅针对当前适配器直接读取消息 UIA 元数据，不代表所有 UI 自动化路线都无解。

| 候选 | 身份／方向能力证据 | 当前版本证据与判断 |
| --- | --- | --- |
| wxautox4 / wxauto Plus | Message 文档有 sender、attr=self/friend、sender_info()、chat_info() 的 friend/group 区分 | 首选兼容性验证候选，需要授权；官网写 4.1，未列本机精确小版本，不能保证可用 |
| hermes-wxauto | 可逐条点头像读资料卡；文本方向使用左右绿色像素推断 | 适合借鉴原型；发送者解析默认关闭、取到的是名字，非稳定 ID；没有本机精确版本证据 |
| wxautopc | 读取头像资料卡的昵称／微信号，方向通过截图颜色推断 | 不直接复用其消息身份关联：正文作字典键、昵称作缓存键，相同正文或同名成员会碰撞 |
| miloira/wxhook 及其新版 pywechat | 旧开源 Event 有 fromUser、toUser、msgId，支持群接口；新版声称覆盖更多 4.x 版本 | 仓库简介列至 4.1.15.50；公开 wxhook 是 3.9.5.81 路线，新版需另行获取，不能将旧源码字段当作新版已验证能力 |
| WeChatFerry | WxMsg 明确包含 sender、roomid、is_self、is_group，并解析 XML 的 atuserlist | 接口设计适合本项目，但项目已归档，README 适配至 3.9.12.51；不适合当前客户端 |
| WeChatPYAPI | 文档包含私聊、群聊和群成员接口 | 专业版 README 列 4.1.12.26，社区版 3.6.0.18 且停止维护；没有当前小版本证据 |

### 为什么 wxautox4 仍需验证

[官网安装文档](https://docs.wxauto.org/docs/install.html) 列出 Plus 支持 Python 3.9–3.13，免费版仅 3.9–3.12、微信最高 4.1.8.107。Plus 的 [Message API](https://docs.wxauto.org/docs/class/Message.html) 与所需字段最接近，但 `sender_info()` 仅给出字典返回类型，不能据此断言每个非好友群成员都有稳定 wxid。

[PyPI wxautox4](https://pypi.org/project/wxautox4/) 当前发布文件为 41.2.1（2026-09-23），描述仍写 4.0.5 Beta，和官网 4.1 说明不一致。推断描述可能滞后，但不能把该推断当兼容证明。购买前应取得针对 4.1.15.56 的适配确认及试用／诊断办法；需要验证当前包是否确实返回方向、逐条身份、群类型和可区分的 @ 证据。未替用户联系维护者或购买。

### 开源头像资料卡路线的具体缺口

- [hermes-wxauto listener.py](https://github.com/doingSthing/hermes-wxauto/blob/main/src/my_wxauto/listener.py)：`_resolve_sender_from_profile_card` 点击头像，`_read_profile_card_name` 选择名字；`_infer_message_is_self_from_pixels` 只处理文本，利用绿色像素判定自身消息。窗口遮挡、主题、图片／表情以及同名用户需要补测，不能把 False 无条件解释为可信的入站消息。该仓库元数据最后推送 2026-05-15，未声明许可证，尚不适合直接复制代码。
- [wxautopc client.py](https://github.com/chungao435-eng/wxautopc/blob/main/wxauto_pc/core/client.py)：`_parse_message_item` 返回 `color_ambiguous`；`get_group_senders_info` 使用 `msg_item_map[item_text]`、`content_to_sender[content]` 和昵称缓存。依据源码推断，相同正文会覆盖前一项，同名人员会复用缓存。其 MIT 代码可作参考，但可靠实现必须绑定具体消息实例，再核对资料卡身份，不能按正文去重或按昵称合并。
- 两者可能短暂占用前台、点击头像和滚动消息；不能承诺 Windows 锁屏仍可收发。只读观察与未来自动发送需分开验收。

### Hook 方案的版本与分发边界

[wxhook 仓库](https://github.com/miloira/wxhook) 的简介和旧开源实现分属不同兼容范围。旧 [model.py](https://github.com/miloira/wxhook/blob/master/wxhook/model.py) 字段只说明旧实现，群消息 fromUser 还可能是群 ID，不能直接当作成员 ID。新版 pywechat 的实际包、授权、4.1.15.56 支持及回调样本都未核实；它与 Hello-Mr-Crab 的同名 pywechat 是不同项目。Hook 依赖客户端内部实现，版本相近不能推导二进制兼容，本轮没有执行注入或更改客户端。

[WeChatFerry 仓库](https://github.com/lich0821/WeChatFerry) 于 2026-07-10 归档，其 [WxMsg 源码](https://github.com/lich0821/WeChatFerry/blob/master/clients/python/wcferry/wxmsg.py) 适合借鉴事件字段设计，但不据此降级当前微信。[WeChatPYAPI](https://github.com/mrsanshui/WeChatPYAPI) 也没有本机版本证据。

### 建议下一步

1. 保留当前客户端，先确认 wxautox4 对 **4.1.15.56** 的精确支持和授权条件；有可用包后先只读采集指定私聊和测试群，不连接自动回复。
2. 若希望坚持开源，做独立头像资料卡诊断原型，记录身份来源与方向置信度；未知就丢弃。先验证能否区分同名群成员，再决定是否值得继续。
3. 使用固定消息实例 ID、会话类型、会话 ID、发送者 ID、self/incoming/unknown、@ 依据和客户端版本作为桥接事件。群 ID 与成员 ID 分开，显示名只用于展示。
4. 验证手机端自身发言、机器人自己发送、两个群成员同文／同名、真实 @／手打 @／引用 @、窗口切换、重复拉取、锁屏和断线恢复。身份不串、无自我回复、无历史补发后，才复用现有 `/bridge` 通道并切换 iLink。Docker 核心和原记忆库均无需重建。
