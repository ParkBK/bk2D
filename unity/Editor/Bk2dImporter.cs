// bk2d 출력(*.character.json + 시트 PNG/JSON)을 Unity 스프라이트/애니메이션으로 가져온다.
// 필요 패키지: 2D Sprite (com.unity.2d.sprite). 2D 템플릿엔 기본 포함, 3D 템플릿엔 없음.
// CS0234 'Sprites' 에러가 나면: Window > Package Manager > Unity Registry > 2D Sprite 설치
// 사용: 출력 폴더를 Assets 아래로 복사 -> *.character.json 선택 -> 우클릭 > bk2D > Import Character
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
        [System.Serializable] class FrameJson { public string name; public int x, y, w, h; }
        [System.Serializable] class Vec2Json { public float x, y; }
        [System.Serializable] class ClipJson
        {
            public string character, clip, image;
            public float fps;
            public bool loop;
            public int pixelsPerUnit;
            public Vec2Json pivot;
            public FrameJson[] frames;
        }
        [System.Serializable] class ClipRefJson { public string name, json; public bool loop; }
        [System.Serializable] class CharacterJson
        {
            public string name, @default, target;  // target: "sprite"(기본) | "ui"
            public int pixelsPerUnit;
            public ClipRefJson[] clips;
        }

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
                var sprites = ImportSheet(dir, c);
                clips[c.clip] = (WriteAnimationClip(dir, c, sprites, ch.target == "ui"), c.loop);
            }

            var controllerPath = $"{dir}/{ch.name}.controller";
            if (!File.Exists(controllerPath))
                CreateController(controllerPath, ch.@default, clips);
            else
                Debug.Log($"[bk2D] {controllerPath} 가 이미 있어 상태머신은 건드리지 않았습니다 (클립만 갱신).");

            AssetDatabase.SaveAssets();
            Debug.Log($"[bk2D] {ch.name}: 클립 {clips.Count}개 가져옴");
        }

        static List<Sprite> ImportSheet(string dir, ClipJson c)
        {
            var texPath = $"{dir}/{c.image}";
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
            ti.GetSourceTextureWidthAndHeight(out var tw, out var th);
            ti.maxTextureSize = Mathf.Min(8192, Mathf.NextPowerOfTwo(Mathf.Max(tw, th)));

            var factory = new SpriteDataProviderFactories();
            factory.Init();
            var dp = factory.GetSpriteEditorDataProviderFromObject(ti);
            dp.InitSpriteEditorDataProvider();

            // 재임포트 시 기존 spriteID 를 유지해야 씬/프리팹의 참조가 끊기지 않는다.
            var existing = dp.GetSpriteRects().ToDictionary(r => r.name, r => r.spriteID);
            var pivot = new Vector2(c.pivot.x, c.pivot.y);
            var rects = c.frames.Select(f => new SpriteRect
            {
                name = f.name,
                rect = new Rect(f.x, f.y, f.w, f.h),
                alignment = SpriteAlignment.Custom,
                pivot = pivot,
                spriteID = existing.TryGetValue(f.name, out var id) ? id : GUID.Generate(),
            }).ToArray();
            dp.SetSpriteRects(rects);
#if UNITY_2021_2_OR_NEWER
            var nameIds = dp.GetDataProvider<ISpriteNameFileIdDataProvider>();
            nameIds?.SetNameFileIdPairs(rects.Select(r => new SpriteNameFileIdPair(r.name, r.spriteID)));
#endif
            dp.Apply();
            ti.SaveAndReimport();

            var byName = AssetDatabase.LoadAllAssetsAtPath(texPath).OfType<Sprite>()
                .ToDictionary(s => s.name);
            return c.frames.Select(f => byName[f.name]).ToList();
        }

        // UI Image 는 com.unity.ugui 패키지 타입이라 컴파일 의존을 피하려고 이름으로 찾는다.
        static System.Type UIImageType() =>
            System.Type.GetType("UnityEngine.UI.Image, UnityEngine.UI")
            ?? throw new System.Exception("[bk2D] target=ui 는 uGUI(com.unity.ugui) 패키지가 필요합니다.");

        static AnimationClip WriteAnimationClip(string dir, ClipJson c, List<Sprite> sprites, bool ui)
        {
            var clip = new AnimationClip { frameRate = c.fps };
            var binding = EditorCurveBinding.PPtrCurve("", ui ? UIImageType() : typeof(SpriteRenderer), "m_Sprite");
            var keys = new ObjectReferenceKeyframe[sprites.Count + 1];
            for (int i = 0; i < sprites.Count; i++)
                keys[i] = new ObjectReferenceKeyframe { time = i / c.fps, value = sprites[i] };
            // 마지막 프레임도 1프레임 길이만큼 보이도록 끝 키를 하나 더 둔다.
            keys[sprites.Count] = new ObjectReferenceKeyframe { time = sprites.Count / c.fps, value = sprites[sprites.Count - 1] };
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
            {
                var state = sm.AddState(kv.Key);
                state.motion = kv.Value.anim;
                var enter = sm.AddAnyStateTransition(state);
                enter.canTransitionToSelf = false;
                enter.hasExitTime = false;
                enter.duration = 0;
                var back = state.AddTransition(hubState);
                back.duration = 0;

                if (kv.Value.loop)
                {
                    ctrl.AddParameter(kv.Key, AnimatorControllerParameterType.Bool);
                    enter.AddCondition(AnimatorConditionMode.If, 0, kv.Key);
                    back.hasExitTime = false;
                    back.AddCondition(AnimatorConditionMode.IfNot, 0, kv.Key);
                }
                else
                {
                    ctrl.AddParameter(kv.Key, AnimatorControllerParameterType.Trigger);
                    enter.AddCondition(AnimatorConditionMode.If, 0, kv.Key);
                    back.hasExitTime = true;
                    back.exitTime = 1f;
                }
            }
        }
    }
}
