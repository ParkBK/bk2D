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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bk2d", description="AI 생성 영상 -> Unity 스프라이트 애니메이션")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="캐릭터 설정 JSON 으로 스프라이트시트 세트를 만든다")
    b.add_argument("config", type=Path)
    b.add_argument("-o", "--out", type=Path, required=True)
    b.add_argument("--no-preview", action="store_true")
    b.add_argument("--report", type=Path, help="QA 리포트를 JSON 으로 저장")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        spec = CharacterSpec.load(args.config)
        rep = build(spec, args.out, previews=not args.no_preview)
        _print_report(rep)
        if args.report:
            args.report.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
