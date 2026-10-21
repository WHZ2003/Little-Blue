# 微信表情包

保留用户指定的 [DeepSeek 社区表情包](https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack/tree/c82a0fb1c8958c2f0d6ea7fa7c12128027cfd7c6) 中 19 张精选图，新增 41 张蓝发鲸鱼女仆表情，总计 60 张，其中 51 张可自动选择。运行时可用数量以 `/memes` 为准；[本机图鉴](../assets/memes/gallery.html) 可浏览全套；新增图片、分组和生成提示词见 `assets/memes/generated/prompts.json`。桌宠原有逐帧动画保持原样。

## 使用

在已绑定的微信会话发送：

| 命令 | 作用 |
| --- | --- |
| `/memes` | 列出本机可用编号、标题与自动配图开关 |
| `/meme 087` | 点发「捧腹大笑」 |
| `/meme 123` | 点发「我能去你家吃饭吗，就一碗」 |
| `/memes off` | 关闭自动配图，仍可明确点发 |
| `/memes on` | 开启自动配图 |
| `/cancel` | 取消当前微信任务、图片上传及排队消息 |

自动配图默认开启。联网聊天时模型在同一次回复中按情绪选择合适编号，不额外调用模型。候选覆盖得意、庆祝、无语、轻松吐槽、招呼、困倦、吃饭和关心等情绪；有明确情绪时可以自然配图，没有合适内容不硬凑。夸张社区梗图有更窄的适用条件，部分仅支持明确点发。图片台词不是身份设定或实际操作结果，不写回成长层。离线时仍可通过微信命令点发已有图片；微信网络本身必须可用。

自动图片最多每小时 **8 张**、间隔至少 **1 分钟**、不连续重复同一张；全部图片合计每小时最多 10 张、间隔至少 10 秒。上限不是目标频率。发送前占用额度，失败或取消也不返还，避免断线重试刷屏。限频与开关重启保留；关闭再开启也不补发此前模型选择的图片。自动图片遵守勿扰和专注状态；明确点发视为用户本次请求。决定保持沉默时不以表情代替沉默。

普通回复先发文字，再尝试图片。图片失败不丢弃文字，写入脱敏审计，结果不明不自动重试；手动发送失败会有文字提示。微信接收图片／语音识别未在本次实现，桌面控制台仍显示文字回复。本功能发送的是图片消息，能否在手机端收藏由微信客户端决定。

## 安装与来源

当前工作区已下载素材。新检出工作区运行：

```powershell
.\.venv\Scripts\python.exe tools\fetch_memes.py
.\start-core.ps1
```

素材固定到上游提交 `c82a0fb1c8958c2f0d6ea7fa7c12128027cfd7c6`。下载来源固定为 GitHub raw，限制大小并核对 Git blob 校验值和 WebP 签名；运行时同样检查。缺少素材仅停用配图，不影响生命模拟和文字聊天。原图放在 Git 忽略的 `assets/memes/community/`，通过只读卷进入 Docker，未放进核心镜像。

新图由内置 image_gen 逐张生成，提示词、参考图与分组记录在 `generated/prompts.json`，原始 PNG 保存在 `generated/originals/`（Git 忽略），供发送的 WebP 保存在 `generated/`。仅做 WebP 格式编码，不裁剪、拼贴或改绘生成结果。生成素材通过 `source=generated` 区分，沿用固定 ID、字节数及内容摘要校验；下载脚本会跳过它们，不向上游请求不存在的文件。

图片使用说明及原作者权利信息见 [素材来源](../assets/memes/README.md)，不要将社区图片误标为 MIT。正式对外分发前应单独确认图片的分发条件。

## 微信协议与边界

参考腾讯官方 [上传流程](https://github.com/Tencent/openclaw-weixin/blob/874637a0dbc24050f72d1385add4fc6170a2d726/src/cdn/upload.ts) 和 [图片消息结构](https://github.com/Tencent/openclaw-weixin/blob/874637a0dbc24050f72d1385add4fc6170a2d726/src/messaging/send.ts)，检查版本 `874637a0dbc24050f72d1385add4fc6170a2d726`，MIT 许可附于 `little_blue/openclaw-weixin-LICENSE.txt`。

本实现使用 AES-128-ECB + PKCS7 满足 iLink 协议，先获取上传参数，再向固定微信 CDN 上传密文，最后发送 type=2 图片消息。消息中的密钥按官方格式是十六进制文本的 base64；不混淆为原始密钥字节的 base64。上传目标只允许 `https://novac2c.cdn.weixin.qq.com/c2c/upload`，禁止重定向，CDN 请求不附带 API Bearer Token。

模型只能选择本地白名单编号，不提供任意 URL、文件上传或接收人参数。接收人仍固定为扫码绑定账号，每次必须有会话上下文。图片上传总超时 80 秒，可取消；日志不记录图片加密参数、用户账号、密钥或正文。表情命令由通道直接解释，不写入成长学习来源。
