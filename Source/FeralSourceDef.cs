using RimWorld;
using UnityEngine;
using Verse;

namespace FeralInheritance
{
    /// <summary>
    /// "이 생물을 처치하면 이 능력을 배운다" + 단계별 쿨다운. 능력 하나당 1개.
    /// 단계 기준(누적 처치 수): XML killsForT1~3 = 기본값, 모드 설정(능력별)이 있으면 설정이 우선 (v0.4).
    /// </summary>
    public class FeralSourceDef : Def
    {
        public ThingDef race;
        public AbilityDef ability;
        /// <summary>능력 아이콘으로 쓸 바닐라 텍스처 경로(없으면 무시).</summary>
        public string iconTexPath;

        /// <summary>XML 기본 단계 기준 (모드 설정에서 능력별로 덮어쓸 수 있음).</summary>
        public int killsForT1 = 1;
        public int killsForT2 = 3;
        public int killsForT3 = 8;

        /// <summary>판을 뒤집는 능력용: true면 처치 수와 관계없이 1단계 고정.</summary>
        public bool lockAtT1 = false;

        /// <summary>단계별 쿨다운(틱). 1일 = 60000틱, 1시간 = 2500틱.</summary>
        public int cooldownTicksT1 = 720000;
        public int cooldownTicksT2 = 180000;
        public int cooldownTicksT3 = 37500;

        public int TierForKills(int kills)
        {
            FeralThresholds th = Thresholds;
            int t1 = th.t1, t2 = th.t2, t3 = th.t3;
            if (kills < t1) return 0;
            if (lockAtT1) return 1;
            if (kills >= t3) return 3;
            if (kills >= t2) return 2;
            return 1;
        }

        /// <summary>현재 적용 중인 단계 기준 (설정 우선, 없으면 XML).</summary>
        public FeralThresholds Thresholds
            => FeralInheritanceMod.Settings != null ? FeralInheritanceMod.Settings.For(this) : FeralSettings.XmlDefaults(this);

        public int CooldownForTier(int tier)
        {
            switch (tier)
            {
                case 3: return cooldownTicksT3;
                case 2: return cooldownTicksT2;
                default: return cooldownTicksT1;
            }
        }

        public static FeralSourceDef ForRace(ThingDef raceDef)
        {
            var all = DefDatabase<FeralSourceDef>.AllDefsListForReading;
            for (int i = 0; i < all.Count; i++)
            {
                if (all[i].race == raceDef) return all[i];
            }
            return null;
        }

        public static FeralSourceDef ForAbility(AbilityDef abilityDef)
        {
            var all = DefDatabase<FeralSourceDef>.AllDefsListForReading;
            for (int i = 0; i < all.Count; i++)
            {
                if (all[i].ability == abilityDef) return all[i];
            }
            return null;
        }

        public override System.Collections.Generic.IEnumerable<string> ConfigErrors()
        {
            foreach (var e in base.ConfigErrors()) yield return e;
            if (race == null) yield return "race is null";
            if (ability == null) yield return "ability is null";
            if (killsForT1 < 1 || killsForT2 < killsForT1 || killsForT3 < killsForT2 || killsForT3 > FeralSettings.MaxKills)
                yield return "kill thresholds must satisfy 1 <= killsForT1 <= killsForT2 <= killsForT3 <= 999 (auto-corrected)";
            if (cooldownTicksT1 <= 0 || cooldownTicksT2 <= 0 || cooldownTicksT3 <= 0) yield return "cooldown ticks must be > 0";
        }
    }

    /// <summary>
    /// 능력 아이콘 = 대상 생물의 성체 몸 그래픽(동쪽). 텍스처 경로를 하드코딩하지 않고 종족 그래픽에서 가져옴.
    /// iconTexPath를 XML에 적으면 그 경로가 우선.
    /// </summary>
    [StaticConstructorOnStartup]
    public static class FeralIconLoader
    {
        static FeralIconLoader()
        {
            foreach (var src in DefDatabase<FeralSourceDef>.AllDefsListForReading)
            {
                if (src.ability == null) continue;
                Texture2D tex = null;
                if (!string.IsNullOrEmpty(src.iconTexPath)) tex = ContentFinder<Texture2D>.Get(src.iconTexPath, false);
                if (tex == null) tex = RaceIcon(src.race);
                if (tex != null) src.ability.uiIcon = tex;
            }
        }

        /// <summary>종족의 마지막(성체) 생애 단계 몸 그래픽의 동쪽 텍스처. 실패하면 null.</summary>
        public static Texture2D RaceIcon(ThingDef race)
        {
            try
            {
                var stages = race?.race?.AnyPawnKind?.lifeStages;
                if (stages == null || stages.Count == 0) return null;
                var gd = stages[stages.Count - 1].bodyGraphicData;
                return gd?.Graphic?.MatEast?.mainTexture as Texture2D;
            }
            catch { return null; }
        }
    }
}
