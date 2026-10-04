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


def _misfit(ref: np.ndarray, cur: np.ndarray, box, dx: int, dy: int) -> float:
    """cur 를 (dx, dy) 만큼 되돌렸을 때 기준 영역 실루엣이 ref 와 어긋나는 정도 (0 = 일치)."""
    x0, y0, x1, y1 = box
    a = ref[y0:y1, x0:x1, 3].astype(np.float32)
    b = _shift(cur, -dx, -dy)[y0:y1, x0:x1, 3].astype(np.float32)
    return float(np.abs(a - b).mean())


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
    # 교차 확인용 머리 쪽 영역 (bbox 상단 25%)
    head = (roi[0], max(0, y0 - m), roi[2], min(h, y0 + int(bh * 0.25) + m))

    # 캐릭터가 화면 끝에서 잘려 있으면 그 축으로는 옮기지 않는다.
    # 옮기면 잘린 선(예: 발목에서 끊긴 다리 끝)이 프레임마다 위아래로 움직여 "튕김"으로 보인다.
    lock_y = y0 <= 1 or y1 >= h - 1
    lock_x = x0 <= 1 or x1 >= w - 1

    ref = _signal(frames[0], roi)
    ref_head = _signal(frames[0], head)
    out, shifts = [frames[0]], [(0, 0)]
    for f in frames[1:]:
        dx, dy, peak = _phase_shift(ref, _signal(f, roi))
        if abs(dx) > limit or abs(dy) > limit or peak < 0.05:
            dx, dy = 0, 0  # 신뢰할 수 없는 측정은 건드리지 않음
        # 몸 전체가 옆으로 밀린 것이면 머리 쪽도 같은 방향으로 비슷하게 움직인다. 발 쪽만 움직였다면
        # 치마/머리카락 같은 부분 동작이므로, 그걸 되돌리면 멀쩡한 몸 전체가 좌우로 흔들린다.
        # 세로는 숨쉬기로 머리만 오르내리는 게 자연스러워 머리를 기준으로 삼지 않는다.
        if dx:
            hx = _phase_shift(ref_head, _signal(f, head))[0]
            if hx * dx <= 0 or abs(dx - hx) > max(2, abs(dx) // 2):
                dx = 0
        if lock_x:
            dx = 0
        if lock_y:
            dy = 0
        # 치마/머리카락처럼 기준 영역 안에서 흔들리는 부분이 크면 위상 상관이 가짜 피크를 잡는다
        # (실제 발은 그대로인데 -40px 같은 이동이 나와 그 프레임만 튕김).
        # 되돌렸을 때 실루엣이 첫 프레임과 더 잘 맞지 않으면 측정 오류로 보고 버린다.
        if (dx or dy) and _misfit(frames[0], f, roi, dx, dy) >= _misfit(frames[0], f, roi, 0, 0):
            dx, dy = 0, 0
        shifts.append((dx, dy))
        out.append(_shift(f, -dx, -dy))
    return out, shifts


def locked_axes(frame) -> list[str]:
    box = alpha_bbox(frame)
    if box is None:
        return []
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = box
    axes = []
    if y0 <= 1 or y1 >= h - 1:
        axes.append("세로")
    if x0 <= 1 or x1 >= w - 1:
        axes.append("가로")
    return axes
