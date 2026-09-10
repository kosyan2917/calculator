from dataclasses import replace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from artcalc.feedback import context_similarity, features
from artcalc.frontier import FrontierBuildGenerator, FrontierGeneratorConfig
from artcalc.optimizer import OptimizationRequest, OptimizerConfig
from artcalc.reactions import REACTIONS, ReactionBuildGenerator
from artcalc.search_session import current_session, search_session
from artcalc.stat_model import derived_stats
from tests.test_optimizer import armor, catalog, container, group


CONFIG = FrontierGeneratorConfig(sweep_points=0, solutions_per_point=1, time_limit=3)
SOLVER = OptimizerConfig(time_limit_per_solve=0.5, nonlinear_iterations=2)


def fixed(name, stats, **kwargs):
    return replace(group(name, stats, stats), **kwargs)


class ReactionSearchTests(unittest.TestCase):
    def test_each_reaction_participates_in_search_not_just_display(self):
        for reaction, stat in REACTIONS.items():
            with self.subTest(reaction=reaction):
                source = catalog((fixed("reactor", {"vitality": -5, stat: 20}),),
                                 (armor("armor", {"bullet_resistance": 400}),), container(1))
                request = OptimizationRequest(budget=1_000_000, targets={"durability": 550})
                self.assertFalse(FrontierBuildGenerator(source, CONFIG, solver_config=SOLVER).search(request).solutions)
                result = ReactionBuildGenerator(source, reaction, CONFIG, SOLVER).search(request)
                self.assertTrue(result.solutions)
                for build in result.solutions:
                    self.assertEqual(build.active_reaction, reaction)
                    self.assertAlmostEqual(build.stats["vitality"], -5)
                    self.assertAlmostEqual(build.artifacts[0]["stats"]["vitality"], -5)
                    self.assertAlmostEqual(build.derived["effective_durability"], 475)
                    self.assertAlmostEqual(build.derived["durability_without_reactions"], 475)
                    self.assertAlmostEqual(build.derived["durability_with_reaction"], 575)
                    self.assertAlmostEqual(build.metrics["durability"], 575)
                    self.assertTrue(build.infection["valid"])
                    self.assertEqual(build.total_price, 1_000_000)
                self.assertEqual(source.artifact_groups[0].stats_high["vitality"], -5)

    def test_other_reaction_allowed_for_passive_stats_but_never_added(self):
        source = catalog((
            fixed("electric", {"electroshock_reaction": 20}, max_count=1),
            fixed("passive_tank", {"bullet_resistance": 100, "burn_reaction": 100}, max_count=1),
            fixed("wrong_reaction", {"burn_reaction": 500}),
        ), (armor("armor", {"bullet_resistance": 400}),), container(2))
        result = ReactionBuildGenerator(source, "electricity", CONFIG, SOLVER).search(
            OptimizationRequest(budget=2_000_000, targets={"durability": 700}))
        self.assertTrue(result.solutions)
        for build in result.solutions:
            self.assertEqual({a["item_id"] for a in build.artifacts}, {"electric", "passive_tank"})
            self.assertAlmostEqual(build.derived["durability_without_reactions"], 600)
            self.assertAlmostEqual(build.derived["durability_with_reaction"], 720)
            self.assertAlmostEqual(build.stats["burn_reaction"], 100)

    def test_effectiveness_equipment_and_negative_reaction_count_once(self):
        bag = {**container(1, {"vitality": 2, "tear_reaction": -1}), "effectiveness": 150}
        source = catalog((fixed("reactor", {"vitality": 10, "tear_reaction": 20, "burn_reaction": 100}),),
                         (armor("armor", {"bullet_resistance": 400, "vitality": 3, "tear_reaction": -4}),), bag)
        result = ReactionBuildGenerator(source, "tear", CONFIG, SOLVER).search(
            OptimizationRequest(budget=None, targets={}))
        build = result.solutions[0]
        self.assertAlmostEqual(build.stats["vitality"], 20)
        self.assertAlmostEqual(build.stats["tear_reaction"], 25)
        self.assertAlmostEqual(build.loadout_stats["vitality"], 17)
        self.assertAlmostEqual(build.loadout_stats["tear_reaction"], 29)
        self.assertNotIn("bullet_resistance", build.loadout_stats)
        self.assertAlmostEqual(build.artifacts[0]["stats"]["vitality"], 15)
        self.assertAlmostEqual(build.derived["durability_without_reactions"], 600)
        self.assertAlmostEqual(build.derived["durability_with_reaction"], 725)
        self.assertEqual(derived_stats(build.stats)["effective_durability"], 600)

    def test_no_gain_or_lethal_infection_is_not_a_valid_reaction_build(self):
        for stats, infections in (({"burn_reaction": 100}, {}),
                                   ({"tear_reaction": -5}, {}),
                                   ({"tear_reaction": 100}, {"radiation": 0.6})):
            with self.subTest(stats=stats, infections=infections):
                source = catalog((group("a", stats, stats, infections, infections),),
                                 (armor("armor", {}),), container(1))
                result = ReactionBuildGenerator(source, "tear", CONFIG, SOLVER).search(
                    OptimizationRequest(budget=1_000_000, targets={"speed": 0}))
                self.assertFalse(result.solutions)

    def test_quality_and_healing_constraints_remain_exact(self):
        low = {"tear_reaction": 10, "periodic_healing": 2, "health_regeneration": 5}
        high = {**low, "tear_reaction": 20}
        source = catalog((group("reactor", low, high, {"radiation": 0}, {"radiation": 1},
                                low=13001, high=14500),),
                         (armor("armor", {"bullet_resistance": 400}),), container(1))
        result = ReactionBuildGenerator(source, "tear", CONFIG, SOLVER).search(
            OptimizationRequest(budget=1_000_000, targets={"durability": 574, "regen": 3}))
        self.assertTrue(result.solutions)
        for build in result.solutions:
            self.assertGreaterEqual(build.derived["durability_with_reaction"], 574)
            self.assertLessEqual(build.derived["durability_with_reaction"], 575)
            self.assertGreaterEqual(build.derived["hp_regen_score"], 3)
            self.assertTrue(build.infection["valid"])
            self.assertLess(build.artifacts[0]["quality_percent"], 145)

    def test_search_sessions_and_feedback_do_not_leak_between_reactions(self):
        source = catalog((fixed("reactor", {"tear_reaction": 20}),),
                         (armor("armor", {}),), container(1))
        with search_session(10) as outer:
            result = ReactionBuildGenerator(source, "tear", CONFIG, SOLVER).search(
                OptimizationRequest(budget=None, targets={}))
            self.assertIs(current_session.get(), outer)
            self.assertFalse(outer.candidates)
        self.assertTrue(result.solutions)
        self.assertEqual(context_similarity({}, {"active_reaction": "tear"}), 0)
        self.assertEqual(context_similarity({"active_reaction": "burning"}, {"active_reaction": "tear"}), 0)
        self.assertEqual(context_similarity({}, {"active_reaction": None}), 1)
        self.assertAlmostEqual(features(result.solutions[0].to_dict(), {})[0], (120 - 100) / 200)

    def test_api_reaction_contract_and_cache_isolation(self):
        from web.backend.app import app, get_optimization_cache
        source = catalog((fixed("reactor", {"tear_reaction": 20}),),
                         (armor("armor", {}),), container(1))
        generator = ReactionBuildGenerator(source, "tear", CONFIG, SOLVER)
        client = TestClient(app)
        get_optimization_cache().clear()
        payload = {"budget": 1_000_000, "targets": {"durability": 110}, "active_reaction": "tear"}
        with patch("web.backend.app.get_reaction_generator", return_value=generator) as factory:
            result = client.post("/api/optimize", json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            data = result.json()
            self.assertEqual(data["request"]["active_reaction"], "tear")
            self.assertEqual(data["solutions"][0]["derived"]["durability_with_reaction"], 120)
            self.assertEqual(data["solutions"][0]["loadout_stats"]["vitality"], 0)
            self.assertEqual(data["solutions"][0]["loadout_stats"]["tear_reaction"], 20)
            cached = client.post("/api/optimize", json=payload).json()
            self.assertTrue(cached["diagnostics"]["cache_hit"])
            self.assertEqual(factory.call_count, 1)
        with patch("web.backend.app.get_optimizer", return_value=FrontierBuildGenerator(source, CONFIG, solver_config=SOLVER)):
            normal = client.post("/api/optimize", json={**payload, "active_reaction": None}).json()
            self.assertFalse(normal["solutions"])
            self.assertFalse(normal["diagnostics"]["cache_hit"])
        for reaction in ("chemical", ["tear", "burning"]):
            self.assertEqual(client.post("/api/optimize", json={**payload, "active_reaction": reaction}).status_code, 422)
        from web.backend.app import OptimizePayload
        self.assertEqual(OptimizePayload(budget=100_000, active_reaction="tear").targets, {})
        self.assertEqual(client.post("/api/upgrade-plans", json={"current_build": data["solutions"][0]}).status_code, 422)
        get_optimization_cache().clear()


if __name__ == "__main__":
    unittest.main()
