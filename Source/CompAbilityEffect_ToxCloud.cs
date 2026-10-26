using RimWorld;
using Verse;

namespace FeralInheritance
{
    public class CompProperties_AbilityToxCloud : CompProperties_AbilityEffect
    {
        /// <summary>독 가스를 채울 반경(칸). GasUtility.AddGas(cell, map, ToxGas, radius)에 그대로 넘김.</summary>
        public float radius = 5.9f;

        public CompProperties_AbilityToxCloud()
        {
            compClass = typeof(CompAbilityEffect_ToxCloud);
        }
    }

    /// <summary>
    /// 시전자 중심 독 가스 구름 (0.9.1~). 죽는 독성사슴과 같은 바닐라 독 가스(GasType.ToxGas).
    /// 시전자 보호 없음: 시전자도 가스에 노출됨 → 방독면·독 저항 유전자 등이 대응 수단.
    /// </summary>
    public class CompAbilityEffect_ToxCloud : CompAbilityEffect
    {
        public new CompProperties_AbilityToxCloud Props => (CompProperties_AbilityToxCloud)props;

        public override void Apply(LocalTargetInfo target, LocalTargetInfo dest)
        {
            base.Apply(target, dest);
            Pawn caster = parent.pawn;
            if (caster == null || !caster.Spawned) return;
            Map map = caster.Map;
            GasUtility.AddGas(caster.Position, map, GasType.ToxGas, Props.radius);
            FleckMaker.ThrowDustPuff(caster.Position, map, 2f);
        }
    }
}
