"""Tests for the procurement world.

WorldContract (from the opsharness core) checks the generic promises: the
correct plan scores 1.0, doing nothing scores 0.0, approvals get enforced,
the harness survives injected model slips, and the world scores 1.0 over MCP.
The wrong agents below are procurement-specific mistakes a real model could make,
and each one has to lose points.
"""
import unittest

from opsharness.testing import WorldContract, play, strip_approvals
from opsharness_procurement.world import ProcurementEnv

SEEDS = range(100)


class Contract(WorldContract, unittest.TestCase):
    world = ProcurementEnv


class WrongAgents(unittest.TestCase):
    def test_procurement_cheapest_ignoring_deadline_and_stock(self):
        for s in SEEDS:
            env = ProcurementEnv(s)
            plan = [(t, a) for t, a in strip_approvals(env.oracle_plan()) if t == "reserve_stock"]
            for o in env.truth["orders"]:
                cheap = min(env.offers[o["part"]], key=lambda x: x["unit_price"])
                plan.append(("place_order", {"supplier": cheap["supplier"], "part": o["part"],
                                             "qty": max(o["qty"], cheap["moq"]), "request_ids": o["request_ids"]}))
            env.limit = 10 ** 9  # isolate the supplier choice from the gate
            self.assertLess(play(env, plan)["score"], 1.0, f"seed {s}")
    def test_procurement_no_consolidation(self):
        for s in SEEDS:
            env = ProcurementEnv(s)
            env.limit = 10 ** 9
            plan = [(t, a) for t, a in env.oracle_plan() if t != "place_order"]
            for o in env.truth["orders"]:
                for rid in o["request_ids"]:
                    rq = next(r for r in env.requests if r["id"] == rid)
                    need = rq["qty"] - env.truth["reserve"].get(rid, 0)
                    off = next(x for x in env.offers[o["part"]] if x["supplier"] == o["supplier"])
                    plan.append(("place_order", {"supplier": o["supplier"], "part": o["part"],
                                                 "qty": max(need, off["moq"]), "request_ids": [rid]}))
            self.assertLess(play(env, plan)["score"], 1.0, f"seed {s}")
    def test_procurement_skips_shelf(self):
        for s in SEEDS:
            env = ProcurementEnv(s)
            plan = [(t, a) for t, a in env.oracle_plan() if t != "reserve_stock"]
            self.assertLess(play(env, plan)["score"], 1.0, f"seed {s}")


if __name__ == "__main__":
    unittest.main()
