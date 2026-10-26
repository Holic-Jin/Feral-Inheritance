using System.Collections.Generic;
using System.Linq;
using RimWorld;
using Verse;

namespace FeralInheritance
{
    /// <summary>
    /// 정보 카드용 AbilityDef. 바닐라 정보 카드(Dialog_InfoCard(Def) → StatsReportUtility.DrawStatsReport(Rect, AbilityDef))는
    /// 정착민을 넘기지 않음 → req.Pawn이 없으면 현재 선택된 정착민 1명을 기준으로 함.
    /// 그 정착민이 이 능력을 가졌으면 바닐라 「재사용 대기시간」 항목을 현재 단계 값으로 보여 주고, 항상 단계표를 추가.
    /// </summary>
    public class FeralAbilityDef : AbilityDef
    {
        public override IEnumerable<StatDrawEntry> SpecialDisplayStats(StatRequest req)
        {
            FeralSourceDef src = FeralSourceDef.ForAbility(this);
            Pawn pawn = req.Pawn ?? (Find.Selector?.SingleSelectedThing as Pawn);
            FeralAbility fa = pawn?.abilities?.GetAbility(this) as FeralAbility;

            List<StatDrawEntry> entries;
            if (fa != null)
            {
                IntRange orig = cooldownTicksRange;
                int t = fa.TierCooldownTicks;
                cooldownTicksRange = new IntRange(t, t);
                try { entries = base.SpecialDisplayStats(req).ToList(); }
                finally { cooldownTicksRange = orig; }
            }
            else entries = base.SpecialDisplayStats(req).ToList();

            if (src != null)
            {
                var th = src.Thresholds;
                var cat = StatCategoryDefOf.Ability;
                if (fa != null)
                {
                    entries.Add(new StatDrawEntry(cat, "FI_Stat_CurrentTier".Translate(),
                        "FI_Stat_TierValue".Translate(fa.Tier.Named("TIER"), fa.kills.Named("KILLS"), pawn.LabelShort.Named("PAWN")),
                        "FI_Stat_CurrentTierDesc".Translate(), 4010));
                }
                for (int tier = 1; tier <= 3; tier++)
                {
                    if (src.lockAtT1 && tier > 1) break;
                    int kills = tier == 1 ? th.t1 : (tier == 2 ? th.t2 : th.t3);
                    entries.Add(new StatDrawEntry(cat, "FI_Stat_TierRow".Translate(tier.Named("TIER")),
                        "FI_Stat_TierRowValue".Translate(src.CooldownForTier(tier).ToStringTicksToPeriod().Named("COOLDOWN"), kills.Named("KILLS")),
                        "FI_Stat_TierRowDesc".Translate(), 4000 - tier));
                }
            }
            return entries;
        }
    }
}
