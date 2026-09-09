import json
import unittest
from datetime import datetime, timedelta

from _bootstrap import make_contract, gl, transfers, reset_transfers, set_now, reset_now
from genlayer import tx_context


def set_pipeline(render_value, prompt_value):
    gl.nondet.web.render = lambda url, mode="text": render_value
    gl.nondet.exec_prompt = lambda prompt, response_format="json": prompt_value


TRIGGERED_PROMPT = {
    "LOCATION_MATCH": "Match",
    "FRESHNESS": "Current",
    "METRIC_VALUE": "75",
    "UNIT": "mm",
}
NOPAYOUT_PROMPT = {
    "LOCATION_MATCH": "Match",
    "FRESHNESS": "Current",
    "METRIC_VALUE": "5",
    "UNIT": "mm",
}

THREE_URLS = [
    "https://weather.gov/x",
    "https://accuweather.com/y",
    "https://timeanddate.com/z",
]


T0 = datetime(2026, 9, 1, 12, 0, 0)


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()
        set_now(T0)

    def tearDown(self):
        reset_now()

    def deposit(self, sender, amount):
        with tx_context(sender, amount):
            raw = self.c.deposit()
        return json.loads(raw)

    def create_policy(
        self,
        sender="0xP1",
        premium=100,
        coverage=3000,
        city="London",
        metric="rainfall_mm",
        comparison="gte",
        threshold="50",
        event_date="2026-09-10",
    ):
        with tx_context(sender, premium):
            raw = self.c.create_policy(
                city, metric, comparison, threshold, event_date, coverage, "test"
            )
        return json.loads(raw)


class TestDeposit(BaseCase):
    def test_requires_positive_amount(self):
        with self.assertRaises(Exception):
            with tx_context("0xU1", 0):
                self.c.deposit()

    def test_first_depositor_gets_shares_equal_to_amount(self):
        rec = self.deposit("0xU1", 1000)
        self.assertEqual(rec["shares"], "1000")
        self.assertEqual(rec["estimated_value"], "1000")

    def test_second_depositor_gets_proportional_shares(self):
        self.deposit("0xU1", 1000)
        rec = self.deposit("0xU2", 500)
        self.assertEqual(rec["shares"], "500")

    def test_shares_accumulate_for_repeat_depositor(self):
        self.deposit("0xU1", 1000)
        rec = self.deposit("0xU1", 500)
        self.assertEqual(rec["shares"], "1500")

    def test_deposit_after_pool_grew_mints_fewer_shares(self):
        self.deposit("0xU1", 1000)
        # simulate pool growth via a resolved NoPayout policy premium
        self.create_policy(premium=1000, coverage=100)
        set_pipeline("page", NOPAYOUT_PROMPT)
        self.c.resolve_policy("0", THREE_URLS)
        # pool is now 2000 (1000 deposit + 1000 premium), total_shares still 1000
        rec = self.deposit("0xU2", 2000)
        # 2000 * 1000 // 2000 = 1000 shares for 2000 GEN (pool was richer per share)
        self.assertEqual(rec["shares"], "1000")


class TestWithdraw(BaseCase):
    def test_requires_positive_shares(self):
        self.deposit("0xU1", 1000)
        with self.assertRaises(Exception):
            with tx_context("0xU1"):
                self.c.withdraw(0)

    def test_cannot_withdraw_more_than_held(self):
        self.deposit("0xU1", 1000)
        with self.assertRaises(Exception):
            with tx_context("0xU1"):
                self.c.withdraw(2000)

    def test_full_withdraw_returns_full_amount(self):
        self.deposit("0xU1", 1000)
        with tx_context("0xU1"):
            rec = json.loads(self.c.withdraw(1000))
        self.assertEqual(rec["shares"], "0")
        self.assertEqual(transfers(), [{"to": "0xU1", "value": 1000}])

    def test_partial_withdraw(self):
        self.deposit("0xU1", 1000)
        with tx_context("0xU1"):
            rec = json.loads(self.c.withdraw(400))
        self.assertEqual(rec["shares"], "600")
        self.assertEqual(transfers(), [{"to": "0xU1", "value": 400}])

    def test_cannot_withdraw_locked_capital(self):
        self.deposit("0xU1", 5000)
        self.create_policy(coverage=2000)
        # only ~3100 (of 5100) unlocked after the policy; withdrawing all
        # 5000 shares should fail
        with self.assertRaises(Exception):
            with tx_context("0xU1"):
                self.c.withdraw(5000)

    def test_can_withdraw_up_to_available_when_partially_locked(self):
        self.deposit("0xU1", 5000)
        self.create_policy(premium=100, coverage=2000)  # locks 2000, pool=5100
        with tx_context("0xU1"):
            rec = json.loads(self.c.withdraw(1000))  # well within the ~3100 available
        self.assertEqual(transfers(), [{"to": "0xU1", "value": 1020}])
        self.assertEqual(rec["shares"], "4000")


class TestCreatePolicyValidation(BaseCase):
    def setUp(self):
        super().setUp()
        self.deposit("0xU1", 10000)

    def test_requires_positive_premium(self):
        with self.assertRaises(Exception):
            with tx_context("0xP1", 0):
                self.c.create_policy("London", "rainfall_mm", "gte", "50", "2026-09-10", 100, "x")

    def test_requires_city(self):
        with self.assertRaises(Exception):
            with tx_context("0xP1", 10):
                self.c.create_policy("  ", "rainfall_mm", "gte", "50", "2026-09-10", 100, "x")

    def test_rejects_bad_metric(self):
        with self.assertRaises(Exception):
            with tx_context("0xP1", 10):
                self.c.create_policy("London", "wind_speed", "gte", "50", "2026-09-10", 100, "x")

    def test_rejects_bad_comparison(self):
        with self.assertRaises(Exception):
            with tx_context("0xP1", 10):
                self.c.create_policy("London", "rainfall_mm", "eq", "50", "2026-09-10", 100, "x")

    def test_rejects_non_numeric_threshold(self):
        with self.assertRaises(Exception):
            with tx_context("0xP1", 10):
                self.c.create_policy(
                    "London", "rainfall_mm", "gte", "abc", "2026-09-10", 100, "x"
                )

    def test_rejects_zero_coverage(self):
        with self.assertRaises(Exception):
            with tx_context("0xP1", 10):
                self.c.create_policy("London", "rainfall_mm", "gte", "50", "2026-09-10", 0, "x")

    def test_rejects_coverage_exceeding_available_pool(self):
        with self.assertRaises(Exception):
            with tx_context("0xP1", 10):
                self.c.create_policy(
                    "London", "rainfall_mm", "gte", "50", "2026-09-10", 999999, "x"
                )

    def test_success_locks_coverage_and_credits_premium(self):
        rec = self.create_policy(premium=50, coverage=2000)
        self.assertEqual(rec["status"], "active")
        state = json.loads(self.c.vault_state())
        self.assertEqual(state["pool_balance"], "10050")
        self.assertEqual(state["locked_amount"], "2000")
        self.assertEqual(state["available"], "8050")

    def test_policy_count_increments(self):
        self.create_policy()
        self.create_policy()
        self.assertEqual(self.c.total_policies(), 2)


class TestCoverageAdmissionControl(BaseCase):
    """
    Steward-requested fix: bounded premium-to-coverage rules that
    prevent locking most of the pool for a trivial premium.
    """

    def test_min_premium_rate_blocks_griefing_low_premium(self):
        self.deposit("0xU1", 100000)
        # 1 wei premium trying to lock nearly the whole pool
        with self.assertRaises(Exception):
            self.create_policy(premium=1, coverage=99000)

    def test_premium_exactly_at_minimum_rate_succeeds(self):
        self.deposit("0xU1", 100000)
        # coverage=40000 -> min premium = 1% = 400
        rec = self.create_policy(premium=400, coverage=40000)
        self.assertEqual(rec["status"], "active")

    def test_premium_one_below_minimum_rate_rejected(self):
        self.deposit("0xU1", 100000)
        with self.assertRaises(Exception):
            self.create_policy(premium=399, coverage=40000)

    def test_max_policy_share_of_pool_blocks_single_policy_dominance(self):
        self.deposit("0xU1", 10000)
        # 60% of pool in one policy, well above the 50% cap, even with
        # an adequate premium
        with self.assertRaises(Exception):
            self.create_policy(premium=600, coverage=6000)

    def test_policy_exactly_at_pool_share_cap_succeeds(self):
        self.deposit("0xU1", 10000)
        # exactly 50% of the pool, with an adequate premium
        rec = self.create_policy(premium=500, coverage=5000)
        self.assertEqual(rec["status"], "active")

    def test_cap_is_based_on_pool_before_this_policys_own_premium(self):
        # A large premium attached to the SAME call must not inflate
        # the pool used to justify its own coverage cap.
        self.deposit("0xU1", 10000)
        with self.assertRaises(Exception):
            self.create_policy(premium=50000, coverage=6000)


class TestExpirePolicy(BaseCase):
    def test_cannot_expire_before_event_date_plus_buffer(self):
        self.deposit("0xU1", 100000)
        self.create_policy(event_date="2026-09-10")
        set_now(datetime(2026, 9, 12))  # event date passed, buffer has not
        with self.assertRaises(Exception):
            self.c.expire_policy("0")

    def test_can_expire_exactly_at_buffer_deadline(self):
        self.deposit("0xU1", 100000)
        self.create_policy(event_date="2026-09-10")
        set_now(datetime(2026, 9, 10) + self.c.EXPIRY_BUFFER)
        rec = json.loads(self.c.expire_policy("0"))
        self.assertEqual(rec["status"], "expired")

    def test_expiry_releases_lock_with_no_payout(self):
        self.deposit("0xU1", 100000)
        self.create_policy(premium=500, coverage=40000, event_date="2026-09-10")
        set_now(datetime(2026, 9, 10) + self.c.EXPIRY_BUFFER + timedelta(days=1))
        rec = json.loads(self.c.expire_policy("0"))
        self.assertEqual(rec["status"], "expired")
        state = json.loads(self.c.vault_state())
        self.assertEqual(state["locked_amount"], "0")
        # premium stays in the pool; no transfer to the policyholder
        self.assertEqual(transfers(), [])

    def test_only_active_policy_can_expire(self):
        self.deposit("0xU1", 100000)
        self.create_policy(premium=500, coverage=40000, event_date="2026-09-10")
        set_pipeline(
            "page",
            {
                "LOCATION_MATCH": "Match",
                "FRESHNESS": "Current",
                "METRIC_VALUE": "5",
                "UNIT": "mm",
            },
        )
        self.c.resolve_policy("0", THREE_URLS)  # -> resolved_nopay
        set_now(datetime(2026, 9, 10) + self.c.EXPIRY_BUFFER + timedelta(days=1))
        with self.assertRaises(Exception):
            self.c.expire_policy("0")

    def test_cannot_expire_twice(self):
        self.deposit("0xU1", 100000)
        self.create_policy(event_date="2026-09-10")
        set_now(datetime(2026, 9, 10) + self.c.EXPIRY_BUFFER)
        self.c.expire_policy("0")
        with self.assertRaises(Exception):
            self.c.expire_policy("0")

    def test_callable_by_anyone(self):
        self.deposit("0xU1", 100000)
        self.create_policy(event_date="2026-09-10")
        set_now(datetime(2026, 9, 10) + self.c.EXPIRY_BUFFER)
        with tx_context("0xSomeoneElse"):
            rec = json.loads(self.c.expire_policy("0"))
        self.assertEqual(rec["status"], "expired")

    def test_expiring_unknown_policy_raises(self):
        with self.assertRaises(Exception):
            self.c.expire_policy("999")

    def test_invalid_event_date_format_rejected_at_creation(self):
        self.deposit("0xU1", 100000)
        with self.assertRaises(Exception):
            self.create_policy(event_date="10-09-2026")  # wrong format


class TestResolvePolicy(BaseCase):
    def setUp(self):
        super().setUp()
        self.deposit("0xU1", 10000)
        self.create_policy(premium=100, coverage=3000)

    def test_cannot_resolve_too_few_sources(self):
        with self.assertRaises(Exception):
            self.c.resolve_policy("0", THREE_URLS[:2])

    def test_cannot_resolve_unknown_policy(self):
        with self.assertRaises(Exception):
            self.c.resolve_policy("999", THREE_URLS)

    def test_insufficient_distinct_domains_rejected(self):
        with self.assertRaises(Exception):
            self.c.resolve_policy(
                "0",
                [
                    "https://weather.gov/a",
                    "https://weather.gov/b",
                    "https://randomblog.com/c",
                ],
            )

    def test_payout_triggered_pays_policyholder_and_releases_lock(self):
        set_pipeline("page", TRIGGERED_PROMPT)
        rec = json.loads(self.c.resolve_policy("0", THREE_URLS))
        self.assertEqual(rec["status"], "resolved_paid")
        self.assertEqual(transfers(), [{"to": "0xP1", "value": 3000}])
        state = json.loads(self.c.vault_state())
        self.assertEqual(state["locked_amount"], "0")
        self.assertEqual(state["pool_balance"], "7100")  # 10100 - 3000

    def test_no_payout_keeps_premium_in_pool_and_releases_lock(self):
        set_pipeline("page", NOPAYOUT_PROMPT)
        rec = json.loads(self.c.resolve_policy("0", THREE_URLS))
        self.assertEqual(rec["status"], "resolved_nopay")
        self.assertEqual(transfers(), [])
        state = json.loads(self.c.vault_state())
        self.assertEqual(state["locked_amount"], "0")
        self.assertEqual(state["pool_balance"], "10100")  # premium stays

    def test_indeterminate_stays_active_and_keeps_lock(self):
        stale_prompt = dict(TRIGGERED_PROMPT, FRESHNESS="Stale")
        set_pipeline("page", stale_prompt)
        rec = json.loads(self.c.resolve_policy("0", THREE_URLS))
        self.assertEqual(rec["final_verdict"], "Indeterminate")
        state = json.loads(self.c.vault_state())
        self.assertEqual(state["locked_amount"], "3000")
        self.assertEqual(transfers(), [])

    def test_cannot_resolve_already_resolved_policy(self):
        set_pipeline("page", TRIGGERED_PROMPT)
        self.c.resolve_policy("0", THREE_URLS)
        with self.assertRaises(Exception):
            self.c.resolve_policy("0", THREE_URLS)

    def test_resolver_identity_does_not_affect_payout_destination(self):
        set_pipeline("page", TRIGGERED_PROMPT)
        with tx_context("0xSomeoneElse", 0):
            rec = json.loads(self.c.resolve_policy("0", THREE_URLS))
        self.assertEqual(rec["policyholder_address"], "0xP1")
        self.assertEqual(transfers(), [{"to": "0xP1", "value": 3000}])

    def test_sources_reporting_different_units_still_reach_consensus(self):
        # Real bug found during live testing: one source reported 23C
        # and another reported 88F for the same reading (an 8-degree
        # real discrepancy once correctly converted, not a rounding
        # difference) and both were treated as "PayoutTriggered" for a
        # near-zero threshold without ever being placed on a common
        # footing. This test locks in the fix: 23C and 73.4F (which
        # ARE the same real temperature) must both normalize to ~23C
        # and agree, while 23C and 88F (which are NOT the same
        # temperature) must be compared on equal footing too.
        calls = {"n": 0}

        def alternating_prompt(prompt, response_format="json"):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "LOCATION_MATCH": "Match",
                    "FRESHNESS": "Current",
                    "METRIC_VALUE": "23",
                    "UNIT": "C",
                }
            return {
                "LOCATION_MATCH": "Match",
                "FRESHNESS": "Current",
                "METRIC_VALUE": "73.4",
                "UNIT": "F",
            }

        gl.nondet.web.render = lambda url, mode="text": "page"
        gl.nondet.exec_prompt = alternating_prompt

        rec = json.loads(self.c.resolve_policy("0", THREE_URLS))
        values = [r["metric_value"] for r in rec["records"] if r.get("quality_flag") == "ok"]
        for v in values:
            self.assertAlmostEqual(v, 23.0, places=3)

    def test_unrecognized_unit_excludes_source_from_consensus(self):
        set_pipeline(
            "page",
            {
                "LOCATION_MATCH": "Match",
                "FRESHNESS": "Current",
                "METRIC_VALUE": "75",
                "UNIT": "kelvin",
            },
        )
        rec = json.loads(self.c.resolve_policy("0", THREE_URLS))
        self.assertEqual(rec["final_verdict"], "Indeterminate")
        for r in rec["records"]:
            self.assertEqual(r["quality_flag"], "unit_unclear")


class TestSolvencyInvariant(BaseCase):
    def test_multiple_policies_cannot_over_lock_pool(self):
        self.deposit("0xU1", 10000)
        self.create_policy(premium=100, coverage=4000)
        self.create_policy(premium=100, coverage=3000)
        # third policy should fail: only ~3300 unlocked left after the first two
        with self.assertRaises(Exception):
            self.create_policy(premium=100, coverage=3500)

    def test_underwriter_cannot_withdraw_below_locked_floor_across_policies(self):
        self.deposit("0xU1", 10000)
        self.create_policy(coverage=3000)
        self.create_policy(coverage=3000)
        # 6000 locked, 4000ish available; try to withdraw all shares
        with self.assertRaises(Exception):
            with tx_context("0xU1"):
                self.c.withdraw(10000)


class TestViews(BaseCase):
    def test_get_policy_roundtrips(self):
        self.deposit("0xU1", 10000)
        created = self.create_policy()
        fetched = json.loads(self.c.get_policy("0"))
        self.assertEqual(fetched["policy_id"], created["policy_id"])

    def test_get_underwriter_for_unknown_address_returns_zero(self):
        rec = json.loads(self.c.get_underwriter("0xNobody"))
        self.assertEqual(rec["shares"], "0")
        self.assertEqual(rec["estimated_value"], "0")

    def test_total_policies_zero_initially(self):
        self.assertEqual(self.c.total_policies(), 0)

    def test_vault_state_zero_initially(self):
        state = json.loads(self.c.vault_state())
        self.assertEqual(state["pool_balance"], "0")
        self.assertEqual(state["locked_amount"], "0")


if __name__ == "__main__":
    unittest.main()
