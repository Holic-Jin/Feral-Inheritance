using System.Collections.Generic;
using UnityEngine;
using Verse;

namespace FeralInheritance
{
    /// <summary>능력 하나의 단계 기준(누적 처치 수).</summary>
    public class FeralThresholds : IExposable
    {
        public int t1 = FeralSettings.DefaultT1;
        public int t2 = FeralSettings.DefaultT2;
        public int t3 = FeralSettings.DefaultT3;

        public FeralThresholds() { }
        public FeralThresholds(int t1, int t2, int t3) { this.t1 = t1; this.t2 = t2; this.t3 = t3; Validate(); }

        public void Validate()
        {
            t1 = Mathf.Clamp(t1, 1, FeralSettings.MaxKills);
            t2 = Mathf.Clamp(t2, t1, FeralSettings.MaxKills);
            t3 = Mathf.Clamp(t3, t2, FeralSettings.MaxKills);
        }

        public bool IsValidOrder => 1 <= t1 && t1 <= t2 && t2 <= t3 && t3 <= FeralSettings.MaxKills;

        public void ExposeData()
        {
            Scribe_Values.Look(ref t1, "t1", FeralSettings.DefaultT1);
            Scribe_Values.Look(ref t2, "t2", FeralSettings.DefaultT2);
            Scribe_Values.Look(ref t3, "t3", FeralSettings.DefaultT3);
        }
    }

    /// <summary>
    /// 모드 설정 (v0.4): 능력별 단계 기준. 키 = AbilityDef defName.
    /// 설정에 값이 없으면 FeralSourceDef(XML)의 기본값을 씀. 설정 값이 있으면 설정이 우선.
    /// v0.3 공통 값(killsForT1~3)은 읽기만 하고, 설정에 아직 없는 능력의 초기값으로 한 번 옮긴 뒤 더는 저장하지 않음.
    /// </summary>
    public class FeralSettings : ModSettings
    {
        public const int DefaultT1 = 1, DefaultT2 = 3, DefaultT3 = 8, MaxKills = 999;

        public const float DefaultTraitCommonality = 0.3f, MaxTraitCommonality = 5f;
        public float traitCommonality = DefaultTraitCommonality;

        /// <summary>습격자 한 명이 「야수의 계승」 유전자 + 능력 1개를 갖고 올 확률 (Biotech 필요). 0~20%.</summary>
        public const float DefaultRaiderGeneChance = 0.02f, MaxRaiderGeneChance = 0.2f;
        public float raiderGeneChance = DefaultRaiderGeneChance;

        public Dictionary<string, FeralThresholds> perAbility = new Dictionary<string, FeralThresholds>();

        // v0.3 공통 설정 (이전 호환용, 읽기 전용)
        private int legacyT1 = -1, legacyT2 = -1, legacyT3 = -1;
        public bool HasLegacy => legacyT1 > 0 || legacyT2 > 0 || legacyT3 > 0;

        public override void ExposeData()
        {
            base.ExposeData();
            Scribe_Collections.Look(ref perAbility, "perAbility", LookMode.Value, LookMode.Deep);
            Scribe_Values.Look(ref traitCommonality, "traitCommonality", DefaultTraitCommonality);
            traitCommonality = Mathf.Clamp(traitCommonality, 0f, MaxTraitCommonality);
            Scribe_Values.Look(ref raiderGeneChance, "raiderGeneChance", DefaultRaiderGeneChance);
            raiderGeneChance = Mathf.Clamp(raiderGeneChance, 0f, MaxRaiderGeneChance);
            if (Scribe.mode == LoadSaveMode.LoadingVars)
            {
                Scribe_Values.Look(ref legacyT1, "killsForT1", -1);
                Scribe_Values.Look(ref legacyT2, "killsForT2", -1);
                Scribe_Values.Look(ref legacyT3, "killsForT3", -1);
            }
            if (perAbility == null) perAbility = new Dictionary<string, FeralThresholds>();
            foreach (var v in perAbility.Values) v?.Validate();
        }

        public static FeralThresholds XmlDefaults(FeralSourceDef src)
            => new FeralThresholds(src.killsForT1, src.killsForT2, src.killsForT3);

        /// <summary>실제로 쓰는 기준. 설정 → (v0.3 공통 값 이전) → XML 기본값 순.</summary>
        public FeralThresholds For(FeralSourceDef src)
        {
            string key = src.ability.defName;
            if (perAbility.TryGetValue(key, out var t) && t != null) return t;
            if (HasLegacy)
            {
                var d = XmlDefaults(src);
                var migrated = new FeralThresholds(
                    legacyT1 > 0 ? legacyT1 : d.t1,
                    legacyT2 > 0 ? legacyT2 : d.t2,
                    legacyT3 > 0 ? legacyT3 : d.t3);
                perAbility[key] = migrated;
                return migrated;
            }
            return XmlDefaults(src);
        }

        /// <summary>설정 화면에서 편집할 항목(없으면 만들어서 넣음).</summary>
        public FeralThresholds GetOrCreate(FeralSourceDef src)
        {
            var cur = For(src);
            string key = src.ability.defName;
            if (!perAbility.ContainsKey(key)) { cur = new FeralThresholds(cur.t1, cur.t2, cur.t3); perAbility[key] = cur; }
            return cur;
        }

        public void ResetOne(FeralSourceDef src) => perAbility.Remove(src.ability.defName);

        public void ResetAll()
        {
            perAbility.Clear();
            legacyT1 = legacyT2 = legacyT3 = -1;
        }

        public void ValidateAll()
        {
            foreach (var v in perAbility.Values) v?.Validate();
        }

        /// <summary>XML 기본값과 같은 항목은 저장하지 않음 → 나중에 XML 기본값이 바뀌면 그대로 따라감.</summary>
        public void DropEntriesEqualToDefaults()
        {
            var remove = new List<string>();
            foreach (var kv in perAbility)
            {
                if (kv.Value == null) { remove.Add(kv.Key); continue; }
                FeralSourceDef src = null;
                foreach (var d in DefDatabase<FeralSourceDef>.AllDefsListForReading)
                    if (d.ability != null && d.ability.defName == kv.Key) { src = d; break; }
                if (src == null) continue; // 지금 로드 안 된 능력(DLC 꺼짐 등) 설정은 보존
                var x = XmlDefaults(src);
                if (x.t1 == kv.Value.t1 && x.t2 == kv.Value.t2 && x.t3 == kv.Value.t3) remove.Add(kv.Key);
            }
            foreach (var k in remove) perAbility.Remove(k);
            legacyT1 = legacyT2 = legacyT3 = -1; // v0.3 값은 이미 능력별로 옮겼으므로 버림
        }
    }
}
