# bk2D

AI 일러스트 → AI 영상(Higgsfield 등) → **Unity 용 투명 스프라이트 애니메이션** 변환 파이프라인.

영상 생성 서비스는 계속 바뀌므로 생성 단계는 수동으로 두고, 어떤 서비스든 mp4 만 받아서
게임에 바로 쓸 수 있는 형태로 만드는 후처리에 집중합니다.

```
mp4 (클립별) ─▶ 프레임 추출 ─▶ 루프 구간 탐지(idle) ─▶ fps 솎기 ─▶ 크로마키 + 디스필
            ─▶ 클립 간 스케일/피벗 정렬 ─▶ 공통 셀 스프라이트시트 + JSON ─▶ QA 리포트
                                                              │
                                     Unity: Sprite 슬라이스 + .anim + AnimatorController
```

**먼저 읽을 것: [docs/GENERATION_GUIDE.md](docs/GENERATION_GUIDE.md)** — 원본 영상 생성 규칙. 결과 품질의 대부분이 여기서 결정됩니다.

## 프로그램으로 쓰기 (GUI)

`bk2D.pyw` 를 더블클릭하거나 `python -m bk2d gui` 로 실행합니다.

1. **영상 폴더**: mp4 가 들어 있는 폴더 선택 → 영상 목록과 클립 이름/반복 여부가 표시됨 (설정 파일이 없으면 첫 빌드 때 자동 생성)
2. **출력 폴더**: Unity 프로젝트의 `Assets` 아래 폴더 선택
3. **빌드 실행** / **자동 감시 시작**(영상을 넣을 때마다 자동 빌드) / **튕김 검사**
4. 상단 **사용법** 메뉴를 누르면 기능·사용 순서·영상 규칙·Unity 적용·문제 해결을 팝업으로 볼 수 있습니다.

선택한 폴더와 옵션은 `%USERPROFILE%\.bk2d_gui.json` 에 저장되어 다음 실행 때 그대로 열립니다.

### exe 로 만들기

`build_exe.bat` 를 더블클릭하면 `dist\bk2D.exe` 하나가 만들어집니다 (PyInstaller 자동 설치, 약 60MB).
Python 없이 실행되지만 **ffmpeg 는 따로 설치되어 PATH 에 있어야** 합니다. 코드를 고친 뒤에는 다시 실행해 exe 를 갱신하세요.

## 빠른 작업 흐름 (watch 모드)

새 동작 영상을 뽑을 때마다 자동으로 Unity 까지 반영:

```bash
python -m bk2d watch work -o C:\MyGame\Assets\Characters\hero
```

1. Higgsfield 에서 받은 mp4 를 `work` 폴더에 **클립 이름으로** 저장 (`attack_2.mp4`, `walk.mp4` ...)
   - 이름에 idle/walk/run/loop 가 있으면 반복, 아니면 1회 재생
   - 이름이 `_` 로 시작하면 무시 (후보 보관용: `_attack_old.mp4`)
2. 감시 중인 터미널이 자동으로 설정에 추가 → 전투/로비 두 벌 빌드 → Unity 폴더에 출력, QA 리포트 출력
3. Unity 창으로 돌아가면 자동 임포트, 컨트롤러에 새 액션 상태(Trigger/Bool) 자동 추가
   - 자동 임포트 끄기/켜기: Tools > bk2D > Auto Import
   - 기존 상태·전이는 건드리지 않음 (손으로 고친 설정 유지)
4. 미리보기(webp/gif)는 Unity 에 안 들어가도록 `work/_preview/` 에 저장

`build` 도 실행 시 폴더의 mp4 와 clips 를 자동 동기화한다 (`--no-sync` 로 끔).

## 설치

```bash
pip install -r requirements.txt   # numpy, pillow
# ffmpeg / ffprobe 가 PATH 에 있어야 함
```

## 사용

1. 캐릭터 설정 파일 작성 (`examples/hero.json` 참고, `src` 는 설정 파일 기준 상대경로)
2. 빌드

```bash
python -m bk2d build examples/hero.json -o out/hero
```

출력:

| 파일 | 내용 |
|---|---|
| `hero_<clip>.png` | 클립별 스프라이트시트 (모든 클립이 같은 셀 크기·피벗 공유) |
| `hero_<clip>.json` | 프레임 rect(Unity 좌표, 좌하단 원점), fps, loop, pivot, QA 점수 |
| `hero.character.json` | 클립 목록, 기본(허브) 클립 |
| `preview/*.webp, *.gif` | 확인용 미리보기 |

3. 터미널 QA 리포트 확인

```
clip         frames   fps  atlas        start→base   end→base     loop
idle             20    12  1208x1168     0.000 OK   0.024 OK      0.024
attack           12    12  968x876       0.024 OK   0.031 OK          -
bad              12    12  968x876       0.001 OK   0.169 튐          -
```

- `start→base` / `end→base`: 기준 포즈(기본 클립 첫 프레임)와의 차이. **튐**이면 Unity 에서 상태 전환 시 포즈가 끊깁니다 → 재생성 대상.
- `loop`: 루프 이음매 차이.
- 등급 기준(0.04 / 0.08)은 합성 테스트 영상으로 정한 값입니다. 실제 생성 영상으로 다시 보정해야 합니다.

### 설정 항목 메모

- `key.tolerance`: `"auto"`(기본)면 배경 초록의 채도에 맞춰 자동. 탁한 초록 배경에서 고정값 `[20, 45]` 을 쓰면 검은 머리·어두운 옷까지 반투명해진다(실제 생성 영상에서 확인됨). 수동으로 줄 때는 `[low, high]`.
- `erase`: 강제로 투명 처리할 영역 목록. 원본 프레임 기준 정규화 좌표 `[x0, y0, x1, y1]` (0~1, 좌상단 원점). 워터마크·UI 잔상 제거용. 캐릭터와 겹치는 영역에 쓰면 캐릭터도 지워진다.
- `profiles`: 같은 영상으로 용도별 여러 벌을 한 번에 빌드. 각 프로필 값이 상위 값을 덮어쓰고, `only` 로 클립을 고르며, 결과는 `-o 폴더/<프로필>` 에 `<name>_<프로필>` 이름으로 나온다. `--profile battle` 로 하나만 빌드 가능.
  ```json
  "profiles": {
    "battle": { "height": 512 },
    "lobby":  { "height": 1024, "only": ["idle_1"], "target": "ui" }
  }
  ```
- `max_frames`: 저장 프레임 수 상한 (전역 / 프로필 / 클립 / 프로필의 `clip_overrides`). 루프는 상한 안에서 루프 지점을 다시 찾고, 1회 클립은 길이를 유지한 채 fps 를 낮춘다(끝 프레임 보존).
- `loop_seconds`: `[최소, 최대]`. 최대값은 **하드 상한** — 루프 재생 길이는 항상 `loop_seconds[1] * fps` 프레임 이하.
- `pingpong`: 루프를 정방향+역방향 재생(0→반환점→0)으로. 저장 프레임이 재생 프레임의 절반+1. 반환점은 기준 포즈와 가장 먼(동작 끝점) 프레임. 숨쉬기처럼 대칭인 동작에 적합, 머리카락이 한 방향으로 흐르는 동작은 역재생이 어색할 수 있음.
- `loop_crossfade`: 루프 시작 n 프레임에 "루프 끝 다음에 원본 영상에서 이어지는 프레임"을 서서히 섞어 마지막→처음 전환을 실제 연속 동작으로 만든다. 역재생(핑퐁) 없이 이음매 튐 제거. 2~3 권장. 프레임 수는 늘지 않음. 섞이는 몇 프레임에 약한 잔상이 생길 수 있다.
- 프로필 간 동작 일치: 같은 클립은 프로필이 달라도 루프 구간이 같아야 같은 움직임으로 보인다. 프로필마다 `max_frames`/`loop_seconds`/`pingpong` 을 다르게 주면 동작 자체가 달라지며, 빌드 시 경고가 뜬다. 해상도(`height`)만 다르게 주는 것이 원칙. 메모리를 줄이려면 그 프로필의 `fps` 만 낮추는 쪽이 동작 구간을 유지한다.
- `max_texture`: 아틀라스 한 장 최대 크기(기본 2048, 모바일 호환). 넘으면 `_p0.png, _p1.png ...` 여러 장으로 분할. **분할은 "한 장이 너무 커서 못 올리는" 문제만 해결하고 총 메모리는 줄이지 않는다** — 메모리는 height/fps/프레임 수로 줄인다.
- `trim`: 프레임별 투명 여백 제거 + 타이트 패킹(기본 켜짐). 각 스프라이트의 피벗이 공통 발밑 지점을 가리키도록 프레임별로 저장되어 Unity 에서 위치가 그대로 유지된다. `target: "ui"` 에서는 자동으로 꺼짐(UI Image 는 스프라이트 크기에 맞춰 늘어나기 때문).
- `unity`: Unity 임포트 시 텍스처 설정. `{"format": "ASTC_6x6", "max_texture_size": 2048, "platforms": ["Android", "iPhone"]}` — 프로필별로 다르게 줄 수 있다.
- 클립 `meta`: 생성 메타데이터 수동 입력 `{"service", "model", "seed", "prompt", "reference"}`. 클립 JSON 에 툴 버전, 원본 파일명/SHA1, 빌드 시각과 함께 기록된다.
- `target`: `"sprite"`(기본, SpriteRenderer) 또는 `"ui"`(Canvas 의 UI Image). 생성되는 .anim 의 대상 컴포넌트가 달라진다.
- `stabilize`: 프레임 간 흔들림 보정. `"feet"`(발 영역을 첫 프레임에 고정), `"body"`(전신 기준), `"none"`. 전역 또는 클립별. 기본은 루프 클립 feet, 1회 클립 none (공격 시 앞으로 내딛는 등 의도된 이동을 지우지 않도록). 프레임마다 디테일이 다시 그려지는 꿈틀거림은 이동이 아니라 보정되지 않는다.
- `height`: 출력 캐릭터 높이(px). `"source"` 면 기준 클립 원본 크기 그대로(축소 없음, 화질 최대·메모리 최대). 게임 화면에 표시되는 최대 크기보다 작으면 Unity 에서 확대되어 흐려진다.
- `loop_seconds`: 루프 길이 탐색 범위 `[최소, 최대]` 초 (전역 또는 클립별). 없으면 영상 후반 40% 에서 찾아 5초짜리 루프가 나올 수 있음 → 메모리 과다. 게임 idle 은 보통 1.5~3초.
- `despeckle`: 몸통과 떨어진 이 면적(px) 미만 덩어리 제거. 셀이 불필요하게 커지는 원인(가장자리 잡티) 제거용. scipy 필요.
- 리포트의 "화면 끝에 닿음" 경고: 캐릭터가 영상 밖으로 잘렸을 가능성. 잘린 부분은 복구 불가 → 여백 있는 기준 이미지로 재생성.

## Unity

0. **필수 패키지 설치**: Window > Package Manager > Unity Registry > `2D Sprite` 설치
   (3D 템플릿 프로젝트에는 기본으로 없음. 없으면 `CS0234: 'Sprites' does not exist in the namespace 'UnityEditor.U2D'` 컴파일 에러)
1. `unity/Editor/Bk2dImporter.cs` 를 프로젝트의 `Assets/.../Editor/` 에 복사
2. `out/hero` 폴더를 `Assets/` 아래로 복사
3. `hero.character.json` 우클릭 → **bk2D > Import Character**

생성물:
- 스프라이트 슬라이스 (공통 커스텀 피벗 = 기준 포즈 발밑 중앙)
- 클립별 `.anim` (`target` 이 sprite 면 SpriteRenderer, ui 면 UI Image 대상)
- `hero.controller`: 기본 클립이 허브인 상태머신
  - 루프 아님(attack, hit) → **Trigger** 파라미터, 끝나면 허브로 복귀
  - 루프(walk) → **Bool** 파라미터, false 가 되면 허브로 복귀
  - 컨트롤러가 이미 있으면 덮어쓰지 않고 클립만 갱신 (재임포트해도 spriteID 유지)

> 이 임포터는 Unity 에서 컴파일 검증을 아직 하지 않았습니다. 첫 사용 시 콘솔 에러를 확인하세요.

## 용량 확인 / 비교

빌드가 끝나면 프로필별로 PNG 용량, 비압축 RGBA 메모리, ASTC 6x6 / 8x8 추정 메모리를 출력한다.
기존 출력과 비교:

```bash
python -m bk2d stats out\hero out\hero_v2
```

**PNG 용량은 디스크 크기일 뿐이고, 게임 메모리는 RGBA/ASTC 값으로 판단한다.**

## 튕김 점검

```bash
python -m bk2d check out\hero_v2
```

Unity 와 같은 방식(프레임별 피벗 = 원점)으로 프레임을 배치해 바닥선 출렁임, 루프 이음매 점프, 움직임 방향 전환 횟수를 잰다.
"데이터상 튕김 없음" 인데 Unity 에서 튄다면 원인은 Unity 쪽(임포트 설정, 스케일, 다른 스프라이트 참조 등)이다.

- 흔들림 보정은 캐릭터가 화면 끝에서 잘린 방향(예: 발이 화면 아래로 잘림 → 세로)으로는 보정하지 않는다. 보정하면 잘린 선이 움직여 튕겨 보이기 때문.

## Unity 임포터 컴파일 검사

```bash
unity/ci/compile_check.sh   # mono(mcs), curl, unzip 필요
```

실제 UnityEngine(2021.3)/UnityEditor(2018.1) 참조 DLL 을 NuGet 에서 받아 컴파일한다. 2D Sprite 패키지 API 만 스텁(`unity/ci/U2DSpritesStubs.cs`)이다.
UnityEditor 가 2018.1 이라 2019 이후에만 있는 API 사용은 이 검사로 잡히지 않는다.

## 테스트

```bash
python -m pytest -q tests
```

합성 영상(초록 배경 + 도형 캐릭터)으로 루프 탐지, 투명도/디스필, 구도가 다른 클립의 정렬, 포즈 불일치 검출을 검증합니다.

## 알려진 한계

- 클립 *내부* 카메라 이동은 보정하지 않음 (생성 시 카메라 고정 필수)
- 셀이 모든 프레임의 합집합 크기라, 팔을 크게 뻗는 액션이 하나 있으면 전 클립의 셀이 커짐 (빈 공간 = 메모리 낭비). 필요해지면 프레임별 트리밍 + 타이트 패킹으로 개선.
- 루프 크로스페이드 미지원 (스프라이트 블렌딩은 잔상이 생겨 오히려 나쁜 경우가 많음)
- rembg 모드는 선택 의존성이며 프레임 간 마스크 흔들림이 있음
