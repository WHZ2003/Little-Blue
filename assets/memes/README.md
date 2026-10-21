# DeepSeek 表情素材

当前共 60 张：19 张社区素材和 41 张新生成表情，其中 51 张开放为自动候选。

`generated/901.webp` 至 `941.webp` 使用内置 `image_gen` 生成，参考社区 010、056 的蓝发鲸鱼女仆形象与表情画风，覆盖问候、确认、庆祝、亲近、惊讶、吐槽、困倦和吃饭等场景。生成提示词、场景和原始输出路径保存在 `generated/prompts.json`；原始 PNG 另保留在本机 `generated/originals/`。WebP 仅作格式编码，没有裁切或重绘。

新生成作品与下述社区原图分别记录来源，不将上游声明或项目代码许可证自动套用于新图。运行 `python tools/register_generated_memes.py` 可以从本机原始 PNG 重新编码并更新校验清单；该脚本不会调用生成服务。自动选择条件见 catalog 中每张图的 `auto_context`。

来源：[the-beating-light-of-the-nail/deepseek-chan-meme-pack](https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack/tree/c82a0fb1c8958c2f0d6ea7fa7c12128027cfd7c6)。本项目选用该版本 README 中介绍的 19 张 WebP 预览图，保持原图、文字和水印。

上游没有统一 MIT 图片许可证；README 说明允许个人聊天、非营利粉丝玩梗使用，角色与原始画作归相应原作者。此处的图片不继承本项目或微信接口代码的许可证。原作者署名、来历和权利人反馈入口见上游仓库及 https://aigengtu.com/ 。

`catalog.json` 保存固定版本、编号、标题、Git blob SHA-1 和字节数；`community/` 存放个人本机素材并被 Git 忽略。执行 `python tools/fetch_memes.py` 下载并校验，不执行上游代码。Docker 只读挂载此目录，图片不打进核心镜像。

新增素材需有明确使用来源，并在 catalog 中登记固定编号、校验值和可选的适用场景；不接受模型返回文件路径或图片 URL。更新目录后重启核心加载。当前只支持 WebP 表情，以图片消息发送，非微信官方贴纸商店包。
