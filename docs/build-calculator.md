# Build calculator components

The build calculator is split into two independent components.

## 1. Precompute

Entrypoint:

```python
from artcalc.precompute import BuildPrecomputer, PrecomputeConfig

payload = BuildPrecomputer(PrecomputeConfig()).run()
```

CLI:

```powershell
python tools\precompute_builds.py
```

Default output:

```text
data/precomputed_builds.json
```

What it does:

```text
1. Loads only Veteran and Master containers.
2. Loads artifact candidates from data/artifact_prices.json.
   Default stat level is +15.
   Default price level is +0, because artifact upgrading is treated as free.
   Default quality strategy is adaptive_grid with 2.5% steps.
   Default minimum quality percent is 95%.
   Default minimum artifact build price is 2,500,000.
   Rubik (`9n7z`) is excluded by default until its unique mechanics are modeled.
   Beam pruning is split into price buckets from 2,500,000 with a 2,500,000 step.
3. Builds full artifact loadouts per container with beam search.
4. Applies container effectiveness to artifact stats, excluding infection stats.
5. Sums positive and negative artifact infections before inner protection.
6. Applies inner protection to infection types except frost.
7. Adds container infection stats after inner protection.
8. Adds base infection output:
   radiation/temperature/biological/psycho = -0.5
   frost = -1.0
9. Keeps only infection-valid builds.
10. Reduces builds to a Pareto frontier per container.
```

Default mechanics are configurable through `MechanicsConfig`.

Important price/stat split:

```text
artifact_upgrade_level = 15
artifact_price_upgrade_level = 0
quality_strategy = adaptive_grid
quality_step = 2.5
min_quality_percent = 95
min_build_price = 2500000
price_bucket_start = 2500000
price_bucket_step = 2500000
price_bucket_beam_size = 20
max_beam_states = 0  # uses beam_size when unset
excluded_artifact_ids = ("9n7z",)  # Rubik
```

That means build power is estimated as if artifacts are eventually upgraded to
`+15`, while budget is spent on buying the base artifact rarity segment. Each
artifact entry keeps `stat_upgrade_level`, `price_upgrade_level`, and
`quality_percent`.

The precompute keeps only full containers. There is no virtual empty artifact
slot: if a zero-effect artifact is present in the candidate list, it can behave
like a real filler. Rubik is excluded for this reason until its variable unique
bonuses are represented explicitly.

Adaptive quality grid:

```text
1. Generate percentage points inside each rarity range.
2. Remove percentage points that are strictly dominated by another percentage
   of the same artifact and rarity at the same price.
3. For harmful-infection artifacts, candidate selection keeps low percentages
   early, then high percentages, then inner percentage steps.
4. For safe artifacts and counter-artifacts, dominated lower percentages are
   pruned aggressively.
5. During container search, beam states are pruned by price buckets, not only by
   global score. Default buckets start at 2,500,000 and grow by 2,500,000.
   This preserves cheaper builds for low-budget queries.
6. Negative infection output is not scored as a positive build property.
   It is only used to keep enough safety representatives during beam pruning
   and to pass the final infection validity check.
```

Important default:

```text
container_effectiveness_affects_infections = False
frost_ignores_inner_protection = True
container_infections_are_protected = False
```

These defaults follow current community-documented mechanics. The old meaning of
container effectiveness as charge drain is intentionally not used for stat scoring.

## 2. Runtime Query

Entrypoint:

```python
from artcalc.query import BuildQueryEngine

engine = BuildQueryEngine()
result = engine.query(
    budget=50_000_000,
    targets={
        "effective_durability": 35000,
        "movement_speed": 5,
        "total_sprint_speed": 110,
        "stamina": 30,
    },
    require_targets=True,
    max_results=20,
)
```

CLI:

```powershell
python tools\query_builds.py --budget 50000000 --targets targets.json --require-targets
```

The query component:

```text
1. Loads data/precomputed_builds.json.
2. Loads data/armor_stats.json.
3. Filters Veteran/Master +15 armor by default.
4. Combines armor stats with precomputed container loadouts.
5. Recomputes derived stats:
   effective_durability
   total_sprint_speed
   hp_regen_score
6. Applies budget and target filters.
7. Scores results using runtime weights.
8. Returns matching builds and near misses.
9. Adds nearby container-upgrade options when the same artifact set works better in another container.
```

The current budget is artifact-only because local reliable prices currently exist
only for artifacts. By default, artifact prices are `+0` purchase prices and do
not include upgrade energy or catalysts. Container and armor prices can be
integrated later as extra price fields without changing the scoring engine.

## 3. Boosted-only Runtime Query

Boosts are a separate runtime layer. The normal precompute and normal query
stay unchanged.

Entrypoint:

```python
from artcalc.boosts import BoostedBuildQueryEngine

engine = BoostedBuildQueryEngine()
result = engine.query_only_with_boosts(
    budget=50_000_000,
    targets={
        "effective_durability": 35000,
        "movement_speed": 5,
        "total_sprint_speed": 110,
        "stamina": 30,
    },
    max_results=20,
)
```

CLI:

```powershell
python tools\extract_boosts.py
python tools\query_boosted_builds.py --budget 50000000 --targets targets.json --output data\boosted_only.json
```

For PowerShell, prefer passing `--targets` as a JSON file path. Inline JSON is
easy to misquote.

Rules:

```text
1. Boosts are loaded from data/boosts.json.
2. Only one consumable per effect_type may be active at the same time.
3. Boost price is always treated as 0.
4. Output and healing effect types are exported but ignored in build queries.
5. Builds already valid without boosts are excluded from this category.
6. The returned category is only_with_boosts.
7. Each result includes stats_without_boosts, derived_without_boosts,
   misses_without_boosts, final stats/derived, selected boosts, and comparison.
```

Default interactive search depth:

```text
base_candidate_limit = 800
boosts_per_type = 4
```

These limits keep the boosted layer usable for an interface. Increase
`--base-candidate-limit` or `--boosts-per-type` for a deeper offline search.

## Quality policy

Precompute defaults to `quality_policy = mid`, because market prices are grouped
by rarity, not exact artifact percentage. Supported policies:

```text
min
mid
p75
max
```

This affects generated artifact stats for each rarity tier.

## Refresh sequence

```powershell
.\tools\fetch_staldata_matrix.ps1
python tools\staldata_prices.py --offline
python tools\extract_armor_stats.py
python tools\extract_boosts.py
python tools\precompute_builds.py --artifact-upgrade-level 15 --artifact-price-upgrade-level 0 --quality-strategy adaptive_grid --quality-step 2.5 --min-quality-percent 95 --min-build-price 2500000 --price-bucket-start 2500000 --price-bucket-step 2500000
```
