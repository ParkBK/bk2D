"""bk2d CLI.

    python -m bk2d init  work                      # 폴더의 mp4 로 설정 파일 생성
    python -m bk2d build work\\hero.json -o out\\hero
    python -m bk2d watch work -o C:\\MyGame\\Assets\\Characters\\hero   # 영상 넣으면 자동 빌드
"""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from . import project, report
from .pipeline import CharacterSpec, build
from .timing import grade


def _print_report(rep: dict):
    print()
    print(f"{'clip':<12}{'frames':>7}{'fps':>6}  {'pages':<22}{'start→base':>12}{'end→base':>11}{'loop':>9}")
    for c in rep["clips"]:
        q = c["qa"]
        loop = "pingpong" if c.get("playback") == "pingpong" else (
            f"{q['loopSeam']:.3f}" if "loopSeam" in q else "-")
        pages = ", ".join(f"{w}x{h}" for w, h in c["pages"])
        print(f"{c['clip']:<12}{c['frames']:>7}{c['fps']:>6g}  {pages:<22}"
              f"{q['startToBase']:>8.3f} {grade(q['startToBase']):<3}"
              f"{q['endToBase']:>7.3f} {grade(q['endToBase']):<3}{loop:>9}")
        for n in c["notes"]:
            print(f"    - {n}")
    print("\nstart/end→base: 기준 포즈(default 클립 첫 프레임)와의 차이. '튐'이면 Unity 전환 시 포즈가 끊깁니다.")


def _print_cost(name: str, rep: dict):
    rows = [{"character": name, "clip": c["clip"], "frames": c["frames"], **c["cost"]} for c in rep["clips"]]
    print()
    print(report.format_rows(rows, f"[용량] {name}"))


def _init(folder: Path, name: str):
    """폴더의 mp4 를 찾아 설정 파일을 만든다."""
    folder.mkdir(parents=True, exist_ok=True)
    cfg_path = folder / f"{name}.json"
    if cfg_path.exists():
        print(f"{cfg_path} 가 이미 있습니다. 덮어쓰지 않습니다.")
        return 1
    clips = [project.clip_entry(p.name) for p in sorted(folder.iterdir()) if project.is_video(p)]
    if not clips:
        clips = [{"name": "idle_1", "src": "idle_1.mp4", "loop": True}]
        print(f"{folder} 에 mp4 가 없어 예시 항목(idle_1.mp4)으로 만듭니다. 영상을 넣고 이름을 맞추세요.")
    default = next((c["name"] for c in clips if "idle" in c["name"].lower()), clips[0]["name"])
    cfg = {
        "name": name, "default": default, "fps": 10,
        "key": {"mode": "chroma", "color": "auto", "tolerance": "auto"},
        "erase": [[0.62, 0.90, 1.0, 1.0]],
        "despeckle": 64,
        "loop_seconds": [1.0, 2.5],
        "max_frames": 16,
        "pingpong": False,
        "max_texture": 2048,
        "unity": {"format": "ASTC_6x6", "platforms": ["Android", "iPhone"]},
        "profiles": {
            "battle": {"height": 512},
            "lobby": {"height": 768, "only": [default], "target": "ui"},
        },
        "clips": clips,
    }
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{cfg_path} 생성 (클립 {len(clips)}개, 기본 클립 {default})")
    for c in clips:
        print(f"  - {c['name']:<16} {'루프' if c['loop'] else '1회'}")
    print("\n다음: python -m bk2d build " + str(cfg_path) + " -o out\\" + name)
    return 0


def _preview_dir(cfg_path: Path, out: Path, prof: str | None) -> Path | None:
    """출력이 Unity Assets 안이면 미리보기(webp/gif)는 작업 폴더로 뺀다 (Unity 에 잡파일 방지)."""
    if "Assets" in out.resolve().parts:
        return cfg_path.parent / "_preview" / (prof or "default")
    return None


def _build_all(cfg_path: Path, out: Path, only_profile: str | None, previews: bool, sync: bool):
    if sync:
        added, removed = project.sync_clips(cfg_path)
        for n in added:
            print(f"+ 새 클립 추가: {n}")
        for n in removed:
            print(f"- 영상이 없어 클립 제거: {n}")
    profiles = [only_profile] if only_profile else CharacterSpec.profiles(cfg_path) or [None]
    reports = {}
    for prof in profiles:
        spec = CharacterSpec.load(cfg_path, prof)
        missing = [str(c.src) for c in spec.clips if not c.src.exists()]
        if missing:
            raise FileNotFoundError("영상 파일이 없습니다:\n  " + "\n  ".join(missing))
        dst = out / prof if prof else out
        print(f"\n=== {spec.name} (height {spec.height}, {spec.target}) -> {dst}")
        rep = build(spec, dst, previews=previews, preview_dir=_preview_dir(cfg_path, out, prof))
        _print_report(rep)
        _print_cost(spec.name, rep)
        reports[prof or spec.name] = rep
    if len(reports) > 1:
        rows = [{"character": r["name"], "clip": c["clip"], "frames": c["frames"], **c["cost"]}
                for r in reports.values() for c in r["clips"]]
        print()
        print(report.format_rows(rows, "[용량] 프로필 전체"))
    return reports


def _watch(folder: Path, out: Path, interval: float, previews: bool, name: str):
    cfg_path = project.find_config(folder)
    if cfg_path is None:
        _init(folder, name)
        cfg_path = project.find_config(folder)
    print(f"감시 시작: {folder.resolve()}  (설정 {cfg_path.name}, 출력 {out})")
    print("mp4 를 넣거나 바꾸면 자동으로 빌드합니다. 이름이 _ 로 시작하면 무시. 종료: Ctrl+C\n")

    def run():
        try:
            _build_all(cfg_path, out, None, previews, sync=True)
            print(f"\n[{time.strftime('%H:%M:%S')}] 완료. 다음 변경을 기다립니다...")
        except Exception as e:  # 한 번 실패해도 감시는 계속
            traceback.print_exc()
            print(f"\n[{time.strftime('%H:%M:%S')}] 빌드 실패: {e}\n파일을 고치면 다시 시도합니다...")

    run()
    last = project.snapshot(folder)
    try:
        while True:
            time.sleep(interval)
            snap = project.snapshot(folder)
            if snap == last:
                continue
            # 다운로드/복사 중인 파일을 피하려고 크기가 멈출 때까지 기다린다.
            while True:
                time.sleep(interval)
                again = project.snapshot(folder)
                if again == snap:
                    break
                snap = again
            changed = sorted(k for k in set(snap) | set(last) if snap.get(k) != last.get(k))
            print(f"\n[{time.strftime('%H:%M:%S')}] 변경 감지: {', '.join(changed)}")
            run()
            last = project.snapshot(folder)  # 빌드 중 우리가 쓴 설정 파일 변경은 무시
    except KeyboardInterrupt:
        print("\n감시 종료")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bk2d", description="AI 생성 영상 -> Unity 스프라이트 애니메이션")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="폴더의 mp4 를 찾아 설정 파일을 자동 생성")
    i.add_argument("folder", type=Path)
    i.add_argument("--name", default="hero")

    b = sub.add_parser("build", help="캐릭터 설정 JSON 으로 스프라이트시트 세트를 만든다")
    b.add_argument("config", type=Path)
    b.add_argument("-o", "--out", type=Path, required=True)
    b.add_argument("--no-preview", action="store_true")
    b.add_argument("--report", type=Path, help="QA 리포트를 JSON 으로 저장")
    b.add_argument("--profile", help="이 프로필만 빌드 (없으면 profiles 전체를 out/<프로필> 로)")
    b.add_argument("--no-sync", action="store_true", help="폴더의 mp4 와 clips 를 동기화하지 않음")

    w = sub.add_parser("watch", help="폴더를 감시하다가 영상이 추가/변경되면 자동 빌드")
    w.add_argument("folder", type=Path)
    w.add_argument("-o", "--out", type=Path, required=True,
                   help="출력 폴더. Unity 프로젝트의 Assets 아래로 지정하면 바로 반영됨")
    w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--no-preview", action="store_true")
    w.add_argument("--name", default="hero", help="설정 파일이 없을 때 만들 캐릭터 이름")

    st = sub.add_parser("stats", help="출력 폴더(들)의 프레임 수/아틀라스/용량/메모리 비교")
    st.add_argument("folders", type=Path, nargs="+")

    args = ap.parse_args(argv)

    if args.cmd == "stats":
        for f in args.folders:
            print(report.format_rows(report.scan(f), f"== {f}"))
            print()
        return 0

    if args.cmd == "init":
        return _init(args.folder, args.name)

    if args.cmd == "watch":
        return _watch(args.folder, args.out, args.interval, not args.no_preview, args.name)

    if args.cmd == "build":
        if not args.config.exists():
            print(f"설정 파일이 없습니다: {args.config}\n"
                  f"먼저: python -m bk2d init {args.config.parent} --name {args.config.stem}")
            return 1
        try:
            reports = _build_all(args.config, args.out, args.profile, not args.no_preview,
                                 sync=not args.no_sync)
        except FileNotFoundError as e:
            print(e)
            return 1
        if args.report:
            args.report.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
