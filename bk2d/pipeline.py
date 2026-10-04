"""캐릭터 단위 빌드: 영상 여러 개 -> 같은 스케일/피벗을 공유하는 스프라이트시트 세트."""
import hashlib
import json
from datetime import datetime
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__, layout, matte, stabilize, timing, video
from . import report as report_mod


@dataclass
class ClipSpec:
    name: str
    src: Path
    loop: bool = False
    fps: float | None = None
    loop_seconds: tuple[float, float] | None = None  # 루프 길이 탐색 범위 (최소, 최대)
    stabilize: str | None = None      # feet | body | none. None 이면 루프 클립은 feet, 나머지는 none
    max_frames: int | None = None     # 저장 프레임 수 상한
    pingpong: bool = False            # 루프를 정방향+역방향 재생으로 (저장 프레임 약 절반)
    loop_crossfade: int = 0           # 루프 시작 n 프레임에 루프 끝 다음(원본 영상의 이어지는) 프레임을 섞어 이음매 제거
    meta: dict = field(default_factory=dict)  # 생성 메타데이터(서비스, 시드, 프롬프트, 레퍼런스 등) 수동 입력


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
    max_texture: int = 2048           # 아틀라스 한 장 최대 크기. 넘으면 여러 장으로 분할
    trim: bool = True                 # 프레임별 투명 여백 잘라내기 (target=ui 에서는 항상 끔)
    pixels_per_unit: int = 100
    target: str = "sprite"            # sprite = SpriteRenderer, ui = UI Image (Canvas)
    unity: dict = field(default_factory=dict)  # Unity 텍스처 설정 (format, max_texture_size, platforms)

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
            overrides = prof.pop("clip_overrides", {})
            raw = {**raw, **prof, "name": f"{raw['name']}_{profile}"}
            if only:
                raw["clips"] = [c for c in raw["clips"] if c["name"] in only]
                if not raw["clips"]:
                    raise ValueError(f"프로필 '{profile}' 의 only {only} 에 해당하는 클립이 없습니다.")
                if raw.get("default") not in only:
                    raw["default"] = raw["clips"][0]["name"]
            # 프로필 안에서 특정 클립만 덮어쓰기: "clip_overrides": {"attack_1": {"max_frames": 16}}
            raw["clips"] = [{**c, **overrides.get(c["name"], {})} for c in raw["clips"]]
        def loop_range(d):
            r = d.get("loop_seconds")
            return tuple(r) if r else None
        clips = [ClipSpec(name=c["name"], src=(base / c["src"]).resolve(),
                          loop=c.get("loop", False), fps=c.get("fps"),
                          loop_seconds=loop_range(c) or loop_range(raw),
                          stabilize=c.get("stabilize", raw.get("stabilize")),
                          max_frames=c.get("max_frames", raw.get("max_frames")),
                          pingpong=bool(c.get("pingpong", raw.get("pingpong", False))),
                          loop_crossfade=int(c.get("loop_crossfade", raw.get("loop_crossfade", 0))),
                          meta=dict(c.get("meta", {})))
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
            max_texture=raw.get("max_texture", 2048),
            trim=raw.get("trim", True),
            pixels_per_unit=raw.get("pixels_per_unit", 100),
            target=raw.get("target", "sprite"),
            unity=dict(raw.get("unity", {})),
        )


@dataclass
class ClipResult:
    spec: ClipSpec
    frames: list[np.ndarray]           # RGBA, 원본 좌표
    placement: layout.Placement
    fps: float
    loop_score: float | None = None
    notes: list[str] = field(default_factory=list)
    sequence: list[int] | None = None  # 재생 순서 (핑퐁). None 이면 0..n-1
    log: dict = field(default_factory=dict)  # 진단용: 실제 적용된 값


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

    dst_fps = spec.fps or ch.fps
    out_fps = min(dst_fps, src_fps)
    n_src = len(paths)
    max_frames = spec.max_frames
    sequence = None
    n_extra = 0
    diag = {"srcFrames": n_src, "srcFps": round(src_fps, 3), "fps": out_fps, "maxFrames": max_frames}

    if spec.loop:
        thumbs = [video.load_thumb(p) for p in paths]
        lo_s, hi_s = spec.loop_seconds or (None, None)
        # 재생 길이 상한(출력 프레임): loop_seconds[1] * fps, max_frames (핑퐁은 저장 k 장 -> 재생 2k-2 장)
        caps = []
        if hi_s:
            caps.append(int(hi_s * out_fps + 1e-6))
        if max_frames:
            caps.append(2 * max_frames - 2 if spec.pingpong else max_frames)
        cap_out = min(caps) if caps else None
        # 출력 프레임 상한 -> 원본 프레임 상한. end*out/src <= cap_out 이면 반올림해도 cap_out 이하.
        hi_src = min(n_src - 1, int(cap_out * src_fps / out_fps)) if cap_out else None
        lo_src = round(lo_s * src_fps) if lo_s else None
        if hi_src is not None and lo_src is not None:
            lo_src = min(lo_src, hi_src)
        diag.update(loopSeconds=[lo_s, hi_s], loopCapFrames=cap_out, searchSrc=[lo_src, hi_src])

        if spec.pingpong:
            # 0 -> 반환점 -> 0. 반환점은 루프 반주기 범위에서 기준 포즈와 가장 먼 프레임.
            turn_hi = hi_src // 2 if hi_src else n_src // 2
            turn_lo = (lo_src or 2) // 2
            turn, _ = timing.find_pingpong_turn(thumbs, turn_lo, turn_hi)
            loop_score = 0.0
            n_out = max(2, round(turn * out_fps / src_fps) + 1)
            if max_frames:
                n_out = min(n_out, max_frames)
            idx = timing.spaced_inclusive(turn + 1, n_out)
            paths = [paths[i] for i in idx]
            sequence = list(range(len(paths))) + list(range(len(paths) - 2, 0, -1))
            notes.append(f"핑퐁: 0→{turn}→0 원본 프레임 ({2 * turn / src_fps:.2f}초), 저장 {len(paths)}장 / 재생 {len(sequence)}장")
            diag.update(pingpongTurnSrc=turn)
        else:
            if lo_src is not None or hi_src is not None:
                end, loop_score = timing.find_loop_end(thumbs, lo_src, hi_src)
            else:
                end, loop_score = timing.find_loop_end(thumbs)
            if end < n_src:
                notes.append(f"루프 구간 {end}/{n_src} 프레임 ({end / src_fps:.2f}초)으로 자름")
            all_paths = paths
            src_idx = timing.resample_indices(end, src_fps, dst_fps)
            if cap_out and len(src_idx) > cap_out:  # 안전장치: 어떤 경우에도 상한 초과 금지
                src_idx = [src_idx[i] for i in timing.resample_indices(len(src_idx), len(src_idx), cap_out)]
            paths = [all_paths[i] for i in src_idx]
            # 크로스페이드용: 루프 끝 다음에 실제 영상에서 이어지는 프레임 (end + i 는 프레임 i 의 "다음 바퀴")
            extra = [all_paths[end + i] for i in src_idx[:max(0, min(spec.loop_crossfade, len(src_idx) - 1))]
                     if end + i < n_src]
            n_extra = len(extra)
            paths = paths + extra
            diag.update(loopEndSrc=end, crossfade=n_extra)
    else:
        # 1회 재생은 마지막 프레임(기준 포즈 복귀)이 반드시 포함돼야 한다. 양 끝 포함 균등 샘플링.
        n_out = max(1, round((n_src - 1) * out_fps / src_fps) + 1)
        paths = [paths[i] for i in timing.spaced_inclusive(n_src, n_out)]
        if max_frames and len(paths) > max_frames:
            # 1회 재생은 동작을 자르면 기준 포즈 복귀가 사라지므로, 길이는 유지하고 fps 를 낮춘다.
            dur = n_src / src_fps
            paths = [paths[i] for i in timing.spaced_inclusive(len(paths), max_frames)]
            out_fps = round(max_frames / dur, 3)
            notes.append(f"max_frames {max_frames}: 길이 {dur:.2f}초 유지, 실효 fps {out_fps:g} 로 낮춤")
    diag.update(frames=len(paths), fps=out_fps)

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

    mode = spec.stabilize or ("feet" if spec.loop else "none")
    if mode != "none":
        locked = stabilize.locked_axes(frames[0])
        frames, shifts = stabilize.stabilize(frames, mode)
        drift = max(max(abs(dx), abs(dy)) for dx, dy in shifts)
        diag.update(stabilize=mode, stabShifts=[list(v) for v in shifts], stabLocked=locked)
        if drift > 0:
            notes.append(f"흔들림 보정({mode}): 최대 {drift}px 이동 되돌림 (원본 해상도 기준)")
        if locked:
            notes.append(f"흔들림 보정: 캐릭터가 화면 끝에서 잘려 있어 {'/'.join(locked)} 방향 보정은 끔 "
                         "(잘린 선이 움직여 튕겨 보이는 것 방지)")

    if n_extra:
        # 시작 프레임들을 "끝 다음 프레임" 쪽에서 원래 프레임 쪽으로 서서히 섞는다.
        # 마지막 프레임 -> 0번 프레임 전환이 실제 영상의 연속 동작이 되어 튀지 않는다.
        body, tail = frames[:-n_extra], frames[-n_extra:]
        for j, nxt in enumerate(tail):
            body[j] = matte.blend(nxt, body[j], (j + 1) / (n_extra + 1))
        frames = body
        notes.append(f"루프 크로스페이드: 시작 {n_extra}프레임을 이어지는 동작과 섞음")

    if target_height is None:  # "source": 이 클립(기준 클립)의 원본 크기를 그대로 쓴다
        box = layout.alpha_bbox(frames[0])
        if box is None:
            raise RuntimeError("첫 프레임에서 캐릭터를 찾지 못했습니다. 키 색/허용치를 확인하세요.")
        target_height = box[3] - box[1]
    placement = layout.placement_from_base(frames[0], target_height)
    diag.update(targetHeight=target_height, scale=round(placement.scale, 4))
    if placement.scale > 1.05:
        notes.append(f"경고: 원본보다 {placement.scale:.2f}배 확대됨 — 흐려짐. 더 높은 해상도(1080P)로 생성하거나 height 를 낮추세요")
    log(f"  [{spec.name}] 적용값: 원본 {n_src}프레임@{src_fps:g}fps -> {len(frames)}프레임@{out_fps:g}fps"
        f" (loop_seconds={list(spec.loop_seconds) if spec.loop_seconds else None}, 상한 {diag.get('loopCapFrames')},"
        f" max_frames={max_frames}, pingpong={spec.pingpong}), height {target_height}, scale {placement.scale:.3f}")
    return ClipResult(spec, frames, placement, out_fps, loop_score, notes, sequence, diag)


def build(ch: CharacterSpec, out_dir: Path, previews: bool = True, log=print,
          preview_dir: Path | None = None) -> dict:
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

    trim = ch.trim and ch.target != "ui"  # UI Image 는 스프라이트 크기에 맞춰 늘어나므로 트리밍 불가
    unity = {
        "maxTextureSize": int(ch.unity.get("max_texture_size", ch.max_texture)),
        "format": ch.unity.get("format", "ASTC_6x6"),
        "platforms": ch.unity.get("platforms", ["Android", "iPhone"]),
    }
    built = datetime.now().isoformat(timespec="seconds")
    manifest = {"version": 2, "name": ch.name, "default": ch.default,
                "pixelsPerUnit": ch.pixels_per_unit, "target": ch.target, "unity": unity,
                # 매 빌드마다 내용이 바뀌어야 Unity 가 재임포트(자동 임포트 트리거)한다.
                "built": built, "clips": []}
    report = []
    for r in results:
        name = r.spec.name
        cells = rendered[name]
        stem = f"{ch.name}_{name}"

        # QA 는 기존과 동일하게 트리밍 전 공통 셀 기준으로 계산
        first, last = _qa_thumb(cells[0]), _qa_thumb(cells[-1])
        qa = {
            "startToBase": round(timing.frame_diff(first, base_thumb), 4),
            "endToBase": round(timing.frame_diff(last, base_thumb), 4),
        }
        if r.spec.loop:
            qa["loopSeam"] = round(timing.frame_diff(last, first), 4)

        # 프레임별 트리밍 -> 페이지 분할 패킹. 각 프레임 피벗은 공통 셀 피벗(발밑)을 그대로 가리킨다.
        pieces = [layout.trim(c, keep=(cell.pivot_x, cell.pivot_y)) if trim else (c, (0, 0)) for c in cells]
        pages, rects = layout.pack_pages([p for p, _ in pieces], ch.spacing, ch.max_texture)
        images = [f"{stem}.png"] if len(pages) == 1 else [f"{stem}_p{i}.png" for i in range(len(pages))]
        for img, fn in zip(pages, images):
            img.save(out_dir / fn, optimize=True)
        for old in out_dir.glob(f"{stem}_p*.png"):  # 이전 빌드에서 남은 페이지 정리
            if old.name not in images:
                old.unlink()
        if len(images) > 1 and (out_dir / f"{stem}.png").exists():
            (out_dir / f"{stem}.png").unlink()

        frames_json = []
        for i, ((piece, (ox, oy)), rect) in enumerate(zip(pieces, rects)):
            w, h = piece.size
            frames_json.append({
                "name": f"{stem}_{i:03d}", **rect,
                "pivot": {"x": round((cell.pivot_x - ox) / w, 6),
                          "y": round(1.0 - (cell.pivot_y - oy) / h, 6)},
                "trim": {"x": ox, "y": cell.h - oy - h, "w": w, "h": h},
            })
        trimmed_px = sum(p.size[0] * p.size[1] for p, _ in pieces)
        if trim:
            r.notes.append(f"트리밍: 셀 대비 픽셀 {100 * trimmed_px / (cell.w * cell.h * len(cells)):.0f}%")
        if len(pages) > 1:
            r.notes.append(f"아틀라스 {len(pages)}장으로 분할 (max_texture {ch.max_texture})")
        if max(max(pg.size) for pg in pages) > unity["maxTextureSize"]:
            r.notes.append(f"경고: 페이지가 unity.max_texture_size {unity['maxTextureSize']} 보다 큼 — Unity 가 축소(흐려짐)")

        playback = "pingpong" if r.sequence else ("loop" if r.spec.loop else "once")
        meta = {**r.spec.meta, "tool": f"bk2d {__version__}", "source": r.spec.src.name,
                "sourceSha1": _sha1(r.spec.src), "built": built}
        clip_json = {
            "version": 2, "character": ch.name, "clip": name,
            "image": images[0], "images": images,
            "pageSizes": [{"w": pg.size[0], "h": pg.size[1]} for pg in pages],
            "fps": r.fps, "loop": r.spec.loop, "playback": playback,
            "pixelsPerUnit": ch.pixels_per_unit,
            "cell": {"w": cell.w, "h": cell.h}, "pivot": pivot,
            "frames": frames_json,
            "qa": qa, "meta": meta, "applied": r.log,
        }
        if r.sequence:
            clip_json["sequence"] = r.sequence
        (out_dir / f"{stem}.json").write_text(json.dumps(clip_json, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
        manifest["clips"].append({"name": name, "json": f"{stem}.json", "loop": r.spec.loop})

        if previews:
            prev = preview_dir or out_dir / "preview"
            prev.mkdir(parents=True, exist_ok=True)
            seq = [cells[i] for i in (r.sequence or range(len(cells)))]
            dur = round(1000 / r.fps)
            seq[0].save(prev / f"{stem}.webp", save_all=True, append_images=seq[1:],
                        duration=dur, loop=0, lossless=True)
            gray = [_over_gray(c) for c in seq]
            gray[0].save(prev / f"{stem}.gif", save_all=True, append_images=gray[1:],
                         duration=dur, loop=0)

        cost = report_mod.image_stats([out_dir / i for i in images])
        report.append({"clip": name, "frames": len(cells), "fps": r.fps, "playback": playback,
                       "atlas": [pg.size[0] for pg in pages[:1]] + [pg.size[1] for pg in pages[:1]],
                       "pages": [list(pg.size) for pg in pages], "cost": cost,
                       "applied": r.log, "qa": qa, "notes": r.notes})

    (out_dir / f"{ch.name}.character.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"name": ch.name, "cell": [cell.w, cell.h], "pivot": pivot, "clips": report}


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _over_gray(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, (96, 96, 96, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")
