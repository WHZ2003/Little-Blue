# 大肥鱼 · 流萤音色

本机已完成 Seed-VC 微调，并接入 Coopanion 的「大肥鱼功能复合包」0.3.8。目录为 `D:\DaFeiYu\firefly-voice`。只改变朗读声音，人设、记忆、聊天模型和原来的 TTS 参数保持不变。

## 使用

正常打开大肥鱼即可。复合包的「语音朗读」配置中已开启「流萤音色（本地 Seed-VC）」。原 Edge TTS 先生成语音，再由本机 GPU 转换音色，最后使用原播放器播放。此功能不额外调用 DeepSeek。

服务地址为 `http://127.0.0.1:18892`，仅监听本机。服务未运行时，第一条朗读会自动运行 `start-service.ps1`，本次冷启动测试约 14 秒；启动后的三句测试转换各约 0.7～0.9 秒。无法转换时自动退回原 Edge 音色，关闭该配置也能立即恢复原声音。

这是本地桌面朗读功能；没有修改云端微信的语音发送行为。电脑关闭后本地音色服务也会停止。现有 Edge TTS 仍需要联网，Seed-VC 推理使用已下载的模型。

试听：`samples/fatfish-integrated.wav` 为实际安装的复合包生成的结果；`samples/trained-1.wav`～`trained-3.wav` 为训练后样本；同目录 `pretrained-*.wav` 是未微调模型的对照。

## 训练记录

- 上游：[Plachtaa/seed-vc](https://github.com/Plachtaa/seed-vc)，固定提交 `51383efd921027683c89e5348211d93ff12ac2a8`。
- 数据：[流萤语音资料库](https://lovefirefly.moe/repo/)，说明见[作者语音页面](https://lovefirefly.moe/post_voicegen/)。使用作者公开的 `GPT-SoVITs-dataset-Firefly.zip` 和参考音频。
- 遵循作者的精选标注，排除短促语气、过短/损坏音频和男女主角分支重复，按源文件划分训练与保留集。
- 训练集 949 段，86.10 分钟；保留集 107 段，10.07 分钟。保留集没有参与训练，但本轮未对其做完整评测。
- 单人声、22,050 Hz、批量 1、1,000 步，在 RTX 4070 上完成；并非仅加载预训练模型。
- 最终权重：`models/firefly-zh-v1/ft_model.pth`；配置：同目录 `config.yml`；日志：`logs/train-v1.log`。
- 权重 SHA256：`E3E041B3F7F055F011393EF2BFC16F19D34C68A73EE5508AD58D02BB2F7DC996`。

三句合成测试均通过本地 Whisper 识别核对（简繁转换、去标点后字符错误率为 0），无静音或削波。声纹相似度均值约 0.916，对照约 0.913，差异较小；这些自动指标不能替代人工试听，也不代表所有句子都能复现角色表现。实际安装插件的端到端测试、服务停止后的自动恢复测试、取消朗读和失败回退测试均通过。为避免打扰，端到端测试将播放器入口的音频保存为文件，没有从扬声器播放。

原始数据的版权与使用条件见 `data/original/GPT-SoVITs-dataset-Firefly/README.md`（如作者目录文件名有变化，以原包为准）；音频来自游戏，作者资料标注非商业使用，商业使用需另行取得许可。原音频、训练权重、运行缓存均已加入 Git 忽略规则。

## 维护

手动启动（PowerShell）：

```powershell
& D:\DaFeiYu\firefly-voice\start-service.ps1 -WaitReady
Invoke-RestMethod http://127.0.0.1:18892/health
```

本机隔离环境使用 Python 3.10.22、PyTorch 2.4.1 + CUDA 12.4；完整依赖见 `requirements-lock.txt`。重新安装时使用 PyTorch 官方 CUDA 索引解析带 `+cu124` 的版本；不要直接覆盖安装上游未锁定的 requirements。复现训练需先准备原始数据，运行 `prepare_data.py`，然后在 `seed-vc` 目录执行：

```powershell
..\.venv\Scripts\python.exe -u train.py --config configs/presets/config_dit_mel_seed_uvit_whisper_small_wavenet.yml --dataset-dir ../data/train-v1 --run-name firefly-zh-v1-new --batch-size 1 --max-steps 1000 --save-every 250 --num-workers 0
```

新训练输出不会自动覆盖在用模型。先比较试听，再替换 `models/firefly-zh-v1` 的模型与匹配配置，并重新启动音色服务。

复合包补丁：`../tools/patch_coopanion_firefly_voice.py`，只适配 0.3.8，重复运行不会重复修改。原文件备份位于 `D:\DaFeiYu\backups\firefly-voice-1792740273757777300`。插件升级可能覆盖补丁；升级后需检查版本兼容性。原始文件的许可证仍适用。

在 `D:\DaFeiYu` 执行测试：

```powershell
node --import ./.research/Coopanion/resources/app/node_modules/tsx/dist/loader.mjs --test firefly-voice/fatfish-voice.test.ts
node --import ./.research/Coopanion/resources/app/node_modules/tsx/dist/loader.mjs firefly-voice/verify_integration.ts
```

第二条会请求 Edge TTS、运行真实音色转换并保存样本，保持静音。日志位于 `logs/integration-check.json`。完整自动对照评测脚本为 `evaluate.py`，需要 `make_samples.ts` 预先生成的源音频及本地 Whisper 模型。
