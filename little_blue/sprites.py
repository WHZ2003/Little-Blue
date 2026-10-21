import json
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap
from .models import Action


@dataclass
class Clip:
    frames: list
    fps: float
    loop: bool = True


class SpriteBank:
    def __init__(self, manifest, size=180):
        manifest = Path(manifest).resolve()
        if manifest.stat().st_size > 100000:
            raise ValueError("角色清单过大。")
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        self.name = data.get("name", "DeepSeek")[:100]
        root = manifest.parent
        decoded_pixels = 0

        def load(relative):
            nonlocal decoded_pixels
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError("角色资源必须位于清单目录内。")
            reader = QImageReader(str(path))
            dims = reader.size()
            if dims.width() <= 0 or dims.height() <= 0 or dims.width() * dims.height() > 32000000:
                raise ValueError("图片尺寸无效或超过 3200 万像素。")
            decoded_pixels += dims.width() * dims.height()
            if decoded_pixels > 64000000:
                raise ValueError("角色总图片尺寸超过 6400 万像素。")
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                raise ValueError(f"无法读取图片：{path.name}")
            return pixmap

        sheet = load(data["sheet"]) if data.get("sheet") else None
        base_sheet = sheet
        columns, rows = int(data.get("columns", 1)), int(data.get("rows", 1))
        if not (1 <= columns <= 32 and 1 <= rows <= 32):
            raise ValueError("图集行列数无效。")
        row_edges = data.get("row_edges")
        if row_edges is not None:
            if (sheet is None or len(row_edges) != rows + 1 or
                    any(type(x) is not int for x in row_edges) or
                    row_edges[0] < 0 or row_edges[-1] > sheet.height() or
                    any(a >= b for a, b in zip(row_edges, row_edges[1:]))):
                raise ValueError("图集行边界无效。")
        self.clips = {}
        total = 0
        for action, config in data["animations"].items():
            Action(action)
            # A replacement clip can use its own atlas without regenerating
            # the user's approved idle, expressions and other animations.
            sheet = load(config['sheet']) if config.get('sheet') else base_sheet
            edges = None if config.get('sheet') else row_edges
            fps = float(config.get("fps", 6))
            if not 1 <= fps <= 60:
                raise ValueError("帧率必须在 1–60 之间。")
            if "frames" in config:
                if not 1 <= len(config["frames"]) <= 64:
                    raise ValueError("每组动画需 1–64 帧。")
                frames = [load(path) for path in config["frames"]]
            elif "rects" in config:
                rects = config["rects"]
                if sheet is None or not isinstance(rects, list) or not 1 <= len(rects) <= 64:
                    raise ValueError("矩形切片需要图集及 1–64 个矩形。")
                frames = []
                for rect in rects:
                    if (not isinstance(rect, list) or len(rect) != 4 or
                            any(type(value) is not int for value in rect)):
                        raise ValueError("切片矩形必须是整数 [x, y, width, height]。")
                    x, y, width, height = rect
                    if (x < 0 or y < 0 or width <= 0 or height <= 0 or
                            x + width > sheet.width() or y + height > sheet.height()):
                        raise ValueError("切片矩形超出图集边界。")
                    frames.append(sheet.copy(x, y, width, height))
            elif sheet is not None:
                row = int(config["row"])
                if not 0 <= row < rows:
                    raise ValueError("动画行号越界。")
                w, h = sheet.width() // columns, sheet.height() // rows
                y = edges[row] if edges else row * h
                height = edges[row + 1] - y if edges else h
                frames = [sheet.copy(col * w, y, w, height) for col in range(columns)]
            else:
                raise ValueError("动画缺少 frames 或 sheet。")
            total += len(frames)
            if total > 256:
                raise ValueError("最多载入 256 帧。")
            frames = [frame.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation) for frame in frames]
            self.clips[action] = Clip(frames, fps, bool(config.get("loop", True)))
        if "idle" not in self.clips:
            raise ValueError("角色必须有 idle 动画。")

    def frame(self, action, elapsed):
        clip = self.clips.get(str(action), self.clips["idle"])
        index = int(max(0, elapsed) * clip.fps)
        index = index % len(clip.frames) if clip.loop else min(index, len(clip.frames) - 1)
        return clip.frames[index]
