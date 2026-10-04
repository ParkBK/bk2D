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
