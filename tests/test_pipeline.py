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
    a = np.asarray(Image.open(out / "hero_idle.png").convert("RGBA")).astype(int)
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
