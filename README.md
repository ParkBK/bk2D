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
- `target`: `"sprite"`(기본, SpriteRenderer) 또는 `"ui"`(Canvas 의 UI Image). 생성되는 .anim 의 대상 컴포넌트가 달라진다.
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
