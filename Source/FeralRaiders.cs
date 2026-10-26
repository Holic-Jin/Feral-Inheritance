using System.Collections.Generic;
using HarmonyLib;
using RimWorld;
using Verse;

namespace FeralInheritance
{
    /// <summary>
    /// 습격자에게 「야수의 계승」 유전자 + 무작위 능력 1개(1단계 고정) 부여 (Biotech 유전자가 로드됐을 때만).
    /// 훅: IncidentWorker_Raid.TryGenerateRaidInfo(IncidentParms, out List&lt;Pawn&gt;, bool) postfix — 습격 전용 생성 경로.
    /// 대상: 플레이어가 아닌, 플레이어와 적대인 세력의 인간형 정착민(변이체 제외). 확률은 모드 설정.
    /// </summary>
    [HarmonyPatch(typeof(IncidentWorker_Raid), nameof(IncidentWorker_Raid.TryGenerateRaidInfo))]
    public static class Patch_IncidentWorker_Raid_TryGenerateRaidInfo
    {
        public static void Postfix(bool __result, IncidentParms parms, ref List<Pawn> pawns, bool debugTest)
        {
            if (!__result || debugTest || pawns == null || parms?.faction == null) return;
            if (FeralEligibility.Gene == null) return; // Biotech 없음
            float chance = FeralInheritanceMod.Settings?.raiderGeneChance ?? FeralSettings.DefaultRaiderGeneChance;
            if (chance <= 0f) return;
            Faction f = parms.faction;
            if (f.IsPlayer || !f.HostileTo(Faction.OfPlayer)) return;
            for (int i = 0; i < pawns.Count; i++)
            {
                Pawn p = pawns[i];
                if (p == null || p.Faction != f) continue;
                if (!Rand.Chance(chance)) continue;
                FeralRaiders.TryMakeFeralRaider(p);
            }
        }
    }

    public static class FeralRaiders
    {
        /// <summary>유전자(변형인자) + 무작위 능력 1개(1단계 고정). 성공하면 true.</summary>
        public static bool TryMakeFeralRaider(Pawn p)
        {
            if (p?.RaceProps == null || !p.RaceProps.Humanlike) return false;
            if (p.genes == null || p.abilities == null || FeralEligibility.Gene == null) return false;
            if (Patch_Pawn_Kill.IsUndeadOrMutant(p)) return false;

            var candidates = new List<FeralSourceDef>();
            foreach (var src in DefDatabase<FeralSourceDef>.AllDefsListForReading)
                if (src?.ability != null && p.abilities.GetAbility(src.ability) == null) candidates.Add(src);
            if (candidates.Count == 0) return false;

            if (!p.genes.HasActiveGene(FeralEligibility.Gene)) p.genes.AddGene(FeralEligibility.Gene, true);

            FeralSourceDef pick = candidates.RandomElement();
            p.abilities.GainAbility(pick.ability);
            if (p.abilities.GetAbility(pick.ability) is FeralAbility fa)
            {
                fa.kills = pick.Thresholds.t1;
                fa.fixedAtT1 = true;
            }
            return true;
        }
    }
}
