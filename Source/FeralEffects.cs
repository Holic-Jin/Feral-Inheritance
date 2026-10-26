using RimWorld;
using UnityEngine;
using Verse;

namespace FeralInheritance
{
    public static class FeralEffects
    {
        /// <summary>습득·단계 상승 연출: 정착민 위로 피어오르는 연기 (빛 효과 없음).</summary>
        public static void RisingSmoke(Pawn pawn)
        {
            if (pawn == null || !pawn.Spawned || pawn.Map == null) return;
            Map map = pawn.Map;
            Vector3 basePos = pawn.DrawPos;
            for (int i = 0; i < 6; i++)
            {
                Vector3 pos = basePos + new Vector3(Rand.Range(-0.4f, 0.4f), 0f, Rand.Range(0f, 0.6f));
                FleckMaker.ThrowSmoke(pos, map, Rand.Range(1.2f, 1.8f));
            }
            FleckMaker.ThrowDustPuff(basePos, map, 1.5f);
        }
    }
}
