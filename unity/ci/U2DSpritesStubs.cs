// 컴파일 검사 전용 스텁. 실제 Unity 프로젝트에는 넣지 말 것.
// com.unity.2d.sprite 패키지(UnityEditor.U2D.Sprites)의 공개 API 시그니처만 문서 기준으로 옮겨 적었다.
// 패키지 DLL 은 Unity 패키지 레지스트리에서만 받을 수 있어 이 환경에서는 실물 대신 스텁을 쓴다.
// SpriteRect, GUID, TextureImporter 등 나머지는 실제 UnityEditor.dll 에서 온다.
using System;
using System.Collections.Generic;

namespace UnityEditor.U2D.Sprites
{
    public interface ISpriteEditorDataProvider
    {
        SpriteImportMode spriteImportMode { get; }
        float pixelsPerUnit { get; }
        UnityEngine.Object targetObject { get; }
        SpriteRect[] GetSpriteRects();
        void SetSpriteRects(SpriteRect[] spriteRects);
        void Apply();
        void InitSpriteEditorDataProvider();
        T GetDataProvider<T>() where T : class;
        bool HasDataProvider(Type type);
    }

    public class SpriteDataProviderFactories
    {
        public void Init() { }
        public ISpriteEditorDataProvider GetSpriteEditorDataProviderFromObject(UnityEngine.Object obj) => null;
    }

    public struct SpriteNameFileIdPair
    {
        public SpriteNameFileIdPair(string name, GUID fileId) { this.name = name; this.fileId = fileId; }
        public string name;
        public GUID fileId;
    }

    public interface ISpriteNameFileIdDataProvider
    {
        IEnumerable<SpriteNameFileIdPair> GetNameFileIdPairs();
        void SetNameFileIdPairs(IEnumerable<SpriteNameFileIdPair> pairs);
    }
}
