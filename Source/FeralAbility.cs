using System.Collections.Generic;
using RimWorld;
using Verse;
using Verse.AI;

namespace FeralInheritance
{
    /// <summary>
    /// 처치 수와 단계를 능력 인스턴스에 저장 (정착민 저장 데이터와 함께 저장됨).
    /// AbilityDef.abilityClass로 지정.
    /// </summary>
    public class FeralAbility : Ability
    {
        public int kills;
        /// <summary>습격자에게 준 능력: 처치 수와 관계없이 1단계 고정 (0.9.1).</summary>
        public bool fixedAtT1;
        private int aiCheckCounter;

        public FeralAbility() : base() { }
        public FeralAbility(Pawn pawn) : base(pawn) { }
        public FeralAbility(Pawn pawn, Precept sourcePrecept) : base(pawn, sourcePrecept) { }
        public FeralAbility(Pawn pawn, AbilityDef def) : base(pawn, def) { }
        public FeralAbility(Pawn pawn, Precept sourcePrecept, AbilityDef def) : base(pawn, sourcePrecept, def) { }

        public FeralSourceDef Source => FeralSourceDef.ForAbility(def);

        public int Tier
        {
            get
            {
                var src = Source;
                if (src == null || fixedAtT1) return 1;
                int t = src.TierForKills(kills);
                return t < 1 ? 1 : t;
            }
        }

        public int TierCooldownTicks
        {
            get
            {
                var src = Source;
                return src != null ? src.CooldownForTier(Tier) : def.cooldownTicksRange.max;
            }
        }

        /// <summary>
        /// def.cooldownTicksRange는 모든 정착민이 공유 → 바닐라 코드가 읽는 동안만 현재 단계 값으로 바꿨다가 되돌림.
        /// (UI·게임 로직은 메인 스레드라 안전)
        /// </summary>
        private T WithTierCooldown<T>(System.Func<T> f)
        {
            IntRange orig = def.cooldownTicksRange;
            int t = TierCooldownTicks;
            def.cooldownTicksRange = new IntRange(t, t);
            try { return f(); }
            finally { def.cooldownTicksRange = orig; }
        }

        public override bool Activate(LocalTargetInfo target, LocalTargetInfo dest)
        {
            bool result = WithTierCooldown(() => base.Activate(target, dest));
            // 보험: 바닐라가 쿨다운을 다른 곳에서 시작해도 현재 단계 값으로 맞춤
            if (result || OnCooldown) StartCooldown(TierCooldownTicks);
            return result;
        }

        /// <summary>단계가 오르면 이미 돌고 있는 쿨다운도 새 단계 값 이하로 줄임.</summary>
        public void Notify_TierChanged()
        {
            int newTotal = TierCooldownTicks;
            if (CooldownTicksRemaining > newTotal) StartCooldown(newTotal);
        }

        public string TierLabel => "FI_GizmoLabel".Translate(def.LabelCap.ToString().Named("ABILITY"), Tier.Named("TIER"));

        public override string Tooltip
        {
            get
            {
                // 바닐라 툴팁의 「재사용 대기시간」 줄이 현재 단계 값으로 나오게 함 (중복 줄 없음)
                string text = WithTierCooldown(() => base.Tooltip);
                var src = Source;
                if (src == null) return text;
                string info = "FI_TierInfo".Translate(Tier.Named("TIER"), (src.race?.label ?? "?").Named("SOURCE"), kills.Named("KILLS"));
                string next;
                if (src.lockAtT1 || fixedAtT1) next = "FI_TierLocked".Translate();
                else if (Tier >= 3) next = "FI_TierMax".Translate();
                else
                {
                    var th = src.Thresholds;
                    int need = (Tier == 1 ? th.t2 : th.t3) - kills;
                    if (need < 1) need = 1;
                    next = "FI_TierNext".Translate(need.Named("REMAIN"));
                }
                return text + "\n\n" + info + "\n" + next;
            }
        }

        /// <summary>효과 반경: 능력 효과 comp의 radius (화염 폭발 / 독 가스). 없으면 verb 사거리.</summary>
        public float EffectRadius
        {
            get
            {
                if (def.comps != null)
                    foreach (var c in def.comps)
                    {
                        if (c is CompProperties_AbilityFireBurst fb) return fb.radius;
                        if (c is CompProperties_AbilityToxCloud tc) return tc.radius;
                    }
                return def.verbProperties?.range ?? 3f;
            }
        }

        /// <summary>
        /// 비플레이어 AI 시전 (0.9.1). 바닐라 AI 경로(aiCanUse / JobGiver_AICastAbility 계열)는 생각 트리에
        /// 능력별 노드가 있어야 하므로 쓰지 않고, 여기서 약 1초마다 직접 판단해 시전 작업을 시작함.
        /// 조건: 플레이어와 적대인 비플레이어 정착민, 반경 안 적(쓰러지지 않은) ≥ 1, 반경 안 아군 ≤ 적.
        /// </summary>
        public override void AbilityTick()
        {
            base.AbilityTick();
            if (++aiCheckCounter < 60) return;
            aiCheckCounter = 0;
            try { TryAICast(); }
            catch (System.Exception e) { Log.ErrorOnce("[Feral Inheritance] AI cast failed: " + e, 0x4F1A2B); }
        }

        private void TryAICast()
        {
            Pawn p = pawn;
            if (p == null || !p.Spawned || p.Dead || p.Downed || p.Map == null) return;
            if (p.Faction == null || p.Faction.IsPlayer || !p.Faction.HostileTo(Faction.OfPlayer)) return;
            if (p.IsPrisoner || p.jobs == null || Casting || OnCooldown || !CanCast.Accepted) return;

            float r = EffectRadius;
            int enemies = 0, allies = 0;
            foreach (Thing t in GenRadial.RadialDistinctThingsAround(p.Position, p.Map, r, true))
            {
                if (!(t is Pawn o) || o == p || o.Dead || o.Downed) continue;
                if (o.HostileTo(p)) enemies++;
                else if (o.Faction == p.Faction) allies++;
            }
            if (enemies < 1 || allies > enemies) return;

            Job job = GetJob(p, p);
            if (job != null) p.jobs.StartJob(job, JobCondition.InterruptForced);
        }

        public override IEnumerable<Command> GetGizmos()
        {
            foreach (Command cmd in base.GetGizmos())
            {
                if (cmd != null) cmd.defaultLabel = TierLabel;
                yield return cmd;
            }
        }

        public override void ExposeData()
        {
            base.ExposeData();
            Scribe_Values.Look(ref kills, "fi_kills", 1);
            Scribe_Values.Look(ref fixedAtT1, "fi_fixedT1", false);
        }
    }
}
