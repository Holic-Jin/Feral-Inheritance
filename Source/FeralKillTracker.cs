using System.Collections.Generic;
using RimWorld;
using Verse;

namespace FeralInheritance
{
    /// <summary>
    /// 능력을 배우기 전의 처치 수 (설정의 1단계 기준이 2 이상일 때 사용).
    /// 키 = 정착민 ThingID + "|" + AbilityDef defName. 배우면 FeralAbility.kills로 옮기고 삭제.
    /// </summary>
    public class FeralKillTracker : GameComponent
    {
        private Dictionary<string, int> preLearnKills = new Dictionary<string, int>();

        public FeralKillTracker(Game game) { }

        public static FeralKillTracker Get() => Current.Game?.GetComponent<FeralKillTracker>();

        private static string Key(Pawn pawn, AbilityDef ability) => pawn.ThingID + "|" + ability.defName;

        public int AddKill(Pawn pawn, AbilityDef ability)
        {
            string k = Key(pawn, ability);
            preLearnKills.TryGetValue(k, out int n);
            n++;
            preLearnKills[k] = n;
            return n;
        }

        public void Clear(Pawn pawn, AbilityDef ability) => preLearnKills.Remove(Key(pawn, ability));

        public override void ExposeData()
        {
            base.ExposeData();
            Scribe_Collections.Look(ref preLearnKills, "fi_preLearnKills", LookMode.Value, LookMode.Value);
            if (preLearnKills == null) preLearnKills = new Dictionary<string, int>();
        }
    }
}
