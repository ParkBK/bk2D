#!/usr/bin/env bash
# Bk2dImporter.cs 컴파일 검사 (Unity 없이).
#  - 실제 UnityEngine 2021.3 모듈 DLL, UnityEditor 2018.1 DLL 을 NuGet 에서 받아 참조
#  - 2D Sprite 패키지 API 는 U2DSpritesStubs.cs 스텁으로 대체
#  - UNITY_2021_2_OR_NEWER 정의/미정의 두 경우 모두 컴파일
# 필요: mono (mcs), curl, unzip
# 한계: UnityEditor DLL 이 2018.1 이라 2019+ 에만 있는 API 는 여기서 확인되지 않는다.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CACHE="${BK2D_UNITYREF:-$HOME/.cache/bk2d-unityref}"
mkdir -p "$CACHE"
fetch() {  # id version dir
  if [ ! -d "$CACHE/$3" ]; then
    curl -sSf -m 300 -o "$CACHE/$3.nupkg" "https://api.nuget.org/v3-flatcontainer/$1/$2/$1.$2.nupkg"
    mkdir -p "$CACHE/$3" && (cd "$CACHE/$3" && unzip -q -o "../$3.nupkg") && chmod -R u+rw "$CACHE/$3"
  fi
}
fetch unityengine.modules 2021.3.33 engine
fetch unity3d.unityeditor 2018.1.6-f1 editor
REFS=$(ls "$CACHE"/engine/lib/net45/UnityEngine*.dll | sed 's/^/-r:/' | tr '\n' ' ')
REFS="$REFS -r:$CACHE/editor/lib/UnityEditor.dll"
for defs in "" "-define:UNITY_2021_2_OR_NEWER"; do
  echo "== mcs ${defs:-(기본)}"
  mcs -nologo -target:library -langversion:7.2 -warnaserror- $defs $REFS \
      -out:"$CACHE/Bk2dImporter.dll" "$HERE/../Editor/Bk2dImporter.cs" "$HERE/U2DSpritesStubs.cs"
done
echo "컴파일 OK"
