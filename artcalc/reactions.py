"""Query-time portfolios for one active damage reaction, without UI dependencies."""
from __future__ import annotations

from dataclasses import replace

from .frontier import FrontierBuildGenerator, FrontierGeneratorConfig
from .optimizer import BuildSolution, OptimizationRequest, OptimizationResult, OptimizerConfig
from .search_session import current_session, search_session
from .solver_catalog import SolverCatalog
from .stat_model import derived_stats


REACTIONS = {
    "electricity": "electroshock_reaction",
    "burning": "burn_reaction",
    "tear": "tear_reaction",
}


class ReactionBuildGenerator:
    """Optimize durability with exactly one reaction added to vitality.

    The affine catalog adapter makes the selected reaction part of both the
    solver constraints and objectives. Returned stats and artifact stats remain
    passive; conditional durability has its own explicit derived field.
    """

    def __init__(self, catalog: SolverCatalog, reaction: str,
                 config: FrontierGeneratorConfig | None = None,
                 solver_config: OptimizerConfig | None = None):
        if reaction not in REACTIONS:
            raise ValueError(f"Unsupported reaction: {reaction}")
        self.reaction = reaction
        self.stat = REACTIONS[reaction]
        self.config = config or FrontierGeneratorConfig()
        adapted = replace(
            catalog,
            artifact_groups=tuple(replace(
                group, stats_low=self._activate(group.stats_low),
                stats_high=self._activate(group.stats_high),
            ) for group in catalog.artifact_groups),
            armors=tuple({**item, "stats": self._activate(item.get("stats") or {})}
                         for item in catalog.armors),
            containers=tuple({**item, "stats": self._activate(item.get("stats") or {})}
                             for item in catalog.containers),
        )
        self.generator = FrontierBuildGenerator(adapted, self.config, solver_config=solver_config)

    def _activate(self, stats: dict[str, float]) -> dict[str, float]:
        return {**stats, "vitality": stats.get("vitality", 0.0) + stats.get(self.stat, 0.0)}

    def _passive(self, stats: dict[str, float]) -> dict[str, float]:
        return {**stats, "vitality": stats.get("vitality", 0.0) - stats.get(self.stat, 0.0)}

    def search(self, request: OptimizationRequest) -> OptimizationResult:
        # A displayed reaction build must actually gain vitality on activation.
        targets = {**request.targets, self.stat: max(request.targets.get(self.stat, 0.0), 0.01)}
        outer = current_session.get()
        seconds = min(self.config.time_limit, outer.remaining()) if outer else self.config.time_limit
        # Incumbents from another reaction (or passive search) are not reusable.
        token = current_session.set(None)
        try:
            with search_session(seconds):
                result = self.generator.search(replace(request, targets=targets))
        finally:
            current_session.reset(token)
        return replace(
            result, request=request,
            solutions=tuple(self._restore(solution) for solution in result.solutions),
            diagnostics={**result.diagnostics, "engine": "reaction_frontier_v1",
                         "active_reaction": self.reaction,
                         "durability_target_basis": "with_reaction"},
        )

    def _restore(self, solution: BuildSolution) -> BuildSolution:
        stats = self._passive(solution.stats)
        derived = derived_stats(stats)
        derived["durability_without_reactions"] = derived["effective_durability"]
        derived["durability_with_reaction"] = solution.derived["effective_durability"]
        return replace(
            solution, build_id=f"reaction:{self.reaction}:{solution.build_id}",
            active_reaction=self.reaction, stats=stats, derived=derived,
            artifacts=tuple({**item, "stats": self._passive(item["stats"])}
                            for item in solution.artifacts),
        )
