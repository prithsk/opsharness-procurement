"""Procurement for a hardware team (Waybill-style).

Engineers file part requests. The agent checks the shelf first, reserves
stock, consolidates the shortfall per part into one order, picks the
cheapest supplier that can meet stock, MOQ and deadline, gets approval for
orders over the spend limit, and flags parts nobody can supply in time.
"""
from opsharness.core import Env, Tool, ToolError

PARTS = ["STM32F405 MCU", "BMI088 IMU", "SMA RF connector", "NEMA17 stepper",
         "4S LiPo 5000mAh", "TCAN1042 CAN transceiver", "M3x8 screw 100pk",
         "TPS54331 buck converter", "u-blox M10 GNSS", "Samtec 20-pin header"]
SUPPLIERS = ["Northwind Components", "Voltline", "Kestrel Supply", "Arcfield Electronics"]
PEOPLE = [("Ana", "avionics"), ("Ben", "ground-station"), ("Chen", "gimbal"),
          ("Dara", "power"), ("Eli", "avionics"), ("Fatima", "test-rig")]


class ProcurementEnv(Env):
    name = "procurement"

    def build(self):
        r = self.rng
        for _ in range(50):
            self._generate()
            self.truth = self._solve()
            if self.truth is not None:
                break
        totals = [o["total"] for o in self.truth["orders"]]
        self.limit = 500.0
        for cand in (500.0, 250.0, 100.0, 50.0):
            if totals and max(totals) > cand:
                self.limit = cand
                break
        self.reservations, self.orders, self.flagged = {}, [], {}

    def _bin(self):
        return f"{self.rng.choice('ABC')}{self.rng.randint(1, 9)}"

    def _generate(self):
        r = self.rng
        a, b, c, d, e = r.sample(PARTS, 5)
        self.shelf = {p: {"qty": 0, "bin": None} for p in PARTS}
        self.offers = {p: [] for p in PARTS}
        for p in (a, b, c, d, e):
            for s in r.sample(SUPPLIERS, 3):
                self.offers[p].append({"supplier": s, "unit_price": round(r.uniform(2, 60), 2),
                                       "lead_days": r.randint(2, 8), "stock": r.randint(40, 400),
                                       "moq": r.choice([1, 1, 5, 10])})
        reqs = []
        qa = r.randint(2, 8)
        self.shelf[a] = {"qty": qa + r.randint(0, 6), "bin": self._bin()}
        reqs.append((a, qa, r.randint(6, 14)))
        sb = r.randint(1, 4)
        self.shelf[b] = {"qty": sb, "bin": self._bin()}
        reqs.append((b, sb + r.randint(3, 10), r.randint(9, 14)))
        reqs.append((c, r.randint(2, 8), r.randint(9, 14)))
        reqs.append((c, r.randint(2, 8), r.randint(9, 14)))
        # d: the cheapest offer is too slow for the deadline
        dl = r.randint(4, 6)
        reqs.append((d, r.randint(3, 9), dl))
        od = sorted(self.offers[d], key=lambda o: o["unit_price"])
        od[0]["lead_days"] = dl + r.randint(2, 5)
        od[1]["lead_days"] = r.randint(2, dl)
        # e: cheapest lacks stock, or nobody can make the deadline
        qe = r.randint(20, 60)
        de = r.randint(5, 8)
        reqs.append((e, qe, de))
        oe = sorted(self.offers[e], key=lambda o: o["unit_price"])
        if r.random() < 0.5:
            oe[0]["stock"] = r.randint(1, qe - 1)
            oe[0]["lead_days"] = r.randint(2, de)
            oe[1]["lead_days"] = r.randint(2, de)
            oe[1]["stock"] = max(oe[1]["stock"], qe + 10)
        else:
            for o in oe:
                o["lead_days"] = de + r.randint(1, 6)
        r.shuffle(reqs)
        self.requests = []
        for i, (part, qty, days) in enumerate(reqs, 1):
            who, proj = r.choice(PEOPLE)
            self.requests.append({"id": f"R{i}", "requester": who, "project": proj,
                                  "part": part, "qty": qty, "needed_within_days": days})

    def _solve(self):
        left = {p: v["qty"] for p, v in self.shelf.items()}
        reserve, short = {}, {}
        for rq in self.requests:
            p = rq["part"]
            take = min(left[p], rq["qty"])
            if take:
                reserve[rq["id"]] = take
                left[p] -= take
            if rq["qty"] - take > 0:
                short.setdefault(p, []).append((rq["id"], rq["qty"] - take, rq["needed_within_days"]))
        orders, unfillable = [], []
        for p, lst in short.items():
            need = sum(x[1] for x in lst)
            deadline = min(x[2] for x in lst)
            cands = []
            for o in self.offers[p]:
                q = max(need, o["moq"])
                if o["stock"] >= q and o["lead_days"] <= deadline:
                    cands.append((round(o["unit_price"] * q, 2), o["supplier"], q))
            if not cands:
                unfillable.append(p)
                continue
            cands.sort()
            if len(cands) > 1 and abs(cands[0][0] - cands[1][0]) < 0.5:
                return None  # near tie, regenerate so the answer is unique
            total, sup, q = cands[0]
            orders.append({"supplier": sup, "part": p, "qty": q, "total": total,
                           "request_ids": sorted(x[0] for x in lst)})
        return {"reserve": reserve, "orders": orders, "unfillable": unfillable}

    # ---- tools -----------------------------------------------------------
    def _req(self, rid):
        for rq in self.requests:
            if rq["id"] == rid:
                return rq
        raise ToolError(f"no request '{rid}'")

    def _part(self, part):
        if part not in PARTS:
            raise ToolError(f"unknown part '{part}'. Use the exact part name from the request.")
        return part

    def _offer(self, supplier, part):
        for o in self.offers[self._part(part)]:
            if o["supplier"] == supplier:
                return o
        raise ToolError(f"{supplier} does not sell {part}")

    def _available(self, part):
        used = sum(q for rid, q in self.reservations.items() if self._req(rid)["part"] == part)
        return self.shelf[part]["qty"] - used

    def list_requests(self):
        return {"requests": self.requests}

    def check_shelf(self, part):
        self._part(part)
        return {"part": part, "available": self._available(part), "bin": self.shelf[part]["bin"]}

    def search_suppliers(self, part):
        return {"part": part, "offers": self.offers[self._part(part)]}

    def reserve_stock(self, request_id, qty):
        rq = self._req(request_id)
        if qty <= 0:
            raise ToolError("qty must be positive")
        if qty > self._available(rq["part"]):
            raise ToolError(f"only {self._available(rq['part'])} available on the shelf")
        self.reservations[request_id] = self.reservations.get(request_id, 0) + qty
        return {"reserved": qty, "request_id": request_id, "bin": self.shelf[rq["part"]]["bin"]}

    def place_order(self, supplier, part, qty, request_ids):
        o = self._offer(supplier, part)
        for rid in request_ids:
            if self._req(rid)["part"] != part:
                raise ToolError(f"request {rid} is not for {part}")
        if qty < o["moq"]:
            raise ToolError(f"minimum order quantity is {o['moq']}")
        if qty > o["stock"]:
            raise ToolError(f"supplier only has {o['stock']} in stock")
        oid = f"PO-{1000 + len(self.orders)}"
        self.orders.append({"id": oid, "supplier": supplier, "part": part, "qty": qty,
                            "request_ids": sorted(request_ids), "total": round(o["unit_price"] * qty, 2)})
        return {"order_id": oid, "status": "placed", "eta_days": o["lead_days"]}

    def _order_gated(self, args):
        try:
            return self._offer(args["supplier"], args["part"])["unit_price"] * args["qty"] > self.limit
        except (ToolError, KeyError):
            return False

    def flag_unfillable(self, part, reason):
        self.flagged[self._part(part)] = reason
        return {"flagged": part}

    def env_tools(self):
        s = {"type": "string"}
        return [
            Tool("list_requests", "List open part requests from engineers.", {}, self.list_requests),
            Tool("check_shelf", "Units of a part on your own shelf that are not yet reserved.",
                 {"part": s}, self.check_shelf, ["part"]),
            Tool("search_suppliers", "Supplier offers for a part: price, lead time, stock, MOQ.",
                 {"part": s}, self.search_suppliers, ["part"]),
            Tool("reserve_stock", "Reserve shelf units for a request.",
                 {"request_id": s, "qty": {"type": "integer"}}, self.reserve_stock, ["request_id", "qty"]),
            Tool("place_order", "Place one purchase order covering one or more requests for the same part.",
                 {"supplier": s, "part": s, "qty": {"type": "integer"},
                  "request_ids": {"type": "array", "items": s}},
                 self.place_order, ["supplier", "part", "qty", "request_ids"], gated=self._order_gated),
            Tool("flag_unfillable", "Escalate a part no supplier can deliver in time.",
                 {"part": s, "reason": s}, self.flag_unfillable, ["part", "reason"]),
        ]

    def task(self):
        return (f"Work through every open part request. Orders with a total above "
                f"${self.limit:.0f} need approval before you place them. Stop when every request is "
                f"covered by shelf stock, an order, or an escalation.")

    # ---- answer key and scoring -------------------------------------------
    def oracle_plan(self):
        t = self.truth
        plan = [(tool, args) for rid, q in sorted(t["reserve"].items())
                for tool, args in [("reserve_stock", {"request_id": rid, "qty": q})]]
        for o in t["orders"]:
            plan += self.gated_call("place_order", {"supplier": o["supplier"], "part": o["part"],
                                                    "qty": o["qty"], "request_ids": o["request_ids"]})
        for p in t["unfillable"]:
            plan.append(("flag_unfillable", {"part": p, "reason": "no supplier meets the deadline"}))
        return plan

    def score(self):
        t = self.truth
        key = lambda o: (o["supplier"], o["part"], o["qty"], tuple(sorted(o["request_ids"])))
        truth_orders = {key(o) for o in t["orders"]}
        placed = [key(o) for o in self.orders]
        correct = 0
        for rq in self.requests:
            rid, p = rq["id"], rq["part"]
            ok = self.reservations.get(rid, 0) == t["reserve"].get(rid, 0)
            mine = [k for k in placed if rid in k[3]]
            want = [k for k in truth_orders if rid in k[3]]
            if p in t["unfillable"]:
                ok = ok and p in self.flagged and not mine
            else:
                ok = ok and mine == want
            correct += ok
        extra = sum(1 for k in placed if k not in truth_orders) + max(0, len(placed) - len(set(placed)))
        wrong_flags = sum(1 for p in self.flagged if p not in t["unfillable"])
        n = len(self.requests)
        score = max(0.0, correct / n - 0.25 * (extra + wrong_flags))
        return {"score": round(score, 4), "details": {
            "requests_correct": correct, "requests": n, "extra_orders": extra, "wrong_flags": wrong_flags,
            "spend": round(sum(o["total"] for o in self.orders), 2),
            "oracle_spend": round(sum(o["total"] for o in t["orders"]), 2)}}
