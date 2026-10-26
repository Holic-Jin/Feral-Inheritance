# Feral Inheritance 0.9.1 (Core 완성판, 릴리스 후보)

「야수의 계승」 특성을 가진 정착민이 특수 능력이 있는 동물을 직접 처치하면 그 능력을 배우고, 같은 종을 더 잡을수록 3단계까지 성장(재사용 대기시간 단축)하는 모드예요. **DLC 없이 작동**하고, Biotech가 있으면 유전자판과 독성사슴 능력이 추가돼요.

| 처치 대상 | 능력 | 발동 | 1단계 (기본 1회) | 2단계 (누적 3회) | 3단계 (누적 8회) |
|---|---|---|---|---|---|
| 폭탄사슴 (Boomalope) | 폭탄사슴 화염 폭발 | 시전자 중심 4.9칸 | 13일 | 3일 | 15시간 |
| 폭탄쥐 (Boomrat) | 폭탄쥐 화염 폭발 | 시전자 중심 1.9칸 | 12일 | 2.5일 | 12시간 |
| 독성사슴 (Toxalope, **Biotech**) | 독성사슴 독 가스 | 시전자 중심 가스 반경 5.9 (시전자도 영향) | 13일 | 3일 | 15시간 |

- **시전 시간:** 모든 능력 0.5초(30틱). 공통 부모 Def `FI_AbilityBase`에 있어서 새 능력도 그대로 물려받아요.
- **습득 자격:** Core 특성 「야수의 계승」, 또는 (Biotech) 같은 이름의 **초월입자 유전자**(이식에 초월입자 캡슐 1개, 대사 0 / 복잡도 3 / 초월입자 1). 둘 중 하나만 있으면 돼요.
- **습격자 (Biotech):** 적대 습격자 한 명마다 낮은 확률(기본 2%, 설정 0~20%)로 이 유전자 + 배울 수 있는 능력 1개(1단계 고정)를 갖고 와요. 반경 안에 적이 있고 아군이 적보다 많지 않으면 전투 중에 사용해요.
- 폭발은 시전자를 빼고 터져요(피해·착화 없음). 폭발로 번진 불이나 주변 아군 피해는 평소대로예요.
- 독 가스는 시전자 중심으로 퍼지고 **시전자도 보호받지 않아요** — 방독면이나 독 저항 유전자가 필요해요.
- 사람·메카노이드·좀비화된 동물(Anomaly 변이체)에게서는 배우지 않아요.
- 툴팁과 정보 창은 **현재 단계** 대기시간을 보여 주고, 정보 창에는 단계표도 있어요.
- 습득·단계 상승 연출: 정착민 위로 피어오르는 연기.
- **모드 설정**(옵션 → 모드 설정 → Feral Inheritance): 특성 등장 확률(0~5, 기본 0.3), 습격자 유전자 확률(0~20%, 기본 2%), 능력별 단계 기준(기본 1 / 3 / 8). 켜진 DLC의 능력은 목록에 자동으로 나와요.
- 단계별 대기시간·1단계 고정(`lockAtT1`)은 `FI_Sources.xml`(Biotech는 `Mods/Biotech/Defs/FI_Toxalope.xml`)에서 바꿔요.
- 능력 아이콘은 해당 동물의 바닐라 그래픽을 자동으로 써요(텍스처 파일 없음).
- 변경 내역 `버전노트.md`, 테스트 `테스트체크리스트.md`.

## 설치
1. 이 폴더 전체를 림월드 `Mods` 폴더에 복사 (Windows Steam 기본: `C:\Program Files (x86)\Steam\steamapps\common\RimWorld\Mods\`)
2. **Harmony** 모드 필요 (창작마당 2009463077). 1.7 unstable은 Harmony v3.0.1 이상
3. 순서: `Harmony` → `Core` → (DLC) → `Feral Inheritance`
4. `Source` 폴더는 게임에 필요 없어요.

## 폴더 구조
- `About/About.xml` — 모드 정보 (packageId `holic.feralinheritance`, 1.6/1.7)
- `LoadFolders.xml` — `Common` + 버전 폴더, Biotech가 켜져 있으면 `Mods/Biotech`도 로드 (`IfModActive`)
- `Common/Defs`, `Common/Languages` — Core 내용(특성, 폭탄사슴·폭탄쥐 능력)과 한국어/영어
- `Mods/Biotech` — 유전자 「야수의 계승」, 독성사슴 능력과 한국어
- `1.6/Assemblies`, `1.7/Assemblies` — 버전별 DLL
- `Source` — C# 소스와 `.csproj`

## 다시 빌드하기
```
cd Source
dotnet build -c Release
```
.NET 10 SDK 필요. NuGet에서 `Krafs.Rimworld.Ref`(1.6.4871 / 1.7.4985-unstable)와 `Lib.Harmony`(2.4.2 / 3.0.0-rc.3)를 받아요.
