"""작업 폴더 관리: 폴더의 mp4 와 설정 파일(clips) 동기화.

규칙
- 파일 이름(확장자 제외) = 클립 이름
- 이름에 idle/loop/walk/run 이 들어가면 루프, 아니면 1회 재생
- 이름이 _ 로 시작하는 mp4 는 무시 (후보 보관용)
"""
import json
from pathlib import Path

LOOP_WORDS = ("idle", "loop", "walk", "run")


def is_video(p: Path) -> bool:
    return p.suffix.lower() == ".mp4" and not p.name.startswith("_")


META_TEMPLATE = {"service": "", "model": "", "seed": "", "prompt": "", "reference": ""}


def clip_entry(filename: str) -> dict:
    stem = Path(filename).stem
    return {"name": stem, "src": filename, "loop": any(w in stem.lower() for w in LOOP_WORDS),
            "meta": dict(META_TEMPLATE)}


def find_config(folder: Path) -> Path | None:
    for p in sorted(folder.glob("*.json")):
        try:
            if "clips" in json.loads(p.read_text(encoding="utf-8-sig")):
                return p
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return None


def sync_clips(cfg_path: Path) -> tuple[list[str], list[str]]:
    """폴더에 새로 생긴 mp4 는 clips 에 추가, 파일이 사라진 클립은 제거. 바뀌면 설정 파일에 저장."""
    raw = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    folder = cfg_path.parent
    known = {c["src"] for c in raw["clips"]}
    added = [clip_entry(p.name) for p in sorted(folder.iterdir())
             if is_video(p) and p.name not in known]
    removed = [c["name"] for c in raw["clips"] if not (folder / c["src"]).exists()]
    if not added and not removed:
        return [], []
    raw["clips"] = [c for c in raw["clips"] if c["name"] not in removed] + added
    names = [c["name"] for c in raw["clips"]]
    if raw.get("default") not in names and names:
        # 허브는 idle > 다른 루프 클립 > 첫 클립 순으로 고른다.
        loops = [c["name"] for c in raw["clips"] if c.get("loop")]
        raw["default"] = next((n for n in names if "idle" in n.lower()), (loops or names)[0])
    # 프로필 only 에서 사라진 클립 정리
    for prof in raw.get("profiles", {}).values():
        if "only" in prof:
            prof["only"] = [n for n in prof["only"] if n in names] or [raw["default"]]
    cfg_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return [c["name"] for c in added], removed


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """감시용: mp4 와 json 의 (크기, 수정시각)."""
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
            for p in folder.iterdir() if p.suffix.lower() in (".mp4", ".json")}
