"""클립 간 스케일/피벗 정렬과 스프라이트시트 패킹."""
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

ALPHA_THRESHOLD = 16


def alpha_bbox(rgba: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(rgba[..., 3] > ALPHA_THRESHOLD)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


@dataclass
class Placement:
    """원본 프레임 좌표 -> 공통 셀 좌표 변환. 피벗은 기준 포즈의 발밑 중앙."""
    scale: float
    pivot_x: float  # 원본 프레임 좌표
    pivot_y: float


def placement_from_base(base: np.ndarray, target_height: int) -> Placement:
    box = alpha_bbox(base)
    if box is None:
        raise RuntimeError("첫 프레임에서 캐릭터를 찾지 못했습니다. 키 색/허용치를 확인하세요.")
    x0, y0, x1, y1 = box
    return Placement(scale=target_height / (y1 - y0), pivot_x=(x0 + x1) / 2.0, pivot_y=float(y1))


def _resize_rgba(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    # 프리멀티플라이드 상태로 리샘플링해야 외곽에 검은/초록 테두리가 생기지 않는다.
    return img.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def frame_extent(rgba: np.ndarray, p: Placement) -> tuple[float, float, float, float] | None:
    box = alpha_bbox(rgba)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    s = p.scale
    return ((x0 - p.pivot_x) * s, (y0 - p.pivot_y) * s, (x1 - p.pivot_x) * s, (y1 - p.pivot_y) * s)


@dataclass
class Cell:
    w: int
    h: int
    pivot_x: int  # 셀 좌상단 기준 픽셀
    pivot_y: int


def cell_from_extents(extents: list[tuple[float, float, float, float]], pad: int) -> Cell:
    minx = min(e[0] for e in extents)
    miny = min(e[1] for e in extents)
    maxx = max(e[2] for e in extents)
    maxy = max(e[3] for e in extents)
    # 피벗을 정수 픽셀에 두고 좌우 여백을 포함한 셀 크기를 정한다.
    left, top = math.ceil(-minx) + pad, math.ceil(-miny) + pad
    right, bottom = math.ceil(maxx) + pad, math.ceil(maxy) + pad
    return Cell(w=left + right, h=top + bottom, pivot_x=left, pivot_y=top)


def render_cell(rgba: np.ndarray, p: Placement, cell: Cell) -> Image.Image:
    out = Image.new("RGBA", (cell.w, cell.h), (0, 0, 0, 0))
    box = alpha_bbox(rgba)
    if box is None:
        return out
    x0, y0, x1, y1 = box
    s = p.scale
    crop = Image.fromarray(rgba).crop(box)
    size = (max(1, round((x1 - x0) * s)), max(1, round((y1 - y0) * s)))
    crop = _resize_rgba(crop, size)
    dx = round(cell.pivot_x + (x0 - p.pivot_x) * s)
    dy = round(cell.pivot_y + (y0 - p.pivot_y) * s)
    out.paste(crop, (dx, dy))  # 빈 캔버스이므로 마스크 없이 그대로 복사
    return out


def pack_grid(cells: list[Image.Image], spacing: int, max_size: int):
    """동일 크기 셀을 격자로 배치. 반환: (atlas, Unity 좌표(좌하단 원점) rect 목록)."""
    cw, ch = cells[0].size
    n = len(cells)
    max_cols = max(1, (max_size + spacing) // (cw + spacing))
    cols = min(n, max_cols, max(1, math.ceil(math.sqrt(n * ch / cw))))
    rows = math.ceil(n / cols)
    w = cols * cw + (cols - 1) * spacing
    h = rows * ch + (rows - 1) * spacing
    # 블록 압축(ETC2/DXT/ASTC 4x4) 호환을 위해 4의 배수로 맞춘다.
    w, h = (w + 3) // 4 * 4, (h + 3) // 4 * 4
    atlas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rects = []
    for i, img in enumerate(cells):
        cx, cy = i % cols, i // cols
        x, y = cx * (cw + spacing), cy * (ch + spacing)
        atlas.paste(img, (x, y))
        rects.append({"x": x, "y": h - y - ch, "w": cw, "h": ch})
    return atlas, rects
