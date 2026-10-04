// bk2d 출력(*.character.json + 시트 PNG/JSON)을 Unity 스프라이트/애니메이션으로 가져온다.
// 필요 패키지: 2D Sprite (com.unity.2d.sprite). 2D 템플릿엔 기본 포함, 3D 템플릿엔 없음.
// CS0234 'Sprites' 에러가 나면: Window > Package Manager > Unity Registry > 2D Sprite 설치
// 사용: 출력 폴더를 Assets 아래로 복사 -> *.character.json 선택 -> 우클릭 > bk2D > Import Character
// 컴파일 검사: unity/ci/compile_check.sh (실제 UnityEngine/UnityEditor 참조 DLL + 2D Sprite 패키지 스텁)
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.U2D.Sprites;
using UnityEngine;

namespace Bk2d.Editor
{
    public static class Bk2dImporter
    {
#pragma warning disable 0649 // JsonUtility 가 채우는 필드
        [System.Serializable] class Vec2Json { public float x, y; }
        [System.Serializable] class SizeJson { public int w, h; }
        [System.Serializable] class FrameJson
        {
            public string name;
            public int page, x, y, w, h;   // page: 아틀라스 페이지 번호, x/y: 좌하단 원점
            public Vec2Json pivot;         // v2: 프레임별 피벗 (트리밍돼도 발밑을 가리킴)
        }
        [System.Serializable] class ClipJson
        {
            public int version;
            public string character, clip, image, playback;
            public string[] images;        // v2: 여러 페이지
            public SizeJson[] pageSizes;   // v2: 페이지 크기
            public float fps;
            public bool loop;
            public int pixelsPerUnit;
            public Vec2Json pivot;
            public FrameJson[] frames;
            public int[] sequence;         // v2: 재생 순서 (핑퐁)
        }
        [System.Serializable] class ClipRefJson { public string name, json; public bool loop; }
        [System.Serializable] class UnityJson
        {
            public int maxTextureSize;
            public string format;          // 예: ASTC_6x6, ASTC_8x8
            public string[] platforms;     // 예: Android, iPhone
        }
        [System.Serializable] class CharacterJson
        {
            public string name, @default, target;  // target: "sprite"(기본) | "ui"
            public int pixelsPerUnit;
            public UnityJson unity;
            public ClipRefJson[] clips;
        }
#pragma warning restore 0649

        const string Menu = "Assets/bk2D/Import Character";

        [MenuItem(Menu, true)]
        static bool Validate() =>
            Selection.objects.Any(o => AssetDatabase.GetAssetPath(o).EndsWith(".character.json"));

        [MenuItem(Menu)]
        static void ImportSelected()
        {
            foreach (var o in Selection.objects)
            {
                var path = AssetDatabase.GetAssetPath(o);
                if (path.EndsWith(".character.json")) ImportCharacter(path);
            }
        }

        public static void ImportCharacter(string manifestPath)
        {
            var dir = Path.GetDirectoryName(manifestPath).Replace('\\', '/');
            var ch = JsonUtility.FromJson<CharacterJson>(File.ReadAllText(manifestPath));
            var clips = new Dictionary<string, (AnimationClip anim, bool loop)>();

            foreach (var cref in ch.clips)
            {
                var c = JsonUtility.FromJson<ClipJson>(File.ReadAllText($"{dir}/{cref.json}"));
                var sprites = ImportSheet(dir, c, ch.unity);
                clips[c.clip] = (WriteAnimationClip(dir, c, sprites, ch.target == "ui"), c.loop);
            }

            var controllerPath = $"{dir}/{ch.name}.controller";
            if (!File.Exists(controllerPath))
                CreateController(controllerPath, ch.@default, clips);
            else
                AddMissingStates(controllerPath, ch.@default, clips);

            AssetDatabase.SaveAssets();
            Debug.Log($"[bk2D] {ch.name}: 클립 {clips.Count}개 가져옴");
        }

        static List<Sprite> ImportSheet(string dir, ClipJson c, UnityJson unity)
        {
            var images = c.images != null && c.images.Length > 0 ? c.images : new[] { c.image };
            var byName = new Dictionary<string, Sprite>();
            for (int page = 0; page < images.Length; page++)
            {
                var texPath = $"{dir}/{images[page]}";
                var frames = c.frames.Where(f => f.page == page).ToArray();
                var size = c.pageSizes != null && page < c.pageSizes.Length ? c.pageSizes[page] : null;
                ImportPage(texPath, c, frames, unity, size);
                foreach (var s in AssetDatabase.LoadAllAssetsAtPath(texPath).OfType<Sprite>())
                    byName[s.name] = s;
            }
            return c.frames.Select(f => byName[f.name]).ToList();
        }

        static void ImportPage(string texPath, ClipJson c, FrameJson[] frames, UnityJson unity, SizeJson size)
        {
            AssetDatabase.ImportAsset(texPath, ImportAssetOptions.ForceSynchronousImport);
            var ti = (TextureImporter)AssetImporter.GetAtPath(texPath);

            ti.textureType = TextureImporterType.Sprite;
            ti.spriteImportMode = SpriteImportMode.Multiple;
            ti.spritePixelsPerUnit = c.pixelsPerUnit;
            ti.alphaIsTransparency = true;
            ti.mipmapEnabled = false;
            ti.filterMode = FilterMode.Bilinear;
            // 일러스트 계열은 기본 압축에서 밴딩/블록 노이즈가 두드러진다.
            ti.textureCompression = TextureImporterCompression.CompressedHQ;
            ApplyTextureSettings(ti, unity, size);

            var factory = new SpriteDataProviderFactories();
            factory.Init();
            var dp = factory.GetSpriteEditorDataProviderFromObject(ti);
            dp.InitSpriteEditorDataProvider();

            // 재임포트 시 기존 spriteID 를 유지해야 씬/프리팹의 참조가 끊기지 않는다.
            var existing = dp.GetSpriteRects().ToDictionary(r => r.name, r => r.spriteID);
            var common = new Vector2(c.pivot.x, c.pivot.y);
            var rects = frames.Select(f => new SpriteRect
            {
                name = f.name,
                rect = new Rect(f.x, f.y, f.w, f.h),
                alignment = SpriteAlignment.Custom,
                // v2 는 프레임마다 잘린 크기가 달라 피벗도 프레임별. 모두 같은 발밑 지점을 가리킨다.
                pivot = c.version >= 2 && f.pivot != null ? new Vector2(f.pivot.x, f.pivot.y) : common,
                spriteID = existing.TryGetValue(f.name, out var id) ? id : GUID.Generate(),
            }).ToArray();
            dp.SetSpriteRects(rects);
#if UNITY_2021_2_OR_NEWER
            var nameIds = dp.GetDataProvider<ISpriteNameFileIdDataProvider>();
            if (nameIds != null)
                nameIds.SetNameFileIdPairs(rects.Select(r => new SpriteNameFileIdPair(r.name, r.spriteID)));
#endif
            dp.Apply();
            ti.SaveAndReimport();
        }

        // 프로필별 텍스처 설정: 기본 플랫폼 maxTextureSize + 모바일 플랫폼 ASTC 오버라이드.
        static void ApplyTextureSettings(TextureImporter ti, UnityJson unity, SizeJson size)
        {
            int largest = size != null ? Mathf.Max(size.w, size.h) : 2048;
            int max = unity != null && unity.maxTextureSize > 0
                ? unity.maxTextureSize
                : Mathf.Min(8192, Mathf.NextPowerOfTwo(largest));
            if (largest > max)
                Debug.LogWarning($"[bk2D] {ti.assetPath}: 페이지 {largest}px > maxTextureSize {max} — Unity 가 축소합니다(흐려짐).");
            ti.maxTextureSize = max;

            if (unity == null || string.IsNullOrEmpty(unity.format)) return;
            if (!TryParseFormat(unity.format, out var format))
            {
                Debug.LogWarning($"[bk2D] 알 수 없는 텍스처 포맷 '{unity.format}' — 플랫폼 오버라이드 생략");
                return;
            }
            var platforms = unity.platforms != null && unity.platforms.Length > 0
                ? unity.platforms : new[] { "Android", "iPhone" };
            foreach (var platform in platforms)
            {
                var ps = ti.GetPlatformTextureSettings(platform);
                ps.overridden = true;
                ps.maxTextureSize = max;
                ps.format = format;
                ti.SetPlatformTextureSettings(ps);
            }
        }

        // Unity 2019+ 는 ASTC_6x6, 2018 은 ASTC_RGBA_6x6. 이름으로 찾아 버전 차이를 흡수한다.
        static bool TryParseFormat(string name, out TextureImporterFormat format)
        {
            if (System.Enum.TryParse(name, out format)) return true;
            if (name.StartsWith("ASTC_") && System.Enum.TryParse("ASTC_RGBA_" + name.Substring(5), out format))
                return true;
            return false;
        }

        // UI Image 는 com.unity.ugui 패키지 타입이라 컴파일 의존을 피하려고 이름으로 찾는다.
        static System.Type UIImageType() =>
            System.Type.GetType("UnityEngine.UI.Image, UnityEngine.UI")
            ?? throw new System.Exception("[bk2D] target=ui 는 uGUI(com.unity.ugui) 패키지가 필요합니다.");

        static AnimationClip WriteAnimationClip(string dir, ClipJson c, List<Sprite> sprites, bool ui)
        {
            var clip = new AnimationClip { frameRate = c.fps };
            var binding = EditorCurveBinding.PPtrCurve("", ui ? UIImageType() : typeof(SpriteRenderer), "m_Sprite");
            // 핑퐁 등 재생 순서가 있으면 그 순서대로 키를 만든다 (같은 스프라이트 재사용).
            var order = c.sequence != null && c.sequence.Length > 0
                ? c.sequence : Enumerable.Range(0, sprites.Count).ToArray();
            var keys = new ObjectReferenceKeyframe[order.Length + 1];
            for (int i = 0; i < order.Length; i++)
                keys[i] = new ObjectReferenceKeyframe { time = i / c.fps, value = sprites[order[i]] };
            // 마지막 프레임도 1프레임 길이만큼 보이도록 끝 키를 하나 더 둔다.
            keys[order.Length] = new ObjectReferenceKeyframe
            {
                time = order.Length / c.fps,
                value = sprites[order[order.Length - 1]],
            };
            AnimationUtility.SetObjectReferenceCurve(clip, binding, keys);

            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.loopTime = c.loop;
            AnimationUtility.SetAnimationClipSettings(clip, settings);

            var path = $"{dir}/{c.character}_{c.clip}.anim";
            var old = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
            if (old == null)
            {
                AssetDatabase.CreateAsset(clip, path);
                return clip;
            }
            // 기존 에셋을 덮어써 Animator 의 참조를 유지한다.
            EditorUtility.CopySerialized(clip, old);
            EditorUtility.SetDirty(old);
            return old;
        }

        // 허브-스포크 상태머신: 기본 클립(idle)이 허브.
        // - 루프 아닌 액션: Trigger 로 진입, 끝나면 허브로 복귀
        // - 루프 액션(walk 등): Bool 이 true 인 동안 유지
        static void CreateController(string path, string hub,
            Dictionary<string, (AnimationClip anim, bool loop)> clips)
        {
            var ctrl = AnimatorController.CreateAnimatorControllerAtPath(path);
            var sm = ctrl.layers[0].stateMachine;
            var hubState = sm.AddState(hub);
            hubState.motion = clips[hub].anim;
            sm.defaultState = hubState;
            foreach (var kv in clips.Where(kv => kv.Key != hub))
                AddActionState(ctrl, sm, hubState, kv.Key, kv.Value.anim, kv.Value.loop);
        }

        // 이미 있는 컨트롤러: 사용자가 손댄 상태/전이는 그대로 두고, 새 클립의 상태만 추가한다.
        static void AddMissingStates(string path, string hub,
            Dictionary<string, (AnimationClip anim, bool loop)> clips)
        {
            var ctrl = AssetDatabase.LoadAssetAtPath<AnimatorController>(path);
            var sm = ctrl.layers[0].stateMachine;
            var existing = sm.states.Select(s => s.state).ToDictionary(s => s.name);
            if (!existing.TryGetValue(hub, out var hubState))
            {
                hubState = sm.AddState(hub);
                hubState.motion = clips[hub].anim;
                sm.defaultState = hubState;
            }
            var added = new List<string>();
            foreach (var kv in clips.Where(kv => kv.Key != hub && !existing.ContainsKey(kv.Key)))
            {
                AddActionState(ctrl, sm, hubState, kv.Key, kv.Value.anim, kv.Value.loop);
                added.Add(kv.Key);
            }
            EditorUtility.SetDirty(ctrl);
            if (added.Count > 0)
                Debug.Log($"[bk2D] {path}: 새 상태 추가 {string.Join(", ", added)}");
        }

        static void AddActionState(AnimatorController ctrl, AnimatorStateMachine sm, AnimatorState hubState,
            string name, AnimationClip anim, bool loop)
        {
            var state = sm.AddState(name);
            state.motion = anim;
            var enter = sm.AddAnyStateTransition(state);
            enter.canTransitionToSelf = false;
            enter.hasExitTime = false;
            enter.duration = 0;
            var back = state.AddTransition(hubState);
            back.duration = 0;

            if (ctrl.parameters.All(p => p.name != name))
                ctrl.AddParameter(name, loop ? AnimatorControllerParameterType.Bool
                                             : AnimatorControllerParameterType.Trigger);
            enter.AddCondition(AnimatorConditionMode.If, 0, name);
            if (loop)
            {
                back.hasExitTime = false;
                back.AddCondition(AnimatorConditionMode.IfNot, 0, name);
            }
            else
            {
                back.hasExitTime = true;
                back.exitTime = 1f;
            }
        }
    }

    // bk2d 가 *.character.json 을 새로 쓰면(빌드마다 내용이 바뀜) 우클릭 없이 자동으로 가져온다.
    // 메뉴 Tools > bk2D > Auto Import 로 끄고 켤 수 있다.
    public class Bk2dAutoImport : AssetPostprocessor
    {
        const string Pref = "bk2d.autoImport";
        const string Menu = "Tools/bk2D/Auto Import";

        static bool Enabled
        {
            get => EditorPrefs.GetBool(Pref, true);
            set => EditorPrefs.SetBool(Pref, value);
        }

        [MenuItem(Menu)]
        static void Toggle() => Enabled = !Enabled;

        [MenuItem(Menu, true)]
        static bool ToggleValidate()
        {
            UnityEditor.Menu.SetChecked(Menu, Enabled);
            return true;
        }

        static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            if (!Enabled) return;
            var targets = imported.Where(p => p.EndsWith(".character.json")).ToArray();
            if (targets.Length == 0) return;
            // 임포트 콜백 안에서 다른 에셋을 임포트하면 안 되므로 다음 에디터 틱으로 미룬다.
            EditorApplication.delayCall += () =>
            {
                foreach (var p in targets)
                {
                    try { Bk2dImporter.ImportCharacter(p); }
                    catch (System.Exception e) { Debug.LogError($"[bk2D] 자동 임포트 실패 {p}: {e}"); }
                }
            };
        }
    }
}
