# Reaction Builds

The "Шизо-варианты" tab searches with one active reaction: electricity,
burning, or tear. Other reactions can occur on useful artifacts but are not
activated or added to the objective.

For total passive bullet resistance B, vitality V, and the selected net
reaction R (in percentage points):

```text
durability_without_reactions = (B + 100) * (V + 100) / 100
durability_with_reaction = (B + 100) * (V + R + 100) / 100
```

The durability requirement, frontier objectives, and personal ranking use the
second value in this mode. All other requirements, prices, quality limits,
exclusions, container capacity and infection checks work as in normal search.
R must be at least 0.01, so a returned reaction build actually benefits from
activation. Negative modifiers of the selected reaction subtract from R.

This is conditional durability while the reaction is active, not average
survivability. Activation damage, duration, uptime, triggering tools and their
costs are not simulated. This does not claim a reaction build is globally
better than the best passive build: both values describe the same composition.

## Reusable API

```python
from artcalc import OptimizationRequest, ReactionBuildGenerator, SolverCatalog

catalog = SolverCatalog.read("data/solver_catalog.json")
generator = ReactionBuildGenerator(catalog, reaction="electricity")
result = generator.search(OptimizationRequest(
    budget=50_000_000,
    targets={"durability": 450, "speed": 0},
    armor_ids=("2ovr0",),
    container_ids=("g35n",),
))
```

The generator adapts a copy of the affine catalog: only the selected reaction
is added to vitality, before container effectiveness. Both the MILP and CP
paths therefore search with the conditional formula, including quality
selection and exact validation. The source catalog is unchanged. Search
sessions are isolated to avoid reusing passive or other-reaction incumbents.
There is no offline build precomputation; the normal 19-second search allowance
is retained for the selected reaction.

Returned `stats` and artifact `stats` are passive. The existing derived
`effective_durability` remains passive too. Additional derived fields are
`durability_without_reactions` and `durability_with_reaction`.
`metrics.durability`, when requested, is conditional. `active_reaction` and a
reaction-prefixed `build_id` identify the scenario unambiguously.

## HTTP API

`POST /api/optimize` accepts optional `active_reaction`:
`null` (normal search), `electricity`, `burning`, or `tear`.
The active reaction alone is sufficient to search with an empty `targets` map.
Multiple reactions and unsupported names are rejected. Cache keys and feedback
contexts include the active reaction; previous passive feedback still matches
normal requests. Selection changes clear the displayed results.

Upgrade plans and their potential score are currently available only for
passive builds. Reaction cards do not offer passive upgrade plans, and the
upgrade endpoint rejects reaction builds instead of silently changing their
optimization scenario.
