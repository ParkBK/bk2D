"""루프 구간 탐지, 프레임 솎기, 포즈 차이 측정."""
import numpy as np


def frame_diff(a: np.ndarray, b: np.ndarray) -> float:
    """두 썸네일의 평균 절대 차이 (0~1).

    RGBA 썸네일이면 둘 중 하나라도 캐릭터가 있는 픽셀만 비교한다.
    (빈 배경이 대부분인 셀에서 차이가 희석되는 것을 막기 위함)
    """
    if a.shape[-1] == 4:
        mask = np.maximum(a[..., 3], b[..., 3]) > 0.1
        if not mask.any():
            return 0.0
        d = np.abs(a[..., :3] * a[..., 3:] - b[..., :3] * b[..., 3:]).mean(axis=-1)
        return float(d[mask].mean())
    return float(np.mean(np.abs(a - b)))


def find_loop_end(thumbs: list[np.ndarray], lo: int | None = None,
                  hi: int | None = None) -> tuple[int, float]:
    """프레임 0 과 가장 비슷한 프레임 j (lo <= j <= hi) 를 찾는다.

    반환값 end 는 배타적 끝 인덱스이므로 [0, end) 를 반복 재생하면 end 에서 0 으로 이어진다.
    lo/hi 를 주지 않으면 영상 후반 40% 에서 찾는다 (가능한 긴 루프).
    """
    n = len(thumbs)
    lo = max(2, int(n * 0.6) if lo is None else lo)
    hi = n - 1 if hi is None else min(hi, n - 1)
    if lo > hi:
        return n, frame_diff(thumbs[-1], thumbs[0])
    score, j = min((frame_diff(thumbs[j], thumbs[0]), j) for j in range(lo, hi + 1))
    return j, score


def resample_indices(count: int, src_fps: float, dst_fps: float) -> list[int]:
    """count 프레임(src_fps)을 dst_fps 로 균등 샘플링한 인덱스."""
    if dst_fps >= src_fps:
        return list(range(count))
    out_n = max(1, round(count * dst_fps / src_fps))
    return [min(count - 1, int(i * count / out_n)) for i in range(out_n)]


def grade(score: float) -> str:
    """frame_diff 점수를 사람이 읽을 등급으로. 경험적 기준이므로 실제 소재로 조정 필요."""
    if score < 0.04:
        return "OK"
    if score < 0.08:
        return "주의"
    return "튐"
