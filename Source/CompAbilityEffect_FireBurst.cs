using System.Collections.Generic;
using RimWorld;
using Verse;

namespace FeralInheritance
{
    public class CompProperties_AbilityFireBurst : CompProperties_AbilityEffect
    {
        /// <summary>폭발 반경(칸). 바닐라 부말로프: 새끼 1.9 / 청소년 2.9 / 성체 4.9.</summary>
        public float radius = 4.9f;
        /// <summary>-1이면 Flame DamageDef 기본 피해(바닐라 부말로프 폭발과 같음).</summary>
        public int damAmount = -1;

        public CompProperties_AbilityFireBurst()
        {
            compClass = typeof(CompAbilityEffect_FireBurst);
        }
    }

    /// <summary>
    /// 시전자 중심 화염 폭발(DamageDefOf.Flame). 대상 지점은 무시하고 항상 시전자 위치에서 터진다.
    /// 시전자는 GenExplosion.DoExplosion의 ignoredThings로 제외 → 피해·착화 없음. 주변 아군은 맞음.
    /// </summary>
    public class CompAbilityEffect_FireBurst : CompAbilityEffect
    {
        public new CompProperties_AbilityFireBurst Props => (CompProperties_AbilityFireBurst)props;

        public override void Apply(LocalTargetInfo target, LocalTargetInfo dest)
        {
            base.Apply(target, dest);
            Pawn caster = parent.pawn;
            if (caster == null || !caster.Spawned) return;
            Map map = caster.Map;
            GenExplosion.DoExplosion(
                center: caster.Position,
                map: map,
                radius: Props.radius,
                damType: DamageDefOf.Flame,
                instigator: caster,
                damAmount: Props.damAmount,
                ignoredThings: new List<Thing> { caster });
        }
    }
}
