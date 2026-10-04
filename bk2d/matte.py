"""배경 제거: 크로마키(기본) 또는 rembg(선택)."""
import numpy as np


def parse_color(value: str) -> np.ndarray:
    value = value.lstrip("#")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def estimate_key_color(rgb: np.ndarray, margin: int = 8) -> np.ndarray:
    """프레임 가장자리 픽셀의 중앙값을 키 색으로 추정한다."""
    edges = np.concatenate([
        rgb[:margin].reshape(-1, 3), rgb[-margin:].reshape(-1, 3),
        rgb[:, :margin].reshape(-1, 3), rgb[:, -margin:].reshape(-1, 3),
    ])
    return np.median(edges, axis=0).astype(np.float32)


def _cbcr(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b
    return np.stack([cb, cr], axis=-1)


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


def key_saturation(key: np.ndarray) -> float:
    """키 색의 채도(CbCr 원점 거리). 무채색(검정/흰색/회색)과 얼마나 떨어져 있는지."""
    return float(np.linalg.norm(_cbcr(key.astype(np.float32))))


def auto_tolerance(key: np.ndarray) -> tuple[float, float]:
    """키 색 채도에 맞춘 허용치.

    허용치가 키 채도보다 크면 검은 머리/어두운 옷 같은 무채색까지 반투명해진다.
    선명한 #00FF00(채도 ~136)이면 (20, 45), 탁한 초록(채도 ~44)이면 (~12, ~26).
    """
    sat = key_saturation(key)
    high = min(45.0, 0.6 * sat)
    return min(20.0, 0.45 * high), high


def erase_regions(rgba: np.ndarray, rects) -> np.ndarray:
    """정규화 좌표 [x0, y0, x1, y1] 영역을 완전 투명으로 (워터마크 등)."""
    if not rects:
        return rgba
    h, w = rgba.shape[:2]
    out = rgba.copy()
    for x0, y0, x1, y1 in rects:
        out[int(y0 * h):int(round(y1 * h)), int(x0 * w):int(round(x1 * w)), 3] = 0
    return out


_SCIPY_WARNED = False


def _scipy_ndimage():
    """scipy 가 없으면 None. 선택 기능이므로 빌드 전체를 멈추지 않고 한 번만 경고한다."""
    global _SCIPY_WARNED
    try:
        from scipy import ndimage
        return ndimage
    except ImportError:
        if not _SCIPY_WARNED:
            print("  [경고] scipy 가 없어 despeckle(잡티 제거)를 건너뜁니다. 설치: pip install scipy")
            _SCIPY_WARNED = True
        return None


def despeckle(rgba: np.ndarray, min_area: int) -> np.ndarray:
    """몸통과 떨어진 min_area 픽셀 미만의 작은 덩어리를 투명 처리 (키잉 잡티, 압축 노이즈)."""
    if min_area <= 0:
        return rgba
    ndimage = _scipy_ndimage()
    if ndimage is None:
        return rgba
    mask = rgba[..., 3] > 0
    labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n <= 1:
        return rgba
    sizes = np.bincount(labels.ravel())
    small = sizes < min_area
    small[0] = False
    if not small.any():
        return rgba
    out = rgba.copy()
    out[small[labels], 3] = 0
    return out


def chroma_key(rgb: np.ndarray, key: np.ndarray, tol_low: float = 20.0,
               tol_high: float = 45.0, despill: bool = True) -> np.ndarray:
    """RGB(uint8) -> RGBA(uint8). 색차(CbCr) 거리로 소프트 매트를 만든다.

    tol_low 이하 거리는 완전 투명, tol_high 이상은 완전 불투명, 그 사이는 선형.
    """
    f = rgb.astype(np.float32)
    dist = np.linalg.norm(_cbcr(f) - _cbcr(key[None, None, :]), axis=-1)
    alpha = np.clip((dist - tol_low) / max(tol_high - tol_low, 1e-3), 0.0, 1.0)

    if despill:
        # 키 색의 지배 채널을 나머지 두 채널 최댓값으로 제한한다.
        # 캐릭터 내부 색이 망가지지 않도록 외곽(반투명 + 2px 확장) 영역에만 적용한다.
        ch = int(np.argmax(key))
        others = [c for c in range(3) if c != ch]
        edge = _dilate(alpha < 0.999, 2) & (alpha > 0.0)
        limit = np.maximum(f[..., others[0]], f[..., others[1]])
        spill = f[..., ch]
        f[..., ch] = np.where(edge, np.minimum(spill, limit), spill)

    rgba = np.dstack([f, alpha * 255.0])
    return np.clip(rgba + 0.5, 0, 255).astype(np.uint8)


def rembg_matte(rgb: np.ndarray, session=None) -> np.ndarray:
    try:
        from rembg import remove, new_session
    except ImportError as e:
        raise RuntimeError("rembg 모드는 `pip install rembg` 가 필요합니다.") from e
    from PIL import Image
    session = session or new_session("isnet-anime")
    return np.asarray(remove(Image.fromarray(rgb), session=session).convert("RGBA"))


def temporal_smooth_alpha(frames: list[np.ndarray], strength: float) -> list[np.ndarray]:
    """인접 프레임 알파를 섞어 외곽 떨림을 줄인다. 빠른 동작에선 잔상이 생기므로 약하게 쓴다."""
    if strength <= 0 or len(frames) < 3:
        return frames
    alphas = [f[..., 3].astype(np.float32) for f in frames]
    out = []
    n = len(frames)
    for i, f in enumerate(frames):
        a = alphas[i]
        nb = (alphas[(i - 1) % n] + alphas[(i + 1) % n]) / 2.0
        # 반투명 외곽에만 적용 (완전 불투명/투명 영역은 유지)
        edge = (a > 0) & (a < 255)
        mixed = np.where(edge, a * (1 - strength) + nb * strength, a)
        g = f.copy()
        g[..., 3] = np.clip(mixed + 0.5, 0, 255).astype(np.uint8)
        out.append(g)
    return out
