# 本地混合记忆检索

SQLite `memories` 保留原文、类型和时间，FTS5 继续负责关键词检索。新增可重建的 `memory_vectors`（分块向量）与 `memory_vector_docs`（模型版本、原文摘要、完成标记），不替换用户身份卡、成长记录或跨渠道对话历史。

聊天和核心 `/rpc search` 使用同一混合检索：各路取候选，向量路用归一化余弦相似度，过滤低于 0.5 的结果；以 Reciprocal Rank Fusion（每路 `1 / (60 + rank)`）合并，按记忆 ID 去重。返回原始记忆以及 `retrieval.via` 和融合分数，向量命中另外包含 `similarity`。这些是相关性分数，不是事实可信度；模型仍须区分用户事实、助手说法、历史与当前状态。明确偏好、最近对话和活动上下文继续独立注入，不靠相似度决定核心身份。

## 模型与运行方式

采用 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，中文、512 维、MIT 模型；[FastEmbed 支持说明](https://qdrant.github.io/fastembed/examples/Supported_Models/)。通过 FastEmbed 0.9.0 和 CPU ONNX Runtime 本地推理，无需新的 API Key，不产生 DeepSeek 嵌入请求，原始记忆不发送到嵌入服务。正常聊天召回的记忆仍按照既有联网设置发送给 DeepSeek。

ONNX 文件使用 [Qdrant 发行版本](https://huggingface.co/Qdrant/bge-small-zh-v1.5/tree/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59)，固定 revision `46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`。下载后生成 SHA-256 清单，每次启动工作进程校验；模型清单摘要和分块版本共同构成索引版本，避免混用不兼容向量。

后台独立进程负责模型推理、余弦扫描和索引增量更新，不在 Qt 或生命循环里运行模型。启动最多等待 15 秒，索引批次最多 8 秒；聊天向量查询（包括等待索引批次）最多 1.5 秒，超时终止自己正在执行的推理并回退关键词。取消会终止相关工作进程。工作进程不继承 DeepSeek 密钥，离线模式加载本地文件，运行时不自动下载模型。

现阶段在 SQLite 中保存向量并精确扫描，不增加独立向量数据库服务；适合个人桌宠规模。记忆大量增长后扫描耗时会增加，届时可替换为近似索引；本实现的超时降级始终保留。默认 320 字符分块、40 字符重叠，每次处理 4 条记忆、每个推理 batch 最多 4 块，避免长记忆末尾完全丢失；模型仍有自身 token 截断限制。

旧记忆启动后自动分批补齐，新记忆正常情况下在约 10 秒内开始索引。写入失败不会标记完成，下次继续；原文变化时提交前再次核验，删除和编辑触发索引失效，并同步维护 FTS5。模型不可用、超时或依赖缺失时，文字聊天与关键词检索继续运行。

## 安装

```powershell
uv pip install --python .venv/Scripts/python.exe -e ".[memory]"
.venv/Scripts/python.exe tools/prepare_memory_model.py
docker compose build core
docker compose up -d --wait
```

首次准备模型需要联网下载约 95 MB 的 ONNX 文件及少量配置，存于 Git 忽略的 `models/bge-small-zh-v1.5/`。Compose 只读挂载 `models/`，核心仍为单容器；为容纳本地模型，内存上限从 512 MiB 调整为 1 GiB。新检出没有模型时可以照常启动并降级关键词检索。

源码本机模式可以通过 `LITTLE_BLUE_EMBEDDING_MODEL_DIR` 指定模型目录。当前旧 PyInstaller EXE 没有嵌入模型依赖，继续通过 Docker 核心使用混合检索；独立旧 EXE 不自动获得本功能。本机模式的聊天会用混合检索，本机控制面板的同步检索仍为关键词；连接核心的桌面检索使用混合检索。

## 查看与控制

无需打开面板，在绑定的微信会话发送：

| 命令 | 作用 |
| --- | --- |
| `/memory` | 查看开关、模型就绪状态、已索引／总记忆数 |
| `/memory off` | 关闭向量索引与召回，保留原文及已有向量 |
| `/memory on` | 开启并继续补齐索引 |
| `/memory rebuild` | 清空派生索引并重新构建，原始记忆不删除；关闭状态下保持关闭 |

开关持久化。核心 `/state` 包含 `memory_index` 状态；已认证且持有桌面会话的 `/rpc` 可使用 `op=memory_index` 与 `memory_action=status/on/off/rebuild`。这些操作不是 LLM 工具，模型不能自行清空或关闭索引。日志只记录错误类型和控制动作，不记录检索正文。

## 验证

`tests/test_retrieval.py` 覆盖旧记忆补齐、重启、模型版本隔离、分块尾部召回、融合去重、删除与编辑、写入竞态、损坏向量、缺失模型降级及取消。核心和微信测试覆盖控制命令与会话权限。

`python tools/check_memory_model.py` 以临时合成数据库实测模型：用「我喜欢喝哪种饮料？」召回蓝莓茶，用「昨天功课是不是很多？」召回数学作业；两者关键词检索均为空。还检查后台索引时事件循环继续运行、关闭后降级及重建不删除原文。此检查不调用 DeepSeek、不发送微信、不写入真实用户记忆。
