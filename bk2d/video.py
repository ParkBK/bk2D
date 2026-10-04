"""ffmpeg 기반 프레임 추출."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} 를 찾을 수 없습니다. ffmpeg 를 설치하고 PATH 에 추가하세요.")


def probe_fps(src: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "json", str(src)],
        check=True, capture_output=True, text=True,
    ).stdout
    num, den = json.loads(out)["streams"][0]["r_frame_rate"].split("/")
    return float(num) / float(den)


def extract_frames(src: Path, out_dir: Path, max_height: int) -> list[Path]:
    """영상의 모든 프레임을 PNG 로 추출한다. max_height 보다 크면 축소한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"scale=-2:'min({max_height},ih)':flags=lanczos"
    base = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf]
    out = str(out_dir / "f_%05d.png")
    # 프레임 복제/누락 없이 원본 프레임 그대로 추출.
    # ffmpeg 5.1+ 는 -fps_mode, 7.0+ 에서 -vsync 제거됨. 구버전 대비 폴백.
    try:
        subprocess.run(base + ["-fps_mode", "passthrough", out], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.run(base + ["-vsync", "0", out], check=True)
    frames = sorted(out_dir.glob("f_*.png"))
    if not frames:
        raise RuntimeError(f"{src} 에서 프레임을 추출하지 못했습니다.")
    return frames


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def load_thumb(path: Path, size: int = 64) -> np.ndarray:
    """루프/전환 비교용 저해상도 float 이미지."""
    img = Image.open(path).convert("RGB").resize((size, size), Image.BILINEAR)
    return np.asarray(img, dtype=np.float32) / 255.0
