using HarmonyLib;
using RimWorld;
using UnityEngine;
using Verse;

namespace FeralInheritance
{
    /// <summary>
    /// 습득 자격: Core 특성 「야수의 계승」 또는 (Biotech 켜졌을 때만 로드되는) 유전자 FI_FeralInheritanceGene.
    /// 유전자 Def는 이름으로 찾음 → Biotech가 없으면 null이고 특성만 봄 (하드 의존 없음).
    /// </summary>
    [StaticConstructorOnStartup]
    public static class FeralEligibility
    {
        public const string GeneDefName = "FI_FeralInheritanceGene";
        public static readonly GeneDef Gene;

        static FeralEligibility()
        {
            Gene = DefDatabase<GeneDef>.GetNamedSilentFail(GeneDefName);
            if (Gene != null)
            {
                // 유전자 아이콘: 바닐라 폭탄사슴 그래픽 재사용 (텍스처 경로 하드코딩 없음)
                Texture2D tex = FeralIconLoader.RaceIcon(DefDatabase<ThingDef>.GetNamedSilentFail("Boomalope"));
                if (tex != null) AccessTools.Field(typeof(GeneDef), "cachedIcon")?.SetValue(Gene, tex);
            }
        }

        public static bool CanLearn(Pawn p)
        {
            if (p == null) return false;
            if (p.story?.traits != null && p.story.traits.HasTrait(FeralDefOf.FI_FeralInheritance)) return true;
            return Gene != null && p.genes != null && p.genes.HasActiveGene(Gene);
        }
    }
}
