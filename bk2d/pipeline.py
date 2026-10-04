"""캐릭터 단위 빌드: 영상 여러 개 -> 같은 스케일/피벗을 공유하는 스프라이트시트 세트."""
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import layout, matte, timing, video


@dataclass
class ClipSpec:
    name: str
    src: Path
    loop: bool = False
    fps: float | None = None
    loop_seconds: tuple[float, float] | None = None  # 루프 길이 탐색 범위 (최소, 최대)


@dataclass
class CharacterSpec:
    name: str
    clips: list[ClipSpec]
    default: str
    fps: float = 12.0
    height: int | str = 512           # 픽셀 높이, 또는 "source" = 기준 클립 원본 해상도 유지(축소 없음)
    key_mode: str = "chroma"          # chroma | rembg
    key_color: str = "auto"           # auto | #RRGGBB
    key_tol: tuple[float, float] | None = None   # None = 키 색 채도로 자동
    erase: list[list[float]] = field(default_factory=list)
    despeckle: int = 0                # 이 면적(px, 원본 해상도) 미만의 떨어진 덩어리 제거
    alpha_floor: int = 16             # 이 값 미만 알파는 0 (거의 투명한 얼룩 제거)
    alpha_smooth: float = 0.0
    max_extract_height: int = 2160    # 1080P 세로(3:4 = 1440px)도 줄이지 않도록
    padding: int = 4
    spacing: int = 2
    max_atlas: int = 8192
    pixels_per_unit: int = 100
    target: str = "sprite"            # sprite = SpriteRenderer, ui = UI Image (Canvas)

    @staticmethod
    def profiles(path: Path) -> list[str]:
        return list(json.loads(path.read_text(encoding="utf-8-sig")).get("profiles", {}))

    @staticmethod
    def load(path: Path, profile: str | None = None) -> "CharacterSpec":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))  # 메모장 BOM 허용
        base = path.parent
        if profile:
            # 프로필 값이 상위 값을 덮어쓴다. "only" 로 클립을 고를 수 있다.
            prof = dict(raw.get("profiles", {})[profile])
            only = prof.pop("only", None)
            raw = {**raw, **prof, "name": f"{raw['name']}_{profile}"}
            if only:
                raw["clips"] = [c for c in raw["clips"] if c["name"] in only]
                if not raw["clips"]:
                    raise ValueError(f"프로필 '{profile}' 의 only {only} 에 해당하는 클립이 없습니다.")
                if raw.get("default") not in only:
                    raw["default"] = raw["clips"][0]["name"]
        def loop_range(d):
            r = d.get("loop_seconds")
            return tuple(r) if r else None
        clips = [ClipSpec(name=c["name"], src=(base / c["src"]).resolve(),
                          loop=c.get("loop", False), fps=c.get("fps"),
                          loop_seconds=loop_range(c) or loop_range(raw))
                 for c in raw["clips"]]
        key = raw.get("key", {})
        return CharacterSpec(
            name=raw["name"], clips=clips, default=raw.get("default", clips[0].name),
            fps=raw.get("fps", 12.0), height=raw.get("height", 512),
            key_mode=key.get("mode", "chroma"), key_color=key.get("color", "auto"),
            key_tol=tuple(key["tolerance"]) if isinstance(key.get("tolerance"), list) else None,
            erase=raw.get("erase", []),
            despeckle=raw.get("despeckle", 0),
            alpha_floor=key.get("alpha_floor", 16),
            alpha_smooth=key.get("temporal_smooth", 0.0),
            max_extract_height=raw.get("max_extract_height", 2160),
            padding=raw.get("padding", 4), spacing=raw.get("spacing", 2),
            max_atlas=raw.get("max_atlas", 8192),
            pixels_per_unit=raw.get("pixels_per_unit", 100),
            target=raw.get("target", "sprite"),
        )


@dataclass
class ClipResult:
    spec: ClipSpec
    frames: list[np.ndarray]           # RGBA, 원본 좌표
    placement: layout.Placement
    fps: float
    loop_score: float | None = None
    notes: list[str] = field(default_factory=list)


def _qa_thumb(img: Image.Image, size: int = 96) -> np.ndarray:
    """QA 비교용 RGBA 썸네일 (셀 비율 유지)."""
    w, h = img.size
    k = size / max(w, h)
    small = img.convert("RGBa").resize((max(1, round(w * k)), max(1, round(h * k))), Image.BILINEAR)
    return np.asarray(small.convert("RGBA"), dtype=np.float32) / 255.0


def process_clip(spec: ClipSpec, ch: CharacterSpec, work: Path, log,
                 target_height: int | None) -> ClipResult:
    src_fps = video.probe_fps(spec.src)
    paths = video.extract_frames(spec.src, work / spec.name, ch.max_extract_height)
    notes = []
    loop_score = None

    if spec.loop:
        thumbs = [video.load_thumb(p) for p in paths]
        if spec.loop_seconds:
            lo_s, hi_s = spec.loop_seconds
            end, loop_score = timing.find_loop_end(thumbs, round(lo_s * src_fps), round(hi_s * src_fps))
        else:
            end, loop_score = timing.find_loop_end(thumbs)
        if end < len(paths):
            notes.append(f"루프 구간 {end}/{len(paths)} 프레임 ({end / src_fps:.2f}초)으로 자름")
        paths = paths[:end]

    dst_fps = spec.fps or ch.fps
    idx = timing.resample_indices(len(paths), src_fps, dst_fps)
    paths = [paths[i] for i in idx]
    out_fps = dst_fps if dst_fps < src_fps else src_fps

    rgbs = [video.load_rgb(p) for p in paths]
    if ch.key_mode == "chroma":
        key = (matte.estimate_key_color(rgbs[0]) if ch.key_color == "auto"
               else matte.parse_color(ch.key_color))
        tol = ch.key_tol or matte.auto_tolerance(key)
        sat = matte.key_saturation(key)
        notes.append("키 색 #%02x%02x%02x (채도 %.0f), 허용치 %.0f~%.0f"
                     % (*(int(v) for v in key), sat, *tol))
        if sat < 60:
            notes.append("경고: 배경 초록이 탁함 — 어두운 머리/옷이 반투명해질 수 있음. "
                         "선명한 #00FF00 배경으로 생성하는 것이 안전")
        frames = [matte.chroma_key(f, key, *tol) for f in rgbs]
    elif ch.key_mode == "rembg":
        frames = [matte.rembg_matte(f) for f in rgbs]
    else:
        raise ValueError(f"알 수 없는 key mode: {ch.key_mode}")
    frames = [matte.despeckle(matte.alpha_floor(matte.erase_regions(f, ch.erase), ch.alpha_floor),
                              ch.despeckle) for f in frames]
    frames = matte.temporal_smooth_alpha(frames, ch.alpha_smooth if spec.loop else 0.0)

    # 화면 가장자리 3px 띠에 걸친 픽셀 수로 "실제 잘림" 과 "잡티" 를 구분한다.
    sides = {"위": [], "아래": [], "왼쪽": [], "오른쪽": []}
    for f in frames:
        m = f[..., 3] > layout.ALPHA_THRESHOLD
        for side, strip in (("위", m[:3]), ("아래", m[-3:]), ("왼쪽", m[:, :3]), ("오른쪽", m[:, -3:])):
            if (cnt := int(strip.sum())) > 0:
                sides[side].append(cnt)
    for side, hits in sides.items():
        if not hits:
            continue
        kind = "잡티 가능성 큼 (despeckle 권장)" if max(hits) < 30 else "실제로 잘렸을 가능성 큼"
        notes.append(f"경고: 화면 {side} 끝에 닿음 — {len(hits)}/{len(frames)} 프레임, "
                     f"최대 {max(hits)}px → {kind}")

    if target_height is None:  # "source": 이 클립(기준 클립)의 원본 크기를 그대로 쓴다
        box = layout.alpha_bbox(frames[0])
        if box is None:
            raise RuntimeError("첫 프레임에서 캐릭터를 찾지 못했습니다. 키 색/허용치를 확인하세요.")
        target_height = box[3] - box[1]
    placement = layout.placement_from_base(frames[0], target_height)
    if placement.scale > 1.05:
        notes.append(f"경고: 원본보다 {placement.scale:.2f}배 확대됨 — 흐려짐. 더 높은 해상도(1080P)로 생성하거나 height 를 낮추세요")
    log(f"  [{spec.name}] {len(frames)} 프레임 @ {out_fps:g}fps")
    return ClipResult(spec, frames, placement, out_fps, loop_score, notes)


def build(ch: CharacterSpec, out_dir: Path, previews: bool = True, log=print) -> dict:
    video.require_ffmpeg()
    if ch.default not in {c.name for c in ch.clips}:
        raise ValueError(f"default 클립 '{ch.default}' 이 clips 에 없습니다.")
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bk2d_") as tmp:
        # 기준(default) 클립을 먼저 처리해 목표 높이를 정하고, 나머지 클립을 그 높이에 맞춘다.
        order = sorted(ch.clips, key=lambda c: c.name != ch.default)
        target = None if ch.height == "source" else int(ch.height)
        done = {}
        for c in order:
            r = process_clip(c, ch, Path(tmp), log, target)
            if target is None:
                target = round(r.placement.scale * (lambda b: b[3] - b[1])(layout.alpha_bbox(r.frames[0])))
                log(f"  원본 해상도 유지: 캐릭터 높이 {target}px")
            done[c.name] = r
        results = [done[c.name] for c in ch.clips]

    # 모든 클립이 같은 셀 크기/피벗을 공유해야 Unity 에서 전환 시 위치가 튀지 않는다.
    extents = [e for r in results for f in r.frames
               if (e := layout.frame_extent(f, r.placement)) is not None]
    cell = layout.cell_from_extents(extents, ch.padding)
    pivot = {"x": cell.pivot_x / cell.w, "y": 1.0 - cell.pivot_y / cell.h}
    log(f"공통 셀 {cell.w}x{cell.h}, 피벗 ({pivot['x']:.3f}, {pivot['y']:.3f})")

    rendered = {r.spec.name: [layout.render_cell(f, r.placement, cell) for f in r.frames]
                for r in results}
    base_thumb = _qa_thumb(rendered[ch.default][0])

    manifest = {"version": 1, "name": ch.name, "default": ch.default,
                "pixelsPerUnit": ch.pixels_per_unit, "target": ch.target, "clips": []}
    report = []
    for r in results:
        name = r.spec.name
        cells = rendered[name]
        atlas, rects = layout.pack_grid(cells, ch.spacing, ch.max_atlas)
        if max(atlas.size) > ch.max_atlas:
            r.notes.append(f"경고: 아틀라스 {atlas.size} 가 {ch.max_atlas} 초과 — fps/height 를 낮추세요")
        elif max(atlas.size) > 4096:
            r.notes.append(f"참고: 아틀라스 {atlas.size[0]}x{atlas.size[1]} — 4096 초과라 일부 저사양 모바일에서 미지원")
        stem = f"{ch.name}_{name}"
        atlas.save(out_dir / f"{stem}.png", optimize=True)

        first, last = _qa_thumb(cells[0]), _qa_thumb(cells[-1])
        qa = {
            "startToBase": round(timing.frame_diff(first, base_thumb), 4),
            "endToBase": round(timing.frame_diff(last, base_thumb), 4),
        }
        if r.spec.loop:
            qa["loopSeam"] = round(timing.frame_diff(last, first), 4)

        clip_json = {
            "version": 1, "character": ch.name, "clip": name, "image": f"{stem}.png",
            "fps": r.fps, "loop": r.spec.loop, "pixelsPerUnit": ch.pixels_per_unit,
            "cell": {"w": cell.w, "h": cell.h}, "pivot": pivot,
            "frames": [{"name": f"{stem}_{i:03d}", **rect} for i, rect in enumerate(rects)],
            "qa": qa,
        }
        (out_dir / f"{stem}.json").write_text(json.dumps(clip_json, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
        manifest["clips"].append({"name": name, "json": f"{stem}.json", "loop": r.spec.loop})

        if previews:
            prev = out_dir / "preview"
            prev.mkdir(exist_ok=True)
            dur = round(1000 / r.fps)
            cells[0].save(prev / f"{stem}.webp", save_all=True, append_images=cells[1:],
                          duration=dur, loop=0, lossless=True)
            gray = [_over_gray(c) for c in cells]
            gray[0].save(prev / f"{stem}.gif", save_all=True, append_images=gray[1:],
                         duration=dur, loop=0)

        report.append({"clip": name, "frames": len(cells), "fps": r.fps,
                       "atlas": list(atlas.size), "qa": qa, "notes": r.notes})

    (out_dir / f"{ch.name}.character.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"cell": [cell.w, cell.h], "pivot": pivot, "clips": report}


def _over_gray(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, (96, 96, 96, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")
