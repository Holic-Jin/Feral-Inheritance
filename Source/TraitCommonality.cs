using HarmonyLib;
using RimWorld;
using Verse;

namespace FeralInheritance
{
    /// <summary>
    /// 「야수의 계승」 특성 등장 확률을 모드 설정 값으로 덮어씀.
    /// TraitDef.commonality는 private float → AccessTools로 설정. Def 로드 후(정적 생성자)와 설정 저장 시 적용.
    /// </summary>
    [StaticConstructorOnStartup]
    public static class TraitCommonality
    {
        private static readonly AccessTools.FieldRef<TraitDef, float> commonalityRef =
            AccessTools.FieldRefAccess<TraitDef, float>("commonality");

        static TraitCommonality() { Apply(); }

        public static void Apply()
        {
            TraitDef def = DefDatabase<TraitDef>.GetNamedSilentFail("FI_FeralInheritance");
            if (def == null || FeralInheritanceMod.Settings == null) return;
            commonalityRef(def) = FeralInheritanceMod.Settings.traitCommonality;
        }
    }
}
