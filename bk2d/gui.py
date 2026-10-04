"""bk2D 데스크톱 프로그램: 영상 폴더와 출력 폴더만 고르면 빌드/자동 감시/튕김 검사를 한다.

    python -m bk2d gui        (또는 저장소 루트의 bk2D.pyw 더블클릭)

CLI 기능(cli._build_all, report.check)을 그대로 쓰고, print 출력은 로그 창으로 돌린다.
"""
import contextlib
import json
import os
import queue
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, cli, project, report

SETTINGS = Path.home() / ".bk2d_gui.json"
ALL_PROFILES = "전체"


class _QueueWriter:
    """작업 스레드의 print 를 로그 창으로 보내는 stdout 대체."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)

    def flush(self):
        pass


HELP_PAGES = [
    ("개요", """bk2D 는 AI 로 만든 캐릭터 영상(mp4)을 Unity 게임용 스프라이트 애니메이션으로 바꿔 주는 프로그램입니다.

하는 일
  • 영상에서 프레임을 뽑아 원하는 fps 로 솎아 냅니다.
  • 초록 배경을 지워 투명 PNG 로 만듭니다.
  • 반복 동작(idle 등)은 자연스럽게 이어지는 루프 구간을 찾아 자릅니다.
  • 캐릭터가 떠다니는 흔들림을 보정합니다.
    (머리와 발이 같이 밀린 경우만 보정하고, 치마/머리카락 같은 부분 움직임은 그대로 둡니다.)
  • 모든 클립을 같은 크기·같은 발 위치로 맞춰 스프라이트시트와 JSON 을 만듭니다.
  • 용도별로 여러 벌(예: 전투 512px, 로비 768px UI)을 한 번에 만듭니다.

Unity 프로젝트의 Assets 폴더 안을 출력 폴더로 지정하면,
Unity 가 자동으로 스프라이트·애니메이션·Animator Controller 까지 만들어 줍니다."""),

    ("사용 순서", """1. 영상 폴더 선택
   [찾아보기] 로 mp4 가 들어 있는 폴더를 고릅니다.
   폴더 안의 영상이 목록에 표시됩니다.
   설정 파일(예: hero.json)이 없으면 첫 빌드 때 '캐릭터 이름' 으로 자동 생성됩니다.

2. 출력 폴더 선택
   Unity 프로젝트의 Assets 아래 폴더를 고르세요.
   예) D:\\MyGame\\Assets\\Characters\\hero

3. 빌드 실행
   폴더의 모든 영상을 처리합니다. 진행 상황과 결과 리포트가 아래 로그 창에 나옵니다.
   프로필을 고르면 그 용도만 빌드합니다. (전체 = 설정 파일의 모든 프로필)

4. 자동 감시 (선택)
   [자동 감시 시작] 을 누르면 영상 폴더를 지켜보다가
   mp4 가 추가되거나 바뀔 때마다 자동으로 다시 빌드합니다.
   새 동작 영상을 계속 뽑는 동안 켜 두면 편합니다. 다시 누르면 멈춥니다.

5. 튕김 검사
   출력 결과를 Unity 배치와 같은 방식으로 측정해서
   특정 프레임만 튀는지, 바닥선이 출렁이는지, 루프 이음매가 끊기는지 알려 줍니다.

선택한 폴더는 다음에 프로그램을 열 때 그대로 기억합니다."""),

    ("영상 규칙", """파일 이름 = 클립 이름
  idle_1.mp4  →  idle_1 클립
  attack_2.mp4  →  attack_2 클립

반복 / 1회 재생
  이름에 idle, walk, run, loop 가 들어가면 반복 재생 클립이 됩니다.
  그 밖의 이름은 1회 재생(공격, 피격 등) 클립이 됩니다.

무시할 영상
  이름이 _ 로 시작하면 처리하지 않습니다.
  버리기 아까운 후보 영상 보관용:  _attack_old.mp4

좋은 결과를 위한 영상 조건
  • 단색 초록 배경 (크로마키)
  • 카메라 고정, 캐릭터가 화면 밖으로 크게 잘리지 않게
  • 모든 클립을 같은 캐릭터 이미지에서 시작
  자세한 규칙: docs/GENERATION_GUIDE.md

세부 설정(fps, 루프 길이, 프로필 등)은 영상 폴더의 설정 파일(hero.json)을 메모장으로 고칩니다."""),

    ("Unity 적용", """준비 (프로젝트마다 한 번)
  1. bk2d 폴더의 unity\\Editor\\Bk2dImporter.cs 를 Unity 프로젝트의 Assets/Editor 폴더에 복사합니다.
  2. 2D Sprite 패키지가 필요합니다. (Window > Package Manager > 2D Sprite)

적용
  1. 이 프로그램에서 출력 폴더를 Assets 안으로 지정하고 빌드합니다.
  2. Unity 창을 클릭하면 자동으로 가져옵니다.
     - 스프라이트 슬라이스, 피벗, 텍스처 압축(ASTC) 설정
     - 클립별 .anim 과 캐릭터별 .controller 생성
     자동으로 안 되면: *.character.json 우클릭 > bk2D > Import Character
     자동 가져오기 끄기/켜기: Tools > bk2D > Auto Import

캐릭터 배치
  • 일반(스프라이트): 스프라이트를 Scene 에 끌어다 놓고 Animator 컴포넌트에 .controller 지정
  • UI(로비 등, target=ui): UI > Image 를 만들고 Animator 에 .controller 지정

코드에서 동작 바꾸기
  반복 클립:  animator.SetBool("walk", true)
  1회 클립:  animator.SetTrigger("attack_1")

다시 빌드해도 기존 스프라이트 참조와 손으로 고친 Animator 상태/전이는 유지됩니다."""),

    ("문제 해결", """특정 프레임에서 캐릭터가 옆으로 튐
  → [튕김 검사] 결과의 '좌우 튕김' 항목에 프레임 번호가 나옵니다.
     흔들림 보정 오측정일 가능성이 큽니다. 설정 파일 클립 항목에 "stabilize": "none" 을 넣어 비교해 보세요.

발/다리 끝이 위아래로 출렁임
  → '바닥선 출렁임' 이 1px 을 넘으면 원본 영상에서 캐릭터가 화면 아래로 잘린 경우입니다.
     영상을 다시 뽑을 때 발끝까지 화면 안에 들어오게 하세요.

루프가 끝날 때 툭 끊김
  → '루프 이음매에서 점프' 가 나오면 설정의 loop_seconds 범위를 넓히거나 loop_crossfade 를 늘리세요.

배경 초록이 남거나 머리카락이 비침
  → 설정의 key.tolerance 를 조정합니다. (기본 auto)

'ffmpeg 가 없습니다' 오류
  → ffmpeg 를 설치하고 PATH 에 추가하세요. (winget install Gyan.FFmpeg)

데이터상 튕김이 없는데 Unity 에서 튐
  → 검사 결과가 깨끗하면 Unity 쪽 설정(Image 의 Preserve Aspect, 스케일 등)을 확인하세요."""),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"bk2D {__version__} — AI 영상 → Unity 스프라이트")
        self.geometry("880x720")
        self.minsize(720, 560)

        self.src = tk.StringVar()
        self.out = tk.StringVar()
        self.name = tk.StringVar(value="hero")
        self.profile = tk.StringVar(value=ALL_PROFILES)
        self.previews = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="영상 폴더와 출력 폴더를 선택하세요.")

        self.log_q: queue.Queue = queue.Queue()
        self.ui_q: queue.Queue = queue.Queue()  # 작업 스레드 -> 화면 갱신 요청
        self.busy = False
        self.watch_stop: threading.Event | None = None

        self._build_menu()
        self._build_ui()
        self._load_settings()
        self.src.trace_add("write", lambda *_: self.refresh_clips())
        self.refresh_clips()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_log)

    # ---------- 화면 구성 ----------
    def _build_menu(self):
        bar = tk.Menu(self)
        bar.add_command(label="사용법", command=self.show_help)
        bar.add_command(label="정보", command=lambda: messagebox.showinfo(
            "bk2D", f"bk2D {__version__}\nAI 생성 영상 → Unity 스프라이트 애니메이션 변환"))
        self.config(menu=bar)

    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # 폴더
        box = ttk.LabelFrame(root, text="폴더", padding=8)
        box.pack(fill="x", **pad)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="영상 폴더").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.src).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="찾아보기", command=self._pick_src).grid(row=0, column=2)
        ttk.Label(box, text="출력 폴더").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(box, textvariable=self.out).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(box, text="찾아보기", command=self._pick_out).grid(row=1, column=2, pady=(6, 0))
        ttk.Label(box, text="Unity 프로젝트의 Assets 아래 폴더를 고르면 Unity 에 바로 반영됩니다.",
                  foreground="#666").grid(row=2, column=1, sticky="w", padx=6)

        # 영상 목록
        clips = ttk.LabelFrame(root, text="영상 목록", padding=8)
        clips.pack(fill="x", **pad)
        self.cfg_label = ttk.Label(clips, text="")
        self.cfg_label.pack(anchor="w")
        cols = ("file", "clip", "play", "state")
        self.tree = ttk.Treeview(clips, columns=cols, show="headings", height=6)
        for c, t, w in zip(cols, ("파일", "클립 이름", "재생", "상태"), (220, 160, 80, 260)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", pady=(4, 0))

        # 옵션 + 실행
        opts = ttk.Frame(root)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="캐릭터 이름").pack(side="left")
        ttk.Entry(opts, textvariable=self.name, width=12).pack(side="left", padx=(4, 14))
        ttk.Label(opts, text="프로필").pack(side="left")
        self.prof_box = ttk.Combobox(opts, textvariable=self.profile, width=12, state="readonly",
                                     values=[ALL_PROFILES])
        self.prof_box.pack(side="left", padx=(4, 14))
        ttk.Checkbutton(opts, text="미리보기(gif/webp) 만들기", variable=self.previews).pack(side="left")

        acts = ttk.Frame(root)
        acts.pack(fill="x", **pad)
        self.btn_build = ttk.Button(acts, text="▶ 빌드 실행", command=self.run_build)
        self.btn_watch = ttk.Button(acts, text="자동 감시 시작", command=self.toggle_watch)
        self.btn_check = ttk.Button(acts, text="튕김 검사", command=self.run_check)
        self.btn_build.pack(side="left")
        self.btn_watch.pack(side="left", padx=6)
        self.btn_check.pack(side="left")
        ttk.Button(acts, text="출력 폴더 열기", command=lambda: self._open(self.out.get())).pack(side="right")
        ttk.Button(acts, text="영상 폴더 열기", command=lambda: self._open(self.src.get())).pack(side="right", padx=6)

        st = ttk.Frame(root)
        st.pack(fill="x", **pad)
        self.bar = ttk.Progressbar(st, mode="indeterminate", length=160)
        self.bar.pack(side="left")
        ttk.Label(st, textvariable=self.status).pack(side="left", padx=8)

        # 로그
        logf = ttk.LabelFrame(root, text="로그", padding=4)
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, wrap="none", font=("Consolas", 9), state="disabled")
        ys = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        xs = ttk.Scrollbar(logf, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)
        ttk.Button(logf, text="로그 지우기", command=self._clear_log).grid(row=2, column=0, sticky="e", pady=(4, 0))

    # ---------- 사용법 팝업 ----------
    def show_help(self):
        if getattr(self, "_help", None) and self._help.winfo_exists():
            self._help.lift()
            return
        win = self._help = tk.Toplevel(self)
        win.title("bk2D 사용법")
        win.geometry("700x560")
        win.transient(self)
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        for title, body in HELP_PAGES:
            frame = ttk.Frame(nb)
            txt = tk.Text(frame, wrap="word", font=("Malgun Gothic", 10), padx=12, pady=10,
                          relief="flat", background=self.cget("background"))
            sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            txt.insert("1.0", body)
            txt.configure(state="disabled")
            txt.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            nb.add(frame, text=title)
        ttk.Button(win, text="닫기", command=win.destroy).pack(pady=(0, 8))

    # ---------- 폴더 / 목록 ----------
    def _pick_src(self):
        d = filedialog.askdirectory(title="영상 폴더 선택", initialdir=self.src.get() or None)
        if d:
            self.src.set(os.path.normpath(d))

    def _pick_out(self):
        d = filedialog.askdirectory(title="출력 폴더 선택 (Unity Assets 아래 권장)",
                                    initialdir=self.out.get() or None)
        if d:
            self.out.set(os.path.normpath(d))

    def _config(self) -> Path | None:
        folder = Path(self.src.get())
        return project.find_config(folder) if folder.is_dir() else None

    def refresh_clips(self):
        self.tree.delete(*self.tree.get_children())
        folder = Path(self.src.get())
        if not self.src.get() or not folder.is_dir():
            self.cfg_label.config(text="영상 폴더를 선택하세요.")
            self.prof_box.config(values=[ALL_PROFILES])
            return
        cfg = self._config()
        known = {}
        profiles = []
        if cfg:
            try:
                raw = json.loads(cfg.read_text(encoding="utf-8-sig"))
                known = {c["src"]: c for c in raw.get("clips", [])}
                profiles = list(raw.get("profiles", {}))
                self.name.set(raw.get("name", self.name.get()))
                self.cfg_label.config(text=f"설정 파일: {cfg.name}   (기본 클립: {raw.get('default', '-')})")
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self.cfg_label.config(text=f"설정 파일 {cfg.name} 을 읽을 수 없습니다: {e}")
        else:
            self.cfg_label.config(text="설정 파일 없음 → 첫 빌드 때 '캐릭터 이름' 으로 자동 생성됩니다.")
        self.prof_box.config(values=[ALL_PROFILES] + profiles)
        if self.profile.get() not in [ALL_PROFILES] + profiles:
            self.profile.set(ALL_PROFILES)

        vids = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".mp4")
        for p in vids:
            if p.name.startswith("_"):
                self.tree.insert("", "end", values=(p.name, "-", "-", "무시 (이름이 _ 로 시작)"))
                continue
            c = known.get(p.name) or project.clip_entry(p.name)
            state = "설정에 있음" if p.name in known else "새 영상 → 빌드 때 자동 추가"
            self.tree.insert("", "end", values=(p.name, c["name"], "반복" if c.get("loop") else "1회", state))
        for src, c in known.items():
            if not (folder / src).exists():
                self.tree.insert("", "end", values=(src, c["name"], "-", "영상 없음 → 빌드 때 설정에서 제거"))
        if not vids:
            self.tree.insert("", "end", values=("(mp4 없음)", "", "", "이 폴더에 영상을 넣으세요"))

    # ---------- 작업 실행 ----------
    def _validate(self, need_src=True, need_out=True) -> bool:
        if need_src and not Path(self.src.get()).is_dir():
            messagebox.showwarning("bk2D", "영상 폴더를 선택하세요.")
            return False
        if need_out and not self.out.get():
            messagebox.showwarning("bk2D", "출력 폴더를 선택하세요.")
            return False
        return True

    def _job_opts(self) -> dict:
        """작업 스레드는 tk 변수를 읽으면 안 되므로, 시작 시점의 입력값을 메인 스레드에서 복사해 넘긴다."""
        prof = self.profile.get()
        return {"src": Path(self.src.get()), "out": Path(self.out.get()),
                "name": self.name.get().strip() or "hero",
                "profile": None if prof == ALL_PROFILES else prof, "previews": self.previews.get()}

    @staticmethod
    def _ensure_config(o: dict) -> Path:
        cfg = project.find_config(o["src"])
        if cfg is None:
            cli._init(o["src"], o["name"])
            cfg = project.find_config(o["src"])
        return cfg

    def _start(self, label: str, job, on_done=None):
        """job 을 작업 스레드에서 실행. print 는 로그 창으로."""
        if self.busy:
            return
        self.busy = True
        self._set_buttons()
        self.status.set(label)
        self.bar.start(12)

        def work():
            ok = True
            with contextlib.redirect_stdout(_QueueWriter(self.log_q)):
                try:
                    job()
                except Exception as e:
                    ok = False
                    traceback.print_exc()
                    print(f"\n[실패] {e}")
            self._ui(lambda: self._finish(ok, on_done))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, ok: bool, on_done):
        self.busy = False
        self.bar.stop()
        self.bar["value"] = 0
        self._set_buttons()
        self.refresh_clips()
        if on_done:
            on_done(ok)

    def _set_buttons(self):
        watching = self.watch_stop is not None
        state = "disabled" if self.busy else "normal"
        self.btn_build.config(state=state)
        self.btn_check.config(state=state)
        self.btn_watch.config(state="normal" if watching or not self.busy else "disabled",
                              text="자동 감시 중지" if watching else "자동 감시 시작")

    @staticmethod
    def _build_once(cfg: Path, o: dict):
        cli._build_all(cfg, o["out"], o["profile"], o["previews"], sync=True)

    def run_build(self):
        if not self._validate():
            return
        self._save_settings()
        o = self._job_opts()

        def job():
            cfg = self._ensure_config(o)
            started = time.time()
            self._build_once(cfg, o)
            print(f"\n[{time.strftime('%H:%M:%S')}] 빌드 완료 ({time.time() - started:.0f}초) → {o['out']}")

        def done(ok):
            self.status.set("빌드 완료. Unity 창을 클릭하면 자동으로 가져옵니다." if ok
                            else "빌드 실패 — 로그를 확인하세요.")
            if not ok:
                messagebox.showerror("bk2D", "빌드에 실패했습니다. 로그 창의 [실패] 내용을 확인하세요.")

        self._start("빌드 중... (영상 길이에 따라 수십 초~몇 분)", job, done)

    def run_check(self):
        if not self._validate(need_src=False):
            return
        out = Path(self.out.get())
        if not any(out.rglob("*.character.json")):
            messagebox.showinfo("bk2D", "출력 폴더에 빌드 결과가 없습니다. 먼저 빌드하세요.")
            return

        def job():
            print(f"\n== 튕김 검사: {out}")
            print(report.check(out))

        def done(ok):
            self.status.set("검사 완료 — 각 클립의 '판정' 줄을 확인하세요." if ok else "검사 실패")

        self._start("튕김 검사 중...", job, done)

    def toggle_watch(self):
        if self.watch_stop is not None:
            self.watch_stop.set()
            self.status.set("감시를 멈추는 중...")
            return
        if not self._validate():
            return
        self._save_settings()
        stop = self.watch_stop = threading.Event()
        o = self._job_opts()
        folder = o["src"]

        def job():
            cfg = self._ensure_config(o)
            print(f"감시 시작: {folder}  (출력 {o['out']})")
            print("mp4 를 넣거나 바꾸면 자동으로 빌드합니다. 이름이 _ 로 시작하면 무시합니다.\n")

            def run():
                try:
                    self._build_once(cfg, o)
                    print(f"\n[{time.strftime('%H:%M:%S')}] 완료. 다음 변경을 기다립니다...")
                except Exception as e:  # 한 번 실패해도 감시는 계속
                    traceback.print_exc()
                    print(f"\n[{time.strftime('%H:%M:%S')}] 빌드 실패: {e}\n파일을 고치면 다시 시도합니다...")
                self._ui(self.refresh_clips)

            run()
            last = project.snapshot(folder)
            while not stop.wait(2.0):
                snap = project.snapshot(folder)
                if snap == last:
                    continue
                # 다운로드/복사 중인 파일을 피하려고 크기가 멈출 때까지 기다린다.
                while not stop.wait(2.0):
                    again = project.snapshot(folder)
                    if again == snap:
                        break
                    snap = again
                if stop.is_set():
                    break
                changed = sorted(k for k in set(snap) | set(last) if snap.get(k) != last.get(k))
                print(f"\n[{time.strftime('%H:%M:%S')}] 변경 감지: {', '.join(changed)}")
                self._ui(lambda: self.status.set("변경 감지 — 빌드 중..."))
                run()
                self._ui(lambda: self.status.set("자동 감시 중 — 영상 폴더에 mp4 를 넣으세요."))
                last = project.snapshot(folder)
            print("\n감시 종료")

        def done(ok):
            self.watch_stop = None
            self._set_buttons()
            self.status.set("감시 종료" if ok else "감시 중 오류 — 로그를 확인하세요.")

        self._start("자동 감시 중 — 영상 폴더에 mp4 를 넣으세요.", job, done)
        self._set_buttons()

    # ---------- 로그 ----------
    def _ui(self, fn):
        """작업 스레드에서 화면을 직접 건드리지 않고 메인 스레드에 맡긴다."""
        self.ui_q.put(fn)

    def _drain_log(self):
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        chunks = []
        try:
            while True:
                chunks.append(self.log_q.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            self.log.configure(state="normal")
            self.log.insert("end", "".join(chunks))
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(100, self._drain_log)

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ---------- 기타 ----------
    def _open(self, path: str):
        if path and Path(path).is_dir():
            os.startfile(path)  # Windows 탐색기
        else:
            messagebox.showinfo("bk2D", "폴더가 아직 없습니다.")

    def _load_settings(self):
        try:
            s = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.src.set(s.get("src", ""))
        self.out.set(s.get("out", ""))
        self.previews.set(s.get("previews", True))
        self.profile.set(s.get("profile", ALL_PROFILES))

    def _save_settings(self):
        s = {"src": self.src.get(), "out": self.out.get(), "previews": self.previews.get(),
             "profile": self.profile.get()}
        try:
            SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _on_close(self):
        if self.busy and not messagebox.askyesno("bk2D", "작업이 진행 중입니다. 종료할까요?"):
            return
        if self.watch_stop:
            self.watch_stop.set()
        self._save_settings()
        self.destroy()


def main():
    App().mainloop()
    return 0
