"""텍스처 용량/메모리 리포트."""
import json
import math
from pathlib import Path

from PIL import Image

MB = 1024 * 1024


def texture_cost(w: int, h: int) -> dict:
    """GPU 메모리 추정 (밉맵 없음). ASTC 는 블록당 16바이트."""
    return {
        "rgba": w * h * 4,
        "astc6": math.ceil(w / 6) * math.ceil(h / 6) * 16,
        "astc8": math.ceil(w / 8) * math.ceil(h / 8) * 16,
    }


def image_stats(paths: list[Path]) -> dict:
    tot = {"png": 0, "rgba": 0, "astc6": 0, "astc8": 0, "sizes": []}
    for p in paths:
        with Image.open(p) as im:
            w, h = im.size
        tot["png"] += p.stat().st_size
        for k, v in texture_cost(w, h).items():
            tot[k] += v
        tot["sizes"].append(f"{w}x{h}")
    return tot


def scan(folder: Path) -> list[dict]:
    """폴더 아래 모든 *.character.json 을 찾아 클립별 프레임 수와 텍스처 비용을 모은다 (예전 출력도 지원)."""
    rows = []
    for man in sorted(folder.rglob("*.character.json")):
        d = man.parent
        ch = json.loads(man.read_text(encoding="utf-8"))
        for c in ch["clips"]:
            meta = json.loads((d / c["json"]).read_text(encoding="utf-8"))
            images = meta.get("images") or [meta["image"]]
            st = image_stats([d / i for i in images])
            rows.append({"character": ch["name"], "dir": str(d), "clip": c["name"],
                         "frames": len(meta["frames"]), "fps": meta.get("fps"),
                         "playback": meta.get("playback", "loop" if meta.get("loop") else "once"),
                         **st})
    return rows


def format_rows(rows: list[dict], title: str = "") -> str:
    if not rows:
        return "(출력 없음)"
    out = []
    if title:
        out.append(title)
    out.append(f"{'캐릭터':<16}{'클립':<12}{'프레임':>6}  {'페이지(크기)':<24}{'PNG':>8}{'RGBA':>9}{'ASTC6x6':>9}{'ASTC8x8':>9}")
    total = {"png": 0, "rgba": 0, "astc6": 0, "astc8": 0}
    by_char = {}
    for r in rows:
        out.append(f"{r['character']:<16}{r['clip']:<12}{r['frames']:>6}  {', '.join(r['sizes']):<24}"
                   f"{r['png'] / MB:>7.1f}M{r['rgba'] / MB:>8.1f}M{r['astc6'] / MB:>8.2f}M{r['astc8'] / MB:>8.2f}M")
        for k in total:
            total[k] += r[k]
            by_char.setdefault(r["character"], dict.fromkeys(total, 0))[k] += r[k]
    if len(by_char) > 1:
        for name, t in by_char.items():
            out.append(f"{'  소계 ' + name:<34}{'':<24}{t['png'] / MB:>7.1f}M{t['rgba'] / MB:>8.1f}M"
                       f"{t['astc6'] / MB:>8.2f}M{t['astc8'] / MB:>8.2f}M")
    out.append(f"{'  합계':<34}{'':<24}{total['png'] / MB:>7.1f}M{total['rgba'] / MB:>8.1f}M"
               f"{total['astc6'] / MB:>8.2f}M{total['astc8'] / MB:>8.2f}M")
    out.append("  PNG=디스크 용량, RGBA/ASTC=GPU 메모리 추정(밉맵 없음). 빌드 용량은 PNG 가 아니라 이 값으로 판단.")
    return "\n".join(out)


def _world_frames(d: Path, meta: dict):
    """각 프레임을 Unity 와 같은 방식(피벗 = 원점)으로 놓았을 때의 불투명 픽셀 좌표."""
    import numpy as np
    pages = {}
    for f in meta["frames"]:
        page = f.get("page", 0)
        if page not in pages:
            images = meta.get("images") or [meta["image"]]
            pages[page] = np.asarray(Image.open(d / images[page]).convert("RGBA"))
        pg = pages[page]
        H = pg.shape[0]
        piece = pg[H - f["y"] - f["h"]:H - f["y"], f["x"]:f["x"] + f["w"], 3]
        pv = f.get("pivot") or meta["pivot"]
        ys, xs = np.nonzero(piece > 128)
        if len(xs) == 0:
            yield None
            continue
        left, bottom = -pv["x"] * f["w"], -pv["y"] * f["h"]
        wx = left + xs
        wy = bottom + (f["h"] - 1 - ys)
        yield {"ground": float(wy.min()), "top": float(wy.max()),
               "cx": float(wx.mean()), "cy": float(wy.mean()),
               "low_cx": float(wx[wy <= np.percentile(wy, 15)].mean()),
               "pivot_out": not (0 <= pv["x"] <= 1 and 0 <= pv["y"] <= 1)}


def x_jumps(cx, loop: bool, height: float) -> list[tuple[int, float]]:
    """가로 위치가 갑자기 바뀌는 지점: [(바뀐 뒤 프레임 인덱스, 이동 px)].

    전체 흔들림 폭(ptp)으로는 한 프레임만 옆으로 튀는 경우가 묻힌다.
    평소 프레임 간 이동의 4배 이상이면서 캐릭터 키의 1%(최소 3px)를 넘는 이동을 잡는다.
    """
    import numpy as np
    cx = np.asarray(cx, dtype=float)
    if len(cx) < 3:
        return []
    steps = np.diff(np.r_[cx, cx[:1]]) if loop else np.diff(cx)
    med = float(np.median(np.abs(steps)))
    limit = max(4 * med, 0.01 * height, 3.0)
    return [((i + 1) % len(cx), float(v)) for i, v in enumerate(steps) if abs(v) > limit]


def check(folder: Path) -> str:
    """출력 데이터만으로 '튕김' 을 측정한다. Unity 배치와 같은 계산(피벗 기준)."""
    import numpy as np
    out = []
    for man in sorted(folder.rglob("*.character.json")):
        d = man.parent
        ch = json.loads(man.read_text(encoding="utf-8"))
        for c in ch["clips"]:
            meta = json.loads((d / c["json"]).read_text(encoding="utf-8"))
            fr = list(_world_frames(d, meta))
            order = meta.get("sequence") or list(range(len(fr)))
            seq = [fr[i] for i in order if fr[i] is not None]
            if len(seq) < 2:
                continue
            g = np.array([s["ground"] for s in seq])
            t = np.array([s["top"] for s in seq])
            lx = np.array([s["low_cx"] for s in seq])
            cy = np.array([s["cy"] for s in seq])
            loop = meta.get("loop", False)
            steps = np.abs(np.diff(np.r_[cy, cy[:1]] if loop else cy))
            body = steps[:-1] if loop else steps
            med = float(np.median(body)) if len(body) else 0.0
            seam = float(steps[-1]) if loop else 0.0
            # 무게중심 세로 이동 방향이 바뀌는 횟수 (자연 호흡 1주기 = 2회)
            dirs = np.sign(np.diff(cy))
            dirs = dirs[dirs != 0]
            flips = int(np.sum(dirs[1:] != dirs[:-1])) if len(dirs) > 1 else 0
            applied = meta.get("applied", {})
            shifts = applied.get("stabShifts") or []
            max_shift = max((max(abs(a), abs(b)) for a, b in shifts), default=0)
            pv_out = sum(s["pivot_out"] for s in seq)
            names = [i for i in order if fr[i] is not None]
            height = float(np.median(t - g))
            jumps = x_jumps([s["cx"] for s in seq], loop, height)
            out.append(f"[{ch['name']}] {c['name']}  ({len(fr)}프레임, 재생 {len(seq)}, {meta.get('playback', '')})")
            out.append(f"  바닥선 출렁임   {np.ptp(g):6.1f}px   (잘린 다리/발끝 선. 1px 넘으면 위아래로 튐)")
            out.append(f"  머리끝 출렁임   {np.ptp(t):6.1f}px")
            out.append(f"  하체 좌우 흔들림 {np.ptp(lx):6.1f}px")
            if loop:
                ratio = seam / med if med > 0 else 0
                out.append(f"  루프 이음매 이동 {seam:6.2f}px  (평소 프레임 간 {med:.2f}px, {ratio:.1f}배)")
            out.append(f"  움직임 방향 전환 {flips}회")
            if jumps:
                out.append("  좌우 튕김       " + ", ".join(f"{names[i]:03d}번 프레임 {v:+.1f}px" for i, v in jumps))
            out.append(f"  흔들림 보정 최대 {max_shift}px, 잠근 축 {applied.get('stabLocked') or '-'}"
                       f"{', 피벗 범위 밖 프레임 ' + str(pv_out) if pv_out else ''}")
            verdict = []
            if np.ptp(g) > 1.5:
                verdict.append("바닥선이 위아래로 움직임 → 잘린 다리 끝이 튐")
            if loop and med > 0 and seam > 3 * med and seam > 1.0:
                verdict.append("루프 이음매에서 점프")
            if flips > max(4, len(seq) // 3):
                verdict.append("방향이 너무 자주 바뀜 → 떨림/핑퐁 느낌")
            if jumps:
                verdict.append("특정 프레임에서 좌우로 튐 → 흔들림 보정 오측정 가능성 (stabilize none 으로 비교)")
            if pv_out:
                verdict.append("피벗이 스프라이트 밖 → Unity 처리에 따라 위치가 어긋날 수 있음")
            out.append("  판정: " + ("; ".join(verdict) if verdict else
                                     "데이터상 튕김 없음 → Unity 쪽(임포트/표시 설정) 확인 필요"))
            out.append("")
    return "\n".join(out) if out else "(출력 없음)"
