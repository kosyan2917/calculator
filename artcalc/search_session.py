from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import time
from types import SimpleNamespace

import highspy
import numpy as np


class ReusableMilp:
    """Keep the common matrix prefix and feasible incumbents between solves."""

    def __init__(self):
        self.solver = highspy.Highs()
        self.solver.setOptionValue("output_flag", False)
        self.solver.setOptionValue("threads", 1)
        self.solver.setOptionValue("mip_rel_gap", 0.001)
        self.problem = None
        self.incumbents: list[np.ndarray] = []

    def solve(self, problem: dict, seconds: float):
        h = self.solver
        matrix = problem["matrix"].tocsr()
        n = len(problem["objective"])
        indices = np.arange(n, dtype=np.int32)
        old = self.problem
        if old is None:
            h.addVars(n, problem["lower_bounds"], problem["upper_bounds"])
            h.changeColsIntegrality(n, indices, problem["integrality"].astype(np.uint8))
            prefix = 0
        else:
            h.changeColsBounds(n, indices, problem["lower_bounds"], problem["upper_bounds"])
            previous = old["matrix"]
            prefix = 0
            for row in range(min(matrix.shape[0], previous.shape[0])):
                a, b = matrix.indptr[row:row + 2]
                c, d = previous.indptr[row:row + 2]
                if not (np.array_equal(matrix.indices[a:b], previous.indices[c:d])
                        and np.array_equal(matrix.data[a:b], previous.data[c:d])
                        and problem["constraint_lows"][row] == old["constraint_lows"][row]
                        and problem["constraint_highs"][row] == old["constraint_highs"][row]):
                    break
                prefix += 1
            removed = previous.shape[0] - prefix
            if removed:
                h.deleteRows(removed, np.arange(prefix, previous.shape[0], dtype=np.int32))
        tail = matrix[prefix:]
        if tail.shape[0]:
            h.addRows(tail.shape[0], problem["constraint_lows"][prefix:],
                      problem["constraint_highs"][prefix:], tail.nnz,
                      tail.indptr.astype(np.int32), tail.indices.astype(np.int32), tail.data)
        h.changeColsCost(n, indices, problem["objective"])
        feasible = []
        for x in self.incumbents:
            values = matrix @ x
            if (np.all(x >= problem["lower_bounds"] - 1e-7)
                    and np.all(x <= problem["upper_bounds"] + 1e-7)
                    and np.all(values >= problem["constraint_lows"] - 1e-7)
                    and np.all(values <= problem["constraint_highs"] + 1e-7)):
                feasible.append(x)
        if feasible:
            h.setSolution(n, indices, min(feasible, key=lambda x: problem["objective"] @ x))
        h.setOptionValue("time_limit", max(0.001, seconds))
        h.run()
        info = h.getInfo()
        status = h.getModelStatus()
        valid = info.primal_solution_status == highspy.SolutionStatus.kSolutionStatusFeasible
        x = np.asarray(h.getSolution().col_value) if valid else None
        if x is not None:
            self.incumbents.append(x.copy())
            self.incumbents = self.incumbents[-24:]
        self.problem = problem
        return SimpleNamespace(
            x=x, fun=info.objective_function_value,
            mip_dual_bound=info.mip_dual_bound, mip_gap=info.mip_gap,
            status=0 if status == highspy.HighsModelStatus.kOptimal else
                   2 if status == highspy.HighsModelStatus.kInfeasible else 1,
        )


@dataclass
class SearchSession:
    deadline: float
    models: dict = field(default_factory=dict)
    forms: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    milp_calls: int = 0
    model_reuses: int = 0

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.perf_counter())

    def solve(self, key: tuple, problem: dict, seconds: float):
        key = (*key, len(problem["objective"]))
        if key in self.models:
            self.model_reuses += 1
        else:
            self.models[key] = ReusableMilp()
        self.milp_calls += 1
        return self.models[key].solve(problem, min(seconds, self.remaining()))


current_session: ContextVar[SearchSession | None] = ContextVar("artifact_search", default=None)


@contextmanager
def search_session(seconds: float = 19.0):
    existing = current_session.get()
    if existing is not None:
        yield existing
        return
    session = SearchSession(time.perf_counter() + seconds)
    token = current_session.set(session)
    try:
        yield session
    finally:
        current_session.reset(token)
