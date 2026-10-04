"""프레임 간 흔들림 보정: 기준 영역(발/전신)을 첫 프레임에 맞춰 평행이동.

AI 영상은 카메라가 고정이어도 캐릭터 전체가 몇 px 씩 떠다니는 경우가 많다.
발 영역을 위상 상관(phase correlation)으로 첫 프레임과 비교해 이동량을 구하고 되돌린다.
프레임마다 디테일이 다시 그려지는 "꿈틀거림" 은 이동이 아니므로 보정되지 않는다.
"""
import numpy as np

from .layout import alpha_bbox

REGIONS = {
    "feet": 0.2,   # 기준 포즈 bbox 하단 20%
    "body": 1.0,   # 전신
}


def _signal(rgba: np.ndarray, box) -> np.ndarray:
    x0, y0, x1, y1 = box
    f = rgba[y0:y1, x0:x1].astype(np.float32)
    lum = (0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]) / 255.0
    a = f[..., 3] / 255.0
    sig = lum * a + a  # 실루엣 + 내부 명암
    sig -= sig.mean()
    win = np.outer(np.hanning(sig.shape[0]), np.hanning(sig.shape[1]))
    return sig * win


def _phase_shift(ref: np.ndarray, cur: np.ndarray) -> tuple[int, int, float]:
    """cur 가 ref 대비 (dx, dy) 만큼 이동했는지와 상관 피크 강도."""
    fr, fc = np.fft.fft2(ref), np.fft.fft2(cur)
    r = fc * np.conj(fr)
    r /= np.abs(r) + 1e-9
    corr = np.fft.ifft2(r).real
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    h, w = corr.shape
    dy = iy - h if iy > h // 2 else iy
    dx = ix - w if ix > w // 2 else ix
    return int(dx), int(dy), float(corr.max())


def _shift(rgba: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """빈 곳은 투명으로 채우는 평행이동 (np.roll 처럼 반대편으로 넘어가지 않음)."""
    if dx == 0 and dy == 0:
        return rgba
    h, w = rgba.shape[:2]
    out = np.zeros_like(rgba)
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = rgba[ys, xs]
    return out


def stabilize(frames: list[np.ndarray], region: str = "feet",
              max_ratio: float = 0.08) -> tuple[list[np.ndarray], list[tuple[int, int]]]:
    """첫 프레임 기준으로 각 프레임의 기준 영역 이동을 되돌린다. 반환: (보정 프레임, 프레임별 측정 이동량)."""
    if region not in REGIONS or len(frames) < 2:
        return frames, [(0, 0)] * len(frames)
    box = alpha_bbox(frames[0])
    if box is None:
        return frames, [(0, 0)] * len(frames)
    x0, y0, x1, y1 = box
    h, w = frames[0].shape[:2]
    bh = y1 - y0
    ry0 = y1 - int(bh * REGIONS[region])
    m = max(8, int(bh * 0.05))  # 이동해도 영역 안에 머물도록 여유
    roi = (max(0, x0 - m), max(0, ry0 - m), min(w, x1 + m), min(h, y1 + m))
    limit = max(4, int(bh * max_ratio))

    ref = _signal(frames[0], roi)
    out, shifts = [frames[0]], [(0, 0)]
    for f in frames[1:]:
        dx, dy, peak = _phase_shift(ref, _signal(f, roi))
        if abs(dx) > limit or abs(dy) > limit or peak < 0.05:
            dx, dy = 0, 0  # 신뢰할 수 없는 측정은 건드리지 않음
        shifts.append((dx, dy))
        out.append(_shift(f, -dx, -dy))
    return out, shifts
