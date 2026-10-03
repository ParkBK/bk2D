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
