using HarmonyLib;
using RimWorld;
using Verse;

namespace FeralInheritance
{
    /// <summary>
    /// Pawn.Kill(DamageInfo?, Hediff) postfix.
    /// 처치자가 「야수의 계승」 특성(또는 Biotech 유전자)을 가진 플레이어 정착민이고, 죽은 생물이 FeralSourceDef에 등록돼 있으면
    /// 첫 처치 = 능력 습득(1단계), 이후 처치 = 누적 처치 수 +1 → 기준 도달 시 단계 상승.
    /// </summary>
    [HarmonyPatch(typeof(Pawn), nameof(Pawn.Kill))]
    public static class Patch_Pawn_Kill
    {
        /// <summary>죽기 직전 상태 기록: 변이체(샘블러·구울·깨어난 시체 등)였는지. 사망 처리 중 상태가 바뀌어도 안전하게.</summary>
        public static void Prefix(Pawn __instance, out bool __state)
        {
            __state = __instance != null && IsUndeadOrMutant(__instance);
        }

        /// <summary>Anomaly 변이체 판정. Core 어셈블리의 속성이라 Anomaly 없이도 안전(그때는 항상 false).</summary>
        public static bool IsUndeadOrMutant(Pawn p)
        {
            return p.IsMutant || p.IsShambler || p.IsGhoul || p.IsAwokenCorpse || p.mutant != null;
        }

        public static void Postfix(Pawn __instance, DamageInfo? dinfo, bool __state)
        {
            if (__instance == null || !__instance.Dead) return;
            if (__state || IsUndeadOrMutant(__instance)) return; // 좀비화(샘블러) 등 변이체는 배우지도 세지도 않음
            if (__instance.RaceProps == null || __instance.RaceProps.Humanlike) return; // 사람 흡수 금지
            if (!dinfo.HasValue) return;

            Pawn killer = dinfo.Value.Instigator as Pawn;
            if (killer == null || killer.Dead || killer == __instance) return;
            if (killer.Faction == null || !killer.Faction.IsPlayer) return;
            if (!FeralEligibility.CanLearn(killer)) return; // 특성 또는 (Biotech) 유전자
            if (killer.abilities == null) return;

            FeralSourceDef src = FeralSourceDef.ForRace(__instance.def);
            if (src?.ability == null) return;

            Ability existing = killer.abilities.GetAbility(src.ability);
            if (existing == null)
            {
                // 배우기 전 처치 수 (1단계 기준이 2 이상이면 여러 번 잡아야 배움)
                var tracker = FeralKillTracker.Get();
                int preKills = tracker != null ? tracker.AddKill(killer, src.ability) : 1;
                int t1 = src.Thresholds.t1;
                if (preKills < t1) return;
                FeralAbility learned = Gain(killer, src, preKills);
                if (learned == null) return;
                tracker?.Clear(killer, src.ability);
                Messages.Message(
                    "FI_LearnedAbility".Translate(killer.Named("PAWN"), __instance.Named("SOURCE"), src.ability.LabelCap.ToString().Named("ABILITY"), learned.Tier.Named("TIER")),
                    new LookTargets(killer), MessageTypeDefOf.PositiveEvent);
                FeralEffects.RisingSmoke(killer);
                return;
            }

            FeralAbility fa = existing as FeralAbility;
            if (fa == null)
            {
                // v0.1 저장 데이터: 기본 Ability 클래스로 저장돼 있음 → FeralAbility로 교체 (처치 1회 인정, 남은 쿨다운 유지)
                int remaining = existing.CooldownTicksRemaining;
                killer.abilities.RemoveAbility(src.ability);
                fa = Gain(killer, src, 1);
                if (fa == null) return;
                if (remaining > 0) fa.StartCooldown(remaining);
            }

            int oldTier = fa.Tier;
            fa.kills++;
            int newTier = fa.Tier;
            if (newTier > oldTier)
            {
                fa.Notify_TierChanged();
                Messages.Message(
                    "FI_TierUp".Translate(killer.Named("PAWN"), src.ability.LabelCap.ToString().Named("ABILITY"), newTier.Named("TIER")),
                    new LookTargets(killer), MessageTypeDefOf.PositiveEvent);
                FeralEffects.RisingSmoke(killer);
            }
        }

        private static FeralAbility Gain(Pawn pawn, FeralSourceDef src, int kills)
        {
            pawn.abilities.GainAbility(src.ability);
            FeralAbility fa = pawn.abilities.GetAbility(src.ability) as FeralAbility;
            if (fa == null)
            {
                Log.Warning("[Feral Inheritance] " + src.ability.defName + " is not a FeralAbility instance (check abilityClass).");
                return null;
            }
            fa.kills = kills;
            return fa;
        }
    }
}
