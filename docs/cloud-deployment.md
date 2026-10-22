# 大肥鱼：2 核 2 GiB 云端部署

## 配置

目标环境为 Ubuntu 24.04 x86_64、2 核 2 GiB、40 GB 磁盘，安装目录为 `/opt/dafeiyu`。
核心与 Hermes 分别限制 1 GiB 和 512 MiB 内存，可配置 1 GiB swap。
API 仅绑定 `127.0.0.1:8765`，通过 SSH 隧道访问，无需开放公网 API 端口。

本地 `.secrets/cloud_ssh_target` 保存一行自己的 `用户名@服务器地址`。
准备 `.secrets/aliyun_dafeiyu_ed25519` 私钥、经核对的 `.secrets/aliyun_known_hosts`，
以及从云端安全取得的 `.secrets/cloud_core_token`。这些文件全部保持私有。

- `start-cloud.cmd` 或 `start-cloud.ps1` 建立本地 18765 端口隧道并打开桌宠。
- `backup-cloud.ps1` 下载并校验可迁移备份，写入忽略目录 `backups/`。
- 关闭本地桌宠不停止云端核心；两台电脑轮流连接同一个云端核心即可共享记忆。
- 已交出的旧核心不能直接启动；迁回本地须走[完整交接流程](handoff.md)。

当前发布打包入口为 `tools/package_bili_release.py`，包含 Hermes、B站及对话改进。
具体版本由生成的 Compose 文件指定；基础包仅用于首次部署，不能覆盖现有升级配置。
模型凭据由核心从私有 `.env` 读取，Hermes 不接触 DeepSeek API key。
使用方式见 [Hermes](hermes-harness.md) 与 [B站浏览](bilibili.md)。

## 部署包

以下是原始核心的首次部署流程。当前 Hermes 部署应使用 `tools/package_hermes_release.py`
生成的配置及两个匹配版本的镜像，不能用原始包覆盖当前 `compose.yaml`。

`python tools/package_cloud.py` 生成 `backups/DaFeiYu-cloud-deploy.tar.gz`。
只含源码、公共模型和表情素材，不包含 API key、聊天、登录凭据或机器私钥。
将包解压到云服务器 `/opt/dafeiyu`。在本地准备镜像：

```powershell
docker build -t little-blue-core:local .
docker save -o backups/core-image.tar little-blue-core:local
```

通过已验证主机指纹的 SSH/SCP 上传部署包与镜像。当前已另外准备压缩镜像
`backups/core-image.tar.gz`，Docker 可以直接导入该压缩格式。再在云端执行：

```bash
sudo docker load -i /path/core-image.tar  # 安装 Docker 后执行
sudo bash /opt/dafeiyu/tools/setup_cloud.sh
sudo bash /opt/dafeiyu/tools/start_cloud.sh
```

首次运行时先执行 setup_cloud.sh 安装 Docker；若镜像还未导入，脚本会保留准备结果，
提示导入镜像后再执行 start_cloud.sh。从本地导入镜像避免依赖云端拉取 Docker Hub。
API key 通过 SSH 单独放到 `/opt/dafeiyu/.env`，所有者为 dafeiyu，权限 600。
启动脚本不会启用联网认知或扫码绑定微信。

核心限制 1 GiB 物理内存，允许最多 0.5 GiB 额外交换空间；浏览器默认不启动。
无现有交换空间时创建 1 GiB swap。浏览器如后续开启，限制 512 MiB 内存，须先实测峰值。
只开放管理所需的 SSH，API 8765/浏览器 8766 仅绑定服务器回环地址。

服务用户 UID 10001 与容器一致，保证私有交接文件可读且不向其他普通用户开放。
该服务用户加入 docker 组，具有 Docker 管理权限；它不是可供不可信程序使用的沙箱账号。

## 迁移现有记忆

先验证云端空白核心健康，再按以下次序迁移。云端所有交接操作以 dafeiyu 身份运行：

```bash
cd /opt/dafeiyu
sudo -H -u dafeiyu .venv/bin/python -m little_blue.handoff prepare --request backups/cloud-request.json
```

将云端请求下载至当前活跃电脑，执行 handoff.ps1 Send 后上传加密交接包；
旧端已停止且包完整后才在云端执行：

```bash
sudo -H -u dafeiyu .venv/bin/python -m little_blue.handoff receive --package backups/to-cloud.lbhand --receipt backups/cloud-receipt.json
```

回执下载后，在旧端执行 handoff.ps1 Finish。保留源端卷与交接状态。
如目前最新记忆在另一台电脑，必须从那台活跃电脑交出，不用旧快照覆盖。
真实 iLink 凭据迁移需实测，切换期间短暂停聊。

## 本地桌宠连接

将云端 core_token 安全下载至本地 `.secrets/cloud_core_token`，不覆盖本地原令牌。
用已验证的 SSH 连接建立隧道（替换用户名和地址）：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18765:127.0.0.1:8765 用户名@云服务器
.\.venv\Scripts\python.exe -m little_blue --core-url http://127.0.0.1:18765 --core-token-file .secrets/cloud_core_token
```

两台电脑轮流连接相同云端核心。当前仍限制同时只有一个桌面客户端。
云端核心是唯一活跃微信端；本地不要启动已交出的旧核心。
后续备份使用 SQLite 一致性导出，另存到本地；不能把同一云盘中的副本当作独立灾备。
