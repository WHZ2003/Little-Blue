"""Build a source-only update; no account, environment, memory or private keys."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    files = set()
    for folder in ('little_blue', 'tools', 'tests', 'docs'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py', '.ps1', '.md', '.json', '.txt'):
                files.add(path.relative_to(ROOT).as_posix())
    files.update(('handoff.ps1', 'memory.ps1', 'setup.ps1', 'start.ps1', 'start-core.ps1', 'wechat.ps1',
                  'Dockerfile', 'pyproject.toml', 'requirements-lock.txt', 'requirements-core.txt',
                  'requirements-bridge.txt', '.dockerignore', '.gitignore'))
    destination = ROOT / 'backups/DaFeiYu-handoff-update-2026-10-21.zip'
    destination.parent.mkdir(exist_ok=True)
    manifest = {'format': 'little-blue-handoff-update-1', 'files': []}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            raw = (ROOT / name).read_bytes()
            manifest['files'].append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest()})
            archive.writestr('payload/' + name, raw)
        archive.writestr('manifest.json', json.dumps(manifest, indent=2))
        archive.write(ROOT / 'tools/install_handoff_update.ps1', 'Install.ps1')
        archive.writestr('README.txt', '''大肥鱼双机交接更新包（源码，不含私人数据）

另一台电脑先有 Little-Blue 项目和 Docker Desktop。
1. 将此 ZIP 解压到临时文件夹；不要解压覆盖项目。
2. 等待现有回复完成，关闭桌宠，在目标项目中执行 docker compose stop core。
3. 在解压目录执行：
   .\\Install.ps1 -TargetFolder D:\\DaFeiYu
   （请替换成这台电脑的实际项目路径。）
4. 在目标项目执行 .\\setup.ps1 -SkipDownloads -SkipTests
   若首次安装，应执行完整 .\\setup.ps1 下载模型和素材。
5. 按项目 docs/handoff.md 完成 Prepare → Send → Receive → Finish。

安装会先校验所有文件并备份被覆盖的源码到项目 backups/。
不会复制或覆盖 .env、.secrets、.handoff、compose.yaml、compose.override.yaml、数据库和模型。
请在第二台电脑自己的 .env 配置 API key；不要复制第一台的 .handoff 私钥。
已有自定义源码会被本版本覆盖，但备份会保留。更新不自动登录微信、不自动进行实际交接。
只有两台都升级后才开始交接；已交出端不要通过恢复旧源码绕过锁。
''')
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert not any(part in ('.env', '.secrets', '.handoff', 'data') for name in archive.namelist() for part in Path(name).parts)
    print(json.dumps({'package': str(destination), 'files': len(files), 'bytes': destination.stat().st_size,
                      'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
