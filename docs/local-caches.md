# 本地缓存位置

大肥鱼的启动、安装脚本加载 `tools/cache-env.ps1`，将 pip、uv、npm、XDG、Hugging Face、Torch 和 Numba 缓存放到项目根目录的 `.cache/`。本机为 `D:\DaFeiYu\.cache`。这些变量仅供启动进程及其子进程使用，不改聊天数据目录或系统 TEMP。

本机另将当前 Windows 用户的 `PIP_CACHE_DIR` 和 `UV_CACHE_DIR` 持久设置到相应 D 盘目录，新开的终端会自动继承。已经打开的终端可以先执行：

```powershell
. D:\DaFeiYu\tools\cache-env.ps1
```

以后安装依赖时先加载此脚本；正常用 `start-coopanion.ps1`、`start.ps1`、`setup.ps1` 或 `firefly-voice/start-service.ps1` 启动会自动加载。正在运行的进程无需重启，新的设置在下次启动时继承。直接启动语音 `service.py` 也会采用 D 盘缓存。

Seed-VC 已下载的模型继续使用 `firefly-voice/seed-vc/checkpoints`，训练权重继续使用 `firefly-voice/models`。Coopanion 的浏览器缓存和登录资料仍在 `.research/Coopanion/data`。现有 Python 环境、浏览器程序、模型、记忆和聊天记录不属于本次清理对象；其他项目的 Hugging Face 和 Whisper 模型缓存也保留。

本次仅通过 pip/uv 自带的清理命令清空 C 盘 pip/uv 缓存，不手动删除运行环境，不使用强制忽略缓存占用检查。Windows 用户缓存变量原值备份在 `backups/cache-settings-*`。删除下载缓存后，未来重装依赖可能需要重新下载。
