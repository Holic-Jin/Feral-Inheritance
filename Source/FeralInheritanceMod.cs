using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;
using Verse;

namespace FeralInheritance
{
    /// <summary>모드 진입점: 설정 로드 + Harmony 패치 + 능력별 설정 화면.</summary>
    public class FeralInheritanceMod : Mod
    {
        public static FeralSettings Settings;

        private Vector2 scrollPos;
        private float lastViewHeight = 600f;
        private readonly Dictionary<string, string[]> buffers = new Dictionary<string, string[]>();

        public FeralInheritanceMod(ModContentPack content) : base(content)
        {
            Settings = GetSettings<FeralSettings>();
            new Harmony("holic.feralinheritance").PatchAll();
        }

        public override string SettingsCategory() => "Feral Inheritance";

        public override void DoSettingsWindowContents(Rect inRect)
        {
            var s = Settings;
            List<FeralSourceDef> sources = DefDatabase<FeralSourceDef>.AllDefsListForReading;

            // 위: 설명 + 전체 초기화
            var top = new Listing_Standard();
            top.Begin(new Rect(inRect.x, inRect.y, inRect.width, 260f));
            // 특성 등장 확률 (0~5, 기본 0.3)
            s.traitCommonality = top.SliderLabeled(
                "FI_Settings_TraitCommonality".Translate(s.traitCommonality.ToString("0.00").Named("VALUE")),
                s.traitCommonality, 0f, FeralSettings.MaxTraitCommonality, 0.6f, "FI_Settings_TraitCommonalityTip".Translate());
            s.traitCommonality = Mathf.Round(s.traitCommonality * 20f) / 20f; // 0.05 단위
            if (top.ButtonText("FI_Settings_TraitCommonalityReset".Translate(), null, 0.4f)) s.traitCommonality = FeralSettings.DefaultTraitCommonality;
            // 습격자 유전자 확률 (Biotech, 0~20%, 기본 2%)
            s.raiderGeneChance = top.SliderLabeled(
                "FI_Settings_RaiderChance".Translate(s.raiderGeneChance.ToStringPercent("0.#").Named("VALUE")),
                s.raiderGeneChance, 0f, FeralSettings.MaxRaiderGeneChance, 0.6f, "FI_Settings_RaiderChanceTip".Translate());
            s.raiderGeneChance = Mathf.Round(s.raiderGeneChance * 200f) / 200f; // 0.5% 단위
            if (top.ButtonText("FI_Settings_RaiderChanceReset".Translate(), null, 0.4f)) s.raiderGeneChance = FeralSettings.DefaultRaiderGeneChance;
            top.GapLine();
            top.Label("FI_Settings_Header".Translate());
            top.Label("FI_Settings_Note".Translate());
            if (top.ButtonText("FI_Settings_ResetAll".Translate(), null, 0.4f))
            {
                s.ResetAll();
                buffers.Clear();
            }
            top.End();

            // 아래: 능력 목록 (스크롤)
            Rect outRect = new Rect(inRect.x, inRect.y + 270f, inRect.width, inRect.height - 270f);
            Rect viewRect = new Rect(0f, 0f, outRect.width - 16f, Mathf.Max(lastViewHeight, outRect.height));
            Widgets.BeginScrollView(outRect, ref scrollPos, viewRect);
            var list = new Listing_Standard();
            list.Begin(viewRect);
            for (int i = 0; i < sources.Count; i++)
            {
                FeralSourceDef src = sources[i];
                if (src?.ability == null || src.race == null) continue;
                string key = src.ability.defName;
                FeralThresholds t = s.GetOrCreate(src);
                if (!buffers.TryGetValue(key, out var buf)) { buf = new string[3]; buffers[key] = buf; }
                var d = FeralSettings.XmlDefaults(src);

                list.GapLine();
                list.Label("FI_Settings_AbilityHeader".Translate(src.ability.LabelCap.ToString().Named("ABILITY"), src.race.LabelCap.ToString().Named("SOURCE"), d.t1.Named("D1"), d.t2.Named("D2"), d.t3.Named("D3")));
                if (src.lockAtT1) list.Label("FI_TierLocked".Translate());
                list.TextFieldNumericLabeled("FI_Settings_T1".Translate(), ref t.t1, ref buf[0], 1, FeralSettings.MaxKills);
                list.TextFieldNumericLabeled("FI_Settings_T2".Translate(), ref t.t2, ref buf[1], 1, FeralSettings.MaxKills);
                list.TextFieldNumericLabeled("FI_Settings_T3".Translate(), ref t.t3, ref buf[2], 1, FeralSettings.MaxKills);
                if (!t.IsValidOrder) list.Label("FI_Settings_Invalid".Translate());
                if (list.ButtonText("FI_Settings_ResetOne".Translate(), null, 0.4f))
                {
                    s.ResetOne(src);
                    buffers.Remove(key);
                }
            }
            lastViewHeight = list.CurHeight + 20f;
            list.End();
            Widgets.EndScrollView();
        }

        public override void WriteSettings()
        {
            // v0.3 공통 값이 있으면, 로드된 모든 능력에 먼저 옮긴 뒤 정리
            foreach (var src in DefDatabase<FeralSourceDef>.AllDefsListForReading)
                if (src?.ability != null) Settings.For(src);
            Settings.ValidateAll();
            Settings.DropEntriesEqualToDefaults();
            buffers.Clear();
            TraitCommonality.Apply();
            base.WriteSettings();
        }
    }
}
