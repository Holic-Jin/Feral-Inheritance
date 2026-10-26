using RimWorld;
using Verse;

namespace FeralInheritance
{
    [DefOf]
    public static class FeralDefOf
    {
        public static TraitDef FI_FeralInheritance;

        static FeralDefOf()
        {
            DefOfHelper.EnsureInitializedInCtor(typeof(FeralDefOf));
        }
    }
}
