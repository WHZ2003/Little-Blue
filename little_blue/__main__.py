import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="Little Blue desktop companion")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--panel", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--core-url", help="Use a local container core, e.g. http://127.0.0.1:8765")
    parser.add_argument("--core-token-file", type=Path, default=Path(".secrets/core_token"))
    args = parser.parse_args()
    data_dir = args.data_dir or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LittleBlue"
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(data_dir / "little-blue.log", maxBytes=1000000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # HTTP libraries must not log URLs/headers at DEBUG level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .storage import Store
    from .ui import Controller
    app = QApplication(sys.argv[:1])
    app.setApplicationName("DeepSeek")
    app.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(data_dir / "app.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(None, "DeepSeek", "同一个数据目录的DeepSeek已经在运行。请从系统托盘打开控制台。")
        return 1
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    if args.core_url:
        from .remote import RemoteController, connection, initial_state
        try:
            url, headers = connection(args.core_url, args.core_token_file)
            state = initial_state(url, headers)
        except Exception:
            QMessageBox.warning(None, "核心连接失败", "请先启动核心容器，并检查本机地址及认证令牌。")
            lock.unlock()
            return 1
        controller = RemoteController(app, state, url, headers, root / "assets", data_dir)
    else:
        store = Store(data_dir / "memory.sqlite3")
        controller = Controller(app, store, root / "assets", data_dir)
    if args.panel:
        controller.show_panel()
    if args.smoke_seconds:
        QTimer.singleShot(round(args.smoke_seconds * 1000), controller.quit)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
