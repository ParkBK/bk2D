"""bk2d CLI.

    python -m bk2d build examples/hero.json -o out/hero
"""
import argparse
import json
import sys
from pathlib import Path

from .pipeline import CharacterSpec, build
from .timing import grade


def _print_report(rep: dict):
    print()
    print(f"{'clip':<12}{'frames':>7}{'fps':>6}  {'atlas':<11}{'start→base':>12}{'end→base':>11}{'loop':>9}")
    for c in rep["clips"]:
        q = c["qa"]
        loop = f"{q['loopSeam']:.3f}" if "loopSeam" in q else "-"
        print(f"{c['clip']:<12}{c['frames']:>7}{c['fps']:>6g}  {'x'.join(map(str, c['atlas'])):<11}"
              f"{q['startToBase']:>8.3f} {grade(q['startToBase']):<3}"
              f"{q['endToBase']:>7.3f} {grade(q['endToBase']):<3}{loop:>9}")
        for n in c["notes"]:
            print(f"    - {n}")
    print("\nstart/end→base: 기준 포즈(default 클립 첫 프레임)와의 차이. '튐'이면 Unity 전환 시 포즈가 끊깁니다.")


def _init(folder: Path, name: str):
    """폴더의 mp4 를 찾아 설정 파일을 만든다. 이름에 idle/loop/walk/run 이 들어가면 루프로 본다."""
    folder.mkdir(parents=True, exist_ok=True)
    cfg_path = folder / f"{name}.json"
    if cfg_path.exists():
        print(f"{cfg_path} 가 이미 있습니다. 덮어쓰지 않습니다.")
        return 1
    videos = sorted(p.name for p in folder.glob("*.mp4"))
    loop_words = ("idle", "loop", "walk", "run")
    clips = [{"name": Path(v).stem, "src": v, "loop": any(w in v.lower() for w in loop_words)}
             for v in videos]
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
        "profiles": {
            "battle": {"height": 512},
            "lobby": {"height": 1024, "only": [default], "target": "ui"},
        },
        "clips": clips,
    }
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{cfg_path} 생성 (클립 {len(clips)}개, 기본 클립 {default})")
    for c in clips:
        print(f"  - {c['name']:<16} {'루프' if c['loop'] else '1회'}")
    print("\n다음: python -m bk2d build " + str(cfg_path) + " -o out\\" + name)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bk2d", description="AI 생성 영상 -> Unity 스프라이트 애니메이션")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="캐릭터 설정 JSON 으로 스프라이트시트 세트를 만든다")
    b.add_argument("config", type=Path)
    b.add_argument("-o", "--out", type=Path, required=True)
    b.add_argument("--no-preview", action="store_true")
    b.add_argument("--report", type=Path, help="QA 리포트를 JSON 으로 저장")
    b.add_argument("--profile", help="이 프로필만 빌드 (없으면 profiles 전체를 out/<프로필> 로)")
    i = sub.add_parser("init", help="폴더의 mp4 를 찾아 설정 파일을 자동 생성")
    i.add_argument("folder", type=Path)
    i.add_argument("--name", default="hero")
    args = ap.parse_args(argv)

    if args.cmd == "init":
        return _init(args.folder, args.name)

    if args.cmd == "build":
        if not args.config.exists():
            print(f"설정 파일이 없습니다: {args.config}\n"
                  f"먼저: python -m bk2d init {args.config.parent} --name {args.config.stem}")
            return 1
        profiles = [args.profile] if args.profile else CharacterSpec.profiles(args.config) or [None]
        reports = {}
        for prof in profiles:
            spec = CharacterSpec.load(args.config, prof)
            missing = [str(c.src) for c in spec.clips if not c.src.exists()]
            if missing:
                print("영상 파일이 없습니다:\n  " + "\n  ".join(missing))
                return 1
            out = args.out / prof if prof else args.out
            print(f"\n=== {spec.name} (height {spec.height}, {spec.target}) -> {out}")
            rep = build(spec, out, previews=not args.no_preview)
            _print_report(rep)
            reports[prof or spec.name] = rep
        if args.report:
            args.report.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
