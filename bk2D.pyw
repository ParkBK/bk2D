"""더블클릭으로 bk2D 프로그램 실행 (콘솔 창 없이)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bk2d.gui import main

main()
