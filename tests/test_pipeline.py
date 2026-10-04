"""합성 영상(초록 배경 + 도형 캐릭터)으로 파이프라인 전체를 검증한다."""
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from bk2d.pipeline import CharacterSpec, build

GREEN = (0, 200, 60)


def _draw_char(size, cx, foot_y, scale, arm=0.0, bob=0.0):
    img = Image.new("RGB", size, GREEN)
    d = ImageDraw.Draw(img)
    s = scale
    top = foot_y - 200 * s + bob * s
    d.ellipse([cx - 50 * s, top + 70 * s, cx + 50 * s, foot_y], fill=(230, 120, 40))   # 몸
    d.ellipse([cx - 35 * s, top, cx + 35 * s, top + 70 * s], fill=(250, 210, 170))      # 머리
    ax = cx + 50 * s + arm * 80 * s
    d.line([cx + 30 * s, top + 110 * s, ax, top + 110 * s], fill=(120, 60, 200), width=int(14 * s))
    return img


def _encode(frames_dir: Path, out: Path, fps=24):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i",
                    str(frames_dir / "%04d.png"), "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    "-crf", "16", str(out)], check=True)


def _make_clip(tmp: Path, name: str, n: int, fn) -> Path:
    d = tmp / f"src_{name}"
    d.mkdir()
    for i in range(n):
        fn(i).save(d / f"{i:04d}.png")
    out = tmp / f"{name}.mp4"
    _encode(d, out)
    return out


def _reconstruct(out: Path, meta_json: Path) -> list[np.ndarray]:
    """JSON 의 페이지/rect/trim 오프셋으로 트리밍 전 공통 셀 프레임을 복원한다."""
    meta = json.loads(meta_json.read_text())
    pages = [np.asarray(Image.open(out / im).convert("RGBA")) for im in meta.get("images", [meta["image"]])]
    cw, chh = meta["cell"]["w"], meta["cell"]["h"]
    cells = []
    for f in meta["frames"]:
        pg = pages[f.get("page", 0)]
        H = pg.shape[0]
        top = H - f["y"] - f["h"]
        piece = pg[top:top + f["h"], f["x"]:f["x"] + f["w"]]
        t = f.get("trim", {"x": 0, "y": 0, "w": f["w"], "h": f["h"]})
        cell = np.zeros((chh, cw, 4), dtype=np.uint8)
        ctop = chh - t["y"] - t["h"]
        cell[ctop:ctop + t["h"], t["x"]:t["x"] + t["w"]] = piece
        # 피벗이 공통 셀 피벗과 같은 점을 가리키는지
        px = t["x"] + f["pivot"]["x"] * t["w"]
        py = t["y"] + f["pivot"]["y"] * t["h"]
        assert abs(px - meta["pivot"]["x"] * cw) < 0.01 and abs(py - meta["pivot"]["y"] * chh) < 0.01
        cells.append(cell)
    return cells


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bk2d")
    size = (480, 360)
    # idle: 40프레임 주기로 숨쉬기, 영상은 52프레임 (루프 탐지가 40 근처를 찾아야 함)
    _make_clip(tmp, "idle", 52, lambda i: _draw_char(
        size, 240, 330, 1.0, bob=6 * math.sin(2 * math.pi * i / 40)))
    # attack: 다른 구도(1.3배 확대, 오른쪽 이동)로 촬영됐지만 기준 포즈에서 시작/종료
    _make_clip(tmp, "attack", 24, lambda i: _draw_char(
        size, 200, 350, 1.3, arm=math.sin(math.pi * i / 23)))
    cfg = {
        "name": "hero", "default": "idle", "fps": 12, "height": 256,
        "key": {"mode": "chroma", "color": "auto"},
        "clips": [{"name": "idle", "src": "idle.mp4", "loop": True},
                  {"name": "attack", "src": "attack.mp4"}],
    }
    (tmp / "hero.json").write_text(json.dumps(cfg))
    out = tmp / "out"
    rep = build(CharacterSpec.load(tmp / "hero.json"), out, log=lambda *_: None)
    return out, rep


def test_outputs_exist(built):
    out, _ = built
    for f in ["hero.character.json", "hero_idle.png", "hero_idle.json",
              "hero_attack.png", "hero_attack.json", "preview/hero_idle.webp"]:
        assert (out / f).exists(), f


def test_loop_detected(built):
    _, rep = built
    idle = next(c for c in rep["clips"] if c["clip"] == "idle")
    # 24fps 40프레임 주기 -> 12fps 20프레임
    assert 18 <= idle["frames"] <= 22
    assert idle["qa"]["loopSeam"] < 0.04


def test_transparency_and_no_green(built):
    out, _ = built
    a = np.stack(_reconstruct(out, out / "hero_idle.json")).astype(int)
    alpha = a[..., 3]
    assert (alpha == 0).mean() > 0.3
    opaque = a[alpha > 200]
    greenish = (opaque[:, 1] > opaque[:, 0] + 40) & (opaque[:, 1] > opaque[:, 2] + 40)
    assert greenish.mean() < 0.01


def test_clips_aligned_to_base_pose(built):
    out, rep = built
    attack = next(c for c in rep["clips"] if c["clip"] == "attack")
    # 구도가 달라도 정렬 후 시작/끝 포즈가 기준 포즈와 일치해야 함
    assert attack["qa"]["startToBase"] < 0.04
    assert attack["qa"]["endToBase"] < 0.04
    a = json.loads((out / "hero_idle.json").read_text())
    b = json.loads((out / "hero_attack.json").read_text())
    assert a["cell"] == b["cell"] and a["pivot"] == b["pivot"]


def test_rects_inside_atlas(built):
    out, _ = built
    meta = json.loads((out / "hero_attack.json").read_text())
    w, h = Image.open(out / meta["image"]).size
    assert w % 4 == 0 and h % 4 == 0
    for r in meta["frames"]:
        assert r["x"] >= 0 and r["y"] >= 0 and r["x"] + r["w"] <= w and r["y"] + r["h"] <= h


def test_pose_mismatch_is_flagged(tmp_path):
    """기준 포즈로 돌아오지 않는 액션은 endToBase 가 크게 나와야 한다."""
    size = (480, 360)
    _make_clip(tmp_path, "idle", 12, lambda i: _draw_char(size, 240, 330, 1.0))
    _make_clip(tmp_path, "bad", 24, lambda i: _draw_char(size, 240, 330, 1.0, arm=i / 23, bob=-20 * i / 23))
    cfg = {"name": "x", "default": "idle", "height": 200,
           "clips": [{"name": "idle", "src": "idle.mp4"}, {"name": "bad", "src": "bad.mp4"}]}
    (tmp_path / "x.json").write_text(json.dumps(cfg))
    rep = build(CharacterSpec.load(tmp_path / "x.json"), tmp_path / "out", previews=False, log=lambda *_: None)
    bad = next(c for c in rep["clips"] if c["clip"] == "bad")
    assert bad["qa"]["endToBase"] > 0.08


def test_dull_green_keeps_dark_character_opaque():
    """탁한 초록 배경(실제 생성 영상 수준)에서도 검은 머리/남색 옷이 반투명해지면 안 된다."""
    from bk2d import matte
    key = np.array([76, 169, 120], dtype=np.float32)
    rgb = np.zeros((4, 3, 3), dtype=np.uint8)
    rgb[0] = (20, 20, 25)     # 검은 머리
    rgb[1] = (30, 35, 80)     # 남색 옷
    rgb[2] = (240, 215, 200)  # 피부
    rgb[3] = key.astype(np.uint8)
    out = matte.chroma_key(rgb, key, *matte.auto_tolerance(key))
    assert (out[:3, :, 3] == 255).all()
    assert (out[3, :, 3] == 0).all()


def test_erase_regions():
    from bk2d import matte
    rgba = np.full((100, 100, 4), 255, dtype=np.uint8)
    out = matte.erase_regions(rgba, [[0.6, 0.9, 1.0, 1.0]])
    assert (out[90:, 60:, 3] == 0).all()
    assert (out[:90, :, 3] == 255).all() and (out[:, :60, 3] == 255).all()


def test_despeckle_removes_small_blobs_only():
    from bk2d import matte
    rgba = np.zeros((100, 100, 4), dtype=np.uint8)
    rgba[20:80, 30:70, 3] = 255    # 몸통 2400px
    rgba[5:8, 95:98, 3] = 255      # 잡티 9px (가장자리)
    rgba[85:95, 10:20, 3] = 255    # 떨어진 덩어리 100px
    out = matte.despeckle(rgba, 64)
    assert out[5:8, 95:98, 3].max() == 0
    assert out[20:80, 30:70, 3].min() == 255
    assert out[85:95, 10:20, 3].min() == 255


def test_loop_search_range():
    from bk2d import timing
    # 주기 10 프레임짜리 신호, 40 프레임 영상: 범위를 [8, 12] 로 주면 10 을 찾아야 함
    thumbs = [np.full((4, 4, 3), np.sin(2 * np.pi * i / 10), dtype=np.float32) for i in range(40)]
    end, score = timing.find_loop_end(thumbs, 8, 12)
    assert end == 10 and score < 1e-5
    end_default, _ = timing.find_loop_end(thumbs)
    assert end_default >= 24


def test_alpha_floor_removes_faint_haze():
    from bk2d import matte
    rgba = np.zeros((10, 10, 4), dtype=np.uint8)
    rgba[..., 3] = 8          # 배경 얼룩
    rgba[3:6, 3:6, 3] = 200   # 캐릭터
    out = matte.alpha_floor(rgba, 16)
    assert (out[3:6, 3:6, 3] == 200).all()
    assert out[..., 3].astype(bool).sum() == 9


def test_height_source_keeps_resolution(tmp_path):
    """height=source 면 기준 클립은 축소 없이(scale 1) 나오고, 다른 클립도 같은 크기로 맞춰진다."""
    size = (480, 360)
    _make_clip(tmp_path, "idle", 12, lambda i: _draw_char(size, 240, 330, 1.0))
    _make_clip(tmp_path, "attack", 12, lambda i: _draw_char(size, 200, 350, 1.3))
    cfg = {"name": "x", "default": "idle", "height": "source",
           "clips": [{"name": "attack", "src": "attack.mp4"}, {"name": "idle", "src": "idle.mp4", "loop": True}]}
    (tmp_path / "x.json").write_text(json.dumps(cfg))
    rep = build(CharacterSpec.load(tmp_path / "x.json"), tmp_path / "out", previews=False, log=lambda *_: None)
    # 원본 캐릭터 높이 200px + 여백 -> 셀 높이도 그 정도여야 함 (축소되지 않음)
    assert 200 <= rep["cell"][1] <= 215
    attack = next(c for c in rep["clips"] if c["clip"] == "attack")
    assert attack["qa"]["startToBase"] < 0.04


def test_profiles_build_two_variants(tmp_path):
    """profiles: battle(전체 클립, 작게) / lobby(idle 만, 크게, ui) 가 각자 폴더로 나온다."""
    from bk2d.cli import main
    size = (480, 360)
    _make_clip(tmp_path, "idle", 12, lambda i: _draw_char(size, 240, 330, 1.0))
    _make_clip(tmp_path, "attack", 12, lambda i: _draw_char(size, 240, 330, 1.0, arm=i / 11))
    cfg = {"name": "hero", "default": "idle", "height": 100,
           "profiles": {"battle": {"height": 100},
                        "lobby": {"height": 300, "only": ["idle"], "target": "ui"}},
           "clips": [{"name": "idle", "src": "idle.mp4", "loop": True}, {"name": "attack", "src": "attack.mp4"}]}
    (tmp_path / "hero.json").write_text(json.dumps(cfg))
    out = tmp_path / "out"
    assert main(["build", str(tmp_path / "hero.json"), "-o", str(out), "--no-preview",
                 "--report", str(tmp_path / "r.json")]) == 0
    battle = json.loads((out / "battle" / "hero_battle.character.json").read_text())
    lobby = json.loads((out / "lobby" / "hero_lobby.character.json").read_text())
    assert [c["name"] for c in battle["clips"]] == ["idle", "attack"]
    assert [c["name"] for c in lobby["clips"]] == ["idle"] and lobby["target"] == "ui"
    rep = json.loads((tmp_path / "r.json").read_text())
    # 원본 캐릭터 높이 200px -> lobby 300 은 확대 경고가 있어야 함
    assert any("확대" in n for n in rep["lobby"]["clips"][0]["notes"])
    assert not any("확대" in n for n in rep["battle"]["clips"][0]["notes"])


def _feet_positions(out_png: Path, meta_json: Path):
    """각 프레임(공통 셀로 복원)에서 캐릭터 하단 중앙(발) 위치."""
    pos = []
    for cell in _reconstruct(meta_json.parent, meta_json):
        ys, xs = np.nonzero(cell[..., 3] > 128)
        bottom = ys.max()
        pos.append((xs[ys >= bottom - 3].mean(), bottom))
    return np.array(pos)


@pytest.mark.parametrize("stab", ["none", "feet"])
def test_stabilize_removes_whole_body_jitter(tmp_path, stab):
    rng = np.random.default_rng(0)
    jitter = rng.integers(-5, 6, size=(36, 2))
    size = (480, 360)
    _make_clip(tmp_path, "idle", 36, lambda i: _draw_char(
        size, 240 + jitter[i][0], 330 + jitter[i][1], 1.0, bob=4 * math.sin(2 * math.pi * i / 36)))
    cfg = {"name": "x", "default": "idle", "height": "source", "fps": 24, "stabilize": stab,
           "clips": [{"name": "idle", "src": "idle.mp4", "loop": True}]}
    (tmp_path / "x.json").write_text(json.dumps(cfg))
    build(CharacterSpec.load(tmp_path / "x.json"), tmp_path / "out", previews=False, log=lambda *_: None)
    pos = _feet_positions(tmp_path / "out" / "x_idle.png", tmp_path / "out" / "x_idle.json")
    spread = (pos.max(axis=0) - pos.min(axis=0)).max()
    if stab == "none":
        assert spread >= 6       # 보정 없으면 발이 흔들림
    else:
        assert spread <= 2.5     # 보정하면 발 고정 (압축·측정 오차 ~2px)


def test_sync_clips_adds_new_videos_and_ignores_underscore(tmp_path):
    from bk2d import project
    size = (480, 360)
    _make_clip(tmp_path, "idle_1", 12, lambda i: _draw_char(size, 240, 330, 1.0))
    cfg = {"name": "hero", "default": "idle_1", "clips": [project.clip_entry("idle_1.mp4")],
           "profiles": {"lobby": {"only": ["idle_1"]}}}
    p = tmp_path / "hero.json"
    p.write_text(json.dumps(cfg))
    assert project.sync_clips(p) == ([], [])
    _make_clip(tmp_path, "attack_1", 12, lambda i: _draw_char(size, 240, 330, 1.0, arm=i / 11))
    _make_clip(tmp_path, "_attack_old", 12, lambda i: _draw_char(size, 240, 330, 1.0))
    _make_clip(tmp_path, "walk", 12, lambda i: _draw_char(size, 240, 330, 1.0))
    added, removed = project.sync_clips(p)
    assert added == ["attack_1", "walk"] and removed == []
    clips = {c["name"]: c for c in json.loads(p.read_text())["clips"]}
    assert clips["attack_1"]["loop"] is False and clips["walk"]["loop"] is True
    (tmp_path / "idle_1.mp4").unlink()
    added, removed = project.sync_clips(p)
    raw = json.loads(p.read_text())
    assert removed == ["idle_1"] and raw["default"] == "walk"
    assert raw["profiles"]["lobby"]["only"] == ["walk"]


def test_build_into_unity_assets_keeps_previews_out(tmp_path):
    from bk2d.cli import main
    work = tmp_path / "work"
    work.mkdir()
    size = (480, 360)
    _make_clip(work, "idle_1", 12, lambda i: _draw_char(size, 240, 330, 1.0))
    assert main(["init", str(work)]) == 0
    _make_clip(work, "attack_1", 12, lambda i: _draw_char(size, 240, 330, 1.0, arm=i / 11))  # init 이후 추가
    assets = tmp_path / "MyGame" / "Assets" / "Characters" / "hero"
    assert main(["build", str(work / "hero.json"), "-o", str(assets)]) == 0
    battle = json.loads((assets / "battle" / "hero_battle.character.json").read_text())
    assert [c["name"] for c in battle["clips"]] == ["idle_1", "attack_1"] and "built" in battle
    assert not list(assets.rglob("*.gif")) and not list(assets.rglob("*.webp"))
    assert (work / "_preview" / "battle" / "hero_battle_attack_1.webp").exists()


@pytest.fixture(scope="module")
def idle_long(tmp_path_factory):
    """5초(120프레임 @24fps), 2초 주기 숨쉬기 idle + 1.5초 attack."""
    tmp = tmp_path_factory.mktemp("long")
    size = (480, 360)
    _make_clip(tmp, "idle", 120, lambda i: _draw_char(size, 240, 330, 1.0, bob=6 * math.sin(2 * math.pi * i / 48)))
    _make_clip(tmp, "attack", 36, lambda i: _draw_char(size, 240, 330, 1.0, arm=math.sin(math.pi * i / 35)))
    return tmp


def _build_cfg(tmp, out_name, **cfg):
    clips = [{"name": "idle", "src": "idle.mp4", "loop": True}]
    if (tmp / "attack.mp4").exists():
        clips.append({"name": "attack", "src": "attack.mp4"})
    base = {"name": "x", "default": "idle", "height": 200, "clips": clips}
    base.update(cfg)
    (tmp / f"{out_name}.json").write_text(json.dumps(base))
    rep = build(CharacterSpec.load(tmp / f"{out_name}.json"), tmp / out_name, previews=False, log=lambda *_: None)
    return {c["clip"]: c for c in rep["clips"]}


def test_loop_seconds_max_caps_loop_length(idle_long):
    clips = _build_cfg(idle_long, "cap", fps=8, loop_seconds=[1.0, 1.5])
    assert clips["idle"]["frames"] <= 12            # 1.5초 * 8fps
    clips = _build_cfg(idle_long, "cap2", fps=8, loop_seconds=[3.0, 4.0])  # 범위 안에 좋은 루프 없어도 상한 지킴
    assert clips["idle"]["frames"] <= 32


def test_max_frames(idle_long):
    clips = _build_cfg(idle_long, "mf", fps=12, loop_seconds=[1.0, 2.5], max_frames=10)
    assert clips["idle"]["frames"] <= 10
    a = clips["attack"]
    assert a["frames"] == 10
    assert abs(a["frames"] / a["fps"] - 1.5) < 0.05   # 1회 재생은 길이 유지, fps 를 낮춤
    assert a["qa"]["endToBase"] < 0.04                # 마지막(기준 포즈 복귀) 프레임 보존


def test_pingpong_halves_frames(idle_long):
    pp = _build_cfg(idle_long, "pp1", fps=12, loop_seconds=[1.5, 2.5], pingpong=True)
    assert pp["idle"]["playback"] == "pingpong"
    meta = json.loads((idle_long / "pp1" / "x_idle.json").read_text())
    n = len(meta["frames"])
    assert meta["sequence"] == list(range(n)) + list(range(n - 2, 0, -1))
    assert n == len(meta["sequence"]) // 2 + 1         # 저장 = 재생의 절반 + 1
    assert len(meta["sequence"]) <= 30                 # 재생 길이도 loop_seconds 상한(2.5초*12) 이하
    capped = _build_cfg(idle_long, "pp2", fps=12, loop_seconds=[1.5, 2.5], pingpong=True, max_frames=8)
    assert capped["idle"]["frames"] <= 8
    assert pp["attack"]["playback"] == "once"          # 1회 클립은 핑퐁 대상 아님


def test_multi_page_and_trim(idle_long):
    clips = _build_cfg(idle_long, "pg", fps=12, max_texture=256, height=120)
    out = idle_long / "pg"
    meta = json.loads((out / "x_idle.json").read_text())
    assert len(meta["images"]) > 1
    for im in meta["images"]:
        w, h = Image.open(out / im).size
        assert w <= 256 and h <= 256 and w % 4 == 0 and h % 4 == 0
    cells = _reconstruct(out, out / "x_idle.json")          # 페이지/트리밍/피벗 복원 검증 포함
    assert all(c.shape[:2] == (meta["cell"]["h"], meta["cell"]["w"]) for c in cells)
    assert any(f["w"] < meta["cell"]["w"] for f in meta["frames"])   # 실제로 잘렸는지


def test_ui_target_is_not_trimmed_and_meta_written(idle_long):
    cfg = {"target": "ui", "unity": {"format": "ASTC_8x8", "max_texture_size": 1024}}
    raw = {"name": "x", "default": "idle", "height": 200, **cfg,
           "clips": [{"name": "idle", "src": "idle.mp4", "loop": True,
                      "meta": {"service": "Higgsfield", "seed": "1234", "prompt": "breathing", "reference": "base.png"}}]}
    (idle_long / "ui.json").write_text(json.dumps(raw))
    build(CharacterSpec.load(idle_long / "ui.json"), idle_long / "ui", previews=False, log=lambda *_: None)
    meta = json.loads((idle_long / "ui" / "x_idle.json").read_text())
    assert all(f["w"] == meta["cell"]["w"] and f["h"] == meta["cell"]["h"] for f in meta["frames"])
    assert meta["meta"]["service"] == "Higgsfield" and meta["meta"]["seed"] == "1234"
    assert meta["meta"]["tool"].startswith("bk2d ") and len(meta["meta"]["sourceSha1"]) == 40
    man = json.loads((idle_long / "ui" / "x.character.json").read_text())
    assert man["unity"] == {"maxTextureSize": 1024, "format": "ASTC_8x8", "platforms": ["Android", "iPhone"]}


def test_stats_command(idle_long, capsys):
    from bk2d.cli import main
    _build_cfg(idle_long, "st", fps=8)
    assert main(["stats", str(idle_long / "st")]) == 0
    out = capsys.readouterr().out
    assert "ASTC6x6" in out and "idle" in out and "합계" in out


def test_once_clip_keeps_last_frame(idle_long):
    """fps 를 낮춰도 1회 클립의 마지막(기준 포즈 복귀) 프레임이 빠지면 안 된다 (이전 버전 버그)."""
    clips = _build_cfg(idle_long, "last", fps=8)
    assert clips["attack"]["qa"]["endToBase"] < 0.04


def test_loop_crossfade_smooths_forced_short_loop(tmp_path):
    """자연 주기(2초)보다 짧게(1.3초) 자르면 팔이 뻗은 상태에서 처음으로 점프한다. 크로스페이드가 이를 없애야 한다."""
    size = (480, 360)
    tri = lambda i: 1 - abs((i % 48) / 24 - 1)       # 0 -> 1 -> 0, 48프레임(2초) 주기
    _make_clip(tmp_path, "idle", 96, lambda i: _draw_char(size, 240, 330, 1.0, arm=tri(i)))
    plain = _build_cfg(tmp_path, "xf0", fps=12, loop_seconds=[1.0, 1.3])
    xf = _build_cfg(tmp_path, "xf1", fps=12, loop_seconds=[1.0, 1.3], loop_crossfade=3)
    assert xf["idle"]["frames"] == plain["idle"]["frames"]     # 프레임 수는 그대로
    assert xf["idle"]["qa"]["loopSeam"] < plain["idle"]["qa"]["loopSeam"] * 0.5


def test_profile_timing_mismatch_warns(idle_long, capsys):
    from bk2d.cli import main
    cfg = {"name": "x", "default": "idle", "fps": 8, "loop_seconds": [1.5, 2.5], "pingpong": True,
           "profiles": {"battle": {"height": 150}, "lobby": {"height": 200, "max_frames": 4}},
           "clips": [{"name": "idle", "src": "idle.mp4", "loop": True}]}
    (idle_long / "tm.json").write_text(json.dumps(cfg))
    assert main(["build", str(idle_long / "tm.json"), "-o", str(idle_long / "tm"), "--no-preview", "--no-sync"]) == 0
    assert "동작 타이밍이 프로필마다 다릅니다" in capsys.readouterr().out


def test_stabilize_locks_axis_when_character_is_cut_at_edge():
    """발이 화면 아래에서 잘린 캐릭터는 세로 보정을 하면 잘린 선이 위아래로 튄다 -> 세로 보정 금지."""
    from bk2d import stabilize
    rng = np.random.default_rng(3)
    frames = []
    for i in range(10):
        a = np.zeros((300, 200, 4), dtype=np.uint8)
        dx, dy = rng.integers(-4, 5, size=2)
        a[40 + dy:300, 60 + dx:140 + dx] = (200, 120, 60, 255)    # 아래로 잘린 몸통
        a[60 + dy:80 + dy, 70 + dx:90 + dx] = (30, 30, 30, 255)     # 무늬(측정용)
        frames.append(a)
    out, shifts = stabilize.stabilize(frames, "feet")
    assert all(dy == 0 for _, dy in shifts)                        # 세로는 잠김
    bottoms = [np.nonzero(f[..., 3])[0].max() for f in out]
    assert len(set(bottoms)) == 1                                 # 잘린 선 고정
    assert stabilize.locked_axes(frames[0]) == ["세로"]


def test_check_command_reports_bounce_metrics(idle_long, capsys):
    from bk2d.cli import main
    _build_cfg(idle_long, "ck", fps=8, loop_seconds=[1.5, 2.5], loop_crossfade=2)
    assert main(["check", str(idle_long / "ck")]) == 0
    out = capsys.readouterr().out
    assert "바닥선 출렁임" in out and "루프 이음매 이동" in out and "판정:" in out
