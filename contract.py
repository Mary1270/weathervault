# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json


class WeatherVault(gl.Contract):
    """
    WeatherVault - Pooled, multi-source-verified parametric weather
    insurance with a real shared GEN underwriting vault.

    -------------------------------------------------------------------
    WHY THIS CONTRACT EXISTS
    -------------------------------------------------------------------
    This is a new Intelligent Contract, but it exists to close the one
    limitation my previous contract, FlightShield, disclosed and
    explicitly left open:

        "Peer-to-peer only, no pooled underwriting. This is
        deliberately a bet between two named wallets, not a
        marketplace with a shared liquidity pool, partial fills, or
        premium pricing. A pooled version would need its own
        solvency/collateralization model - out of scope for a first
        version."

    WeatherVault IS that pooled version. Instead of two named wallets
    staking equal amounts against each other, any number of
    underwriters deposit GEN into a single shared vault and receive
    shares (a standard proportional-ownership vault, the same pattern
    used by lending/vault protocols) representing their claim on the
    pool. Policyholders then pay a premium (much smaller than the
    coverage amount) to open a policy against a real, multi-source-
    verified weather trigger. If the trigger is confirmed, the
    policyholder is paid the FULL coverage amount out of the shared
    pool - drawn from many underwriters' capital, not one counter-
    party's matched stake. If it isn't triggered, the premium stays in
    the pool, raising the value of every underwriter's shares.

    -------------------------------------------------------------------
    THE SOLVENCY MODEL (the part FlightShield said was "out of scope")
    -------------------------------------------------------------------
    A pooled insurer can promise more coverage than it can actually
    pay if it isn't careful. WeatherVault enforces solvency with one
    simple invariant, checked on every state change:

        pool_balance >= locked_amount

    `locked_amount` is the sum of `coverage_amount` across every
    currently-active policy - GEN that is spoken for but not yet paid
    out. `create_policy` may only open a new policy if
    `pool_balance - locked_amount >= coverage_amount` (there is enough
    *unlocked* capital to fully cover it). `withdraw` may only pay out
    an underwriter up to `pool_balance - locked_amount` (underwriters
    can always take out their own money - just never money that is
    backing someone else's active policy). Both checks use the same
    single source of truth, so the pool can never promise more than it
    holds, and an underwriter can never accidentally strand an active
    policyholder by withdrawing coverage out from under them.

    -------------------------------------------------------------------
    WHAT'S REUSED FROM FLIGHTSHIELD / THE ORACLE CONTRACTS
    -------------------------------------------------------------------
    The evidence pipeline is architecturally identical to FlightShield
    and the OilPriceOracle/GoldPriceOracle line: a small static
    reputable-domain allowlist, 2+ independent sources required,
    gl.eq_principle.prompt_comparative for LLM-classification
    consensus, and deterministic (Python, not LLM) numeric parsing of
    the metric value - applied to weather-station readings instead of
    prices or flight status.

    -------------------------------------------------------------------
    WHAT'S GENUINELY NEW HERE
    -------------------------------------------------------------------
      - A share-based vault (`deposit` / `withdraw`) instead of a
        single locked stake - real proportional-ownership accounting,
        the first primitive in this portfolio that isn't a 1:1 bet.
      - `locked_amount` reserve accounting enforcing pool solvency
        across MANY simultaneously-active policies, not just one.
      - Premiums, not stakes: a policyholder risks a small premium for
        a large coverage amount, funded by many underwriters' pooled
        capital - the actual shape of real insurance, not a symmetric
        wager.

    -------------------------------------------------------------------
    DISCLOSED LIMITATION CARRIED FORWARD (being upfront about it)
    -------------------------------------------------------------------
    Like FlightShield, WeatherVault has no trusted on-chain clock, so
    an `active` policy whose evidence can never again show FRESHNESS
    as `Current` (because the event date has receded too far into the
    past for any tracking site to still call it "current") has no
    forced-expiry release path in this version - the underwriter
    capital backing it stays `locked` indefinitely. FlightShield fixed
    its own equivalent stranding problem with `request_cancel`
    (mutual consent between exactly two parties); a pooled version
    needs a different mechanism (there is no single "other party" to
    get consent from), which is intentionally left for a future
    submission rather than rushed here.
    """

    # ------------------------------------------------------------------
    # Persistent on-chain storage
    # ------------------------------------------------------------------
    underwriter_shares: TreeMap[str, str]
    total_shares: u256
    pool_balance: u256
    locked_amount: u256
    policies: TreeMap[str, str]
    policy_count: u256

    # ------------------------------------------------------------------
    # Fixed vocabularies
    # ------------------------------------------------------------------
    LOCATION_MATCH_WORDS = ("Match", "Mismatch", "Unclear")
    FRESHNESS_WORDS = ("Current", "Stale", "Unknown")
    FETCH_STATUSES = ("ok", "empty", "timeout", "inaccessible", "malformed")
    METRICS = ("rainfall_mm", "max_temp_c")
    COMPARISONS = ("gte", "lte")
    FINAL_VERDICTS = ("PayoutTriggered", "NoPayout", "Indeterminate")
    POLICY_STATUSES = ("active", "resolved_paid", "resolved_nopay")

    REPUTABLE_WEATHER_DOMAINS = frozenset(
        {
            "weather.gov",
            "accuweather.com",
            "wunderground.com",
            "timeanddate.com",
            "weather.com",
        }
    )

    KNOWN_MULTI_PART_SUFFIXES = ("co.uk", "com.au", "co.jp", "com.br")

    MIN_SOURCES_SUBMITTED = 3
    MAX_SOURCES_SUBMITTED = 6
    MIN_INDEPENDENT_SOURCES = 2

    EQUIVALENCE_PRINCIPLE = (
        "The result is a JSON object classifying a single weather-data "
        "web page. Two results are equivalent if and only if they agree "
        "on every categorical field (fetch_status, location_match, "
        "freshness) and, taking UNIT into account, on the METRIC_VALUE "
        "field's real-world meaning (e.g. '23' with unit 'C' and '73.4' "
        "with unit 'F' are equivalent; '23' with unit 'C' and '30' with "
        "unit 'C' are not). Minor wording differences in free-text "
        "fields do not affect equivalence."
    )

    def __init__(self):
        self.total_shares = u256(0)
        self.pool_balance = u256(0)
        self.locked_amount = u256(0)
        self.policy_count = u256(0)

    # ==================================================================
    # UNDERWRITER VAULT
    # ==================================================================

    @gl.public.write.payable
    def deposit(self) -> str:
        """
        Any address may become an underwriter by depositing GEN.
        Shares are minted proportionally to the pool's value BEFORE
        this deposit, so earlier underwriters' shares appreciate as
        premiums accumulate - a standard vault share mechanic.
        """
        amount = int(gl.message.value)
        if amount <= 0:
            raise Exception("deposit requires a positive amount (attach GEN value).")

        sender = str(gl.message.sender_address)

        if int(self.total_shares) == 0:
            minted = amount
        else:
            minted = (amount * int(self.total_shares)) // int(self.pool_balance)
            if minted <= 0:
                raise Exception(
                    "deposit amount too small to mint any shares at the current "
                    "pool valuation."
                )

        current = int(self.underwriter_shares.get(sender, "0"))
        self.underwriter_shares[sender] = str(current + minted)
        self.total_shares = u256(int(self.total_shares) + minted)
        self.pool_balance = u256(int(self.pool_balance) + amount)

        return json.dumps(self._vault_summary(sender))

    @gl.public.write
    def withdraw(self, shares: int) -> str:
        """
        Redeem shares for GEN. Only ever limited by how much of the
        pool is NOT currently locked backing active policies - an
        underwriter's own capital is always theirs, but never capital
        that is promised to a policyholder right now.
        """
        if shares is None or int(shares) <= 0:
            raise Exception("withdraw requires a positive share amount.")
        shares = int(shares)

        sender = str(gl.message.sender_address)
        held = int(self.underwriter_shares.get(sender, "0"))
        if shares > held:
            raise Exception(f"You hold {held} shares, cannot withdraw {shares}.")

        total_shares = int(self.total_shares)
        pool_balance = int(self.pool_balance)
        amount = (shares * pool_balance) // total_shares

        available = pool_balance - int(self.locked_amount)
        if amount > available:
            raise Exception(
                f"Only {available} GEN is unlocked right now; the rest of the pool "
                "is backing active policies. Try a smaller withdrawal."
            )

        remaining = held - shares
        if remaining == 0:
            del self.underwriter_shares[sender]
        else:
            self.underwriter_shares[sender] = str(remaining)
        self.total_shares = u256(total_shares - shares)
        self.pool_balance = u256(pool_balance - amount)

        gl.get_contract_at(Address(sender)).emit_transfer(value=amount)

        return json.dumps(self._vault_summary(sender))

    # ==================================================================
    # POLICIES
    # ==================================================================

    @gl.public.write.payable
    def create_policy(
        self,
        city: str,
        metric: str,
        comparison: str,
        threshold: str,
        event_date: str,
        coverage_amount: int,
        description: str,
    ) -> str:
        """
        Opens a parametric weather policy. The caller pays a premium
        (gl.message.value) and, if the pool has enough UNLOCKED
        capital, is guaranteed `coverage_amount` GEN if the committed
        trigger is later verified true.
        """
        premium = int(gl.message.value)
        if premium <= 0:
            raise Exception("create_policy requires a positive premium (attach GEN value).")

        city = (city or "").strip()
        if not city:
            raise Exception("city is required.")

        if metric not in self.METRICS:
            raise Exception(f"metric must be one of {self.METRICS}.")
        if comparison not in self.COMPARISONS:
            raise Exception(f"comparison must be one of {self.COMPARISONS}.")

        try:
            threshold_value = float(threshold)
        except (TypeError, ValueError):
            raise Exception("threshold must be a numeric string.")

        event_date = (event_date or "").strip()
        if not event_date:
            raise Exception("event_date is required.")

        if coverage_amount is None or int(coverage_amount) <= 0:
            raise Exception("coverage_amount must be a positive integer.")
        coverage_amount = int(coverage_amount)

        pool_balance = int(self.pool_balance) + premium
        available = pool_balance - int(self.locked_amount)
        if coverage_amount > available:
            raise Exception(
                f"Insufficient pool capital: {available} GEN is unlocked, but this "
                f"policy needs {coverage_amount} GEN of coverage."
            )

        self.pool_balance = u256(pool_balance)
        self.locked_amount = u256(int(self.locked_amount) + coverage_amount)

        policy_id = str(int(self.policy_count))
        self.policy_count = u256(int(self.policy_count) + 1)

        record = {
            "policy_id": policy_id,
            "status": "active",
            "policyholder_address": str(gl.message.sender_address),
            "premium": str(premium),
            "coverage_amount": str(coverage_amount),
            "city": city,
            "metric": metric,
            "comparison": comparison,
            "threshold": threshold_value,
            "event_date": event_date,
            "description": (description or "").strip(),
            "resolution_attempts": 0,
            "records": [],
            "final_verdict": None,
        }
        self.policies[policy_id] = json.dumps(record)
        return json.dumps(record)

    @gl.public.write
    def resolve_policy(self, policy_id: str, source_urls: list[str]) -> str:
        """
        Runs the multi-source fetch -> LLM classification ->
        deterministic aggregation pipeline. On PayoutTriggered, pays
        the policyholder out of the shared pool and releases the
        reserve. On NoPayout, releases the reserve and the premium
        stays in the pool for underwriters. Callable by anyone - the
        payout destination and amount were both fixed at
        create_policy time, never supplied by the resolver.
        """
        record = self._load_policy(policy_id)
        if record["status"] != "active":
            raise Exception(f"policy {policy_id} is '{record['status']}', not active.")

        if not isinstance(source_urls, list):
            raise Exception("source_urls must be a list of URLs.")
        if len(source_urls) < self.MIN_SOURCES_SUBMITTED:
            raise Exception(
                f"resolve_policy requires at least {self.MIN_SOURCES_SUBMITTED} source_urls."
            )
        if len(source_urls) > self.MAX_SOURCES_SUBMITTED:
            raise Exception(
                f"resolve_policy accepts at most {self.MAX_SOURCES_SUBMITTED} source_urls."
            )

        annotated = self._annotate_sources(source_urls)
        distinct_reputable = {
            a["canonical_domain"]
            for a in annotated
            if a["is_reputable"] and not a["is_duplicate_domain"]
        }
        if len(distinct_reputable) < self.MIN_INDEPENDENT_SOURCES:
            raise Exception(
                f"At least {self.MIN_INDEPENDENT_SOURCES} distinct reputable weather "
                "domains are required before any page is fetched."
            )

        classified = self._classify_all_sources(annotated, record)
        verdict, meta = self._aggregate(classified)

        record["records"] = classified
        record["final_verdict"] = verdict
        record["resolution_attempts"] = int(record["resolution_attempts"]) + 1
        record["independent_source_count"] = meta["independent_total"]

        if verdict == "Indeterminate":
            self.policies[policy_id] = json.dumps(record)
            return json.dumps(record)

        coverage_amount = int(record["coverage_amount"])
        self.locked_amount = u256(int(self.locked_amount) - coverage_amount)

        if verdict == "PayoutTriggered":
            self.pool_balance = u256(int(self.pool_balance) - coverage_amount)
            record["status"] = "resolved_paid"
            self.policies[policy_id] = json.dumps(record)
            gl.get_contract_at(Address(record["policyholder_address"])).emit_transfer(
                value=coverage_amount
            )
        else:
            record["status"] = "resolved_nopay"
            self.policies[policy_id] = json.dumps(record)

        return json.dumps(record)

    # ==================================================================
    # PUBLIC VIEW METHODS
    # ==================================================================

    @gl.public.view
    def get_policy(self, policy_id: str) -> str:
        return json.dumps(self._load_policy(policy_id))

    @gl.public.view
    def get_underwriter(self, address: str) -> str:
        return json.dumps(self._vault_summary((address or "").strip()))

    @gl.public.view
    def vault_state(self) -> str:
        return json.dumps(
            {
                "total_shares": str(self.total_shares),
                "pool_balance": str(self.pool_balance),
                "locked_amount": str(self.locked_amount),
                "available": str(int(self.pool_balance) - int(self.locked_amount)),
            }
        )

    @gl.public.view
    def total_policies(self) -> int:
        return int(self.policy_count)

    # ==================================================================
    # INTERNAL HELPERS
    # ==================================================================

    def _vault_summary(self, address: str) -> dict:
        shares = int(self.underwriter_shares.get(address, "0"))
        total_shares = int(self.total_shares)
        pool_balance = int(self.pool_balance)
        value = (shares * pool_balance) // total_shares if total_shares > 0 else 0
        return {
            "address": address,
            "shares": str(shares),
            "estimated_value": str(value),
        }

    def _load_policy(self, policy_id: str) -> dict:
        raw = self.policies.get(str(policy_id))
        if raw is None:
            raise Exception(f"No policy with id '{policy_id}'.")
        return json.loads(raw)

    def _extract_domain(self, url_or_domain: str) -> str:
        text = (url_or_domain or "").strip().lower()
        text = text.split("://", 1)[-1]
        text = text.split("/", 1)[0]
        text = text.split(":", 1)[0]
        text = text.split("?", 1)[0]
        text = text.split("#", 1)[0]
        if text.startswith("www."):
            text = text[4:]
        parts = text.split(".")
        if len(parts) >= 3:
            last_two = ".".join(parts[-2:])
            if last_two in self.KNOWN_MULTI_PART_SUFFIXES:
                return ".".join(parts[-3:])
        return text

    def _canonical_reputable_domain(self, domain: str):
        if domain in self.REPUTABLE_WEATHER_DOMAINS:
            return domain
        for rep in self.REPUTABLE_WEATHER_DOMAINS:
            if domain.endswith("." + rep):
                return rep
        return None

    def _annotate_sources(self, source_urls: list[str]) -> list[dict]:
        seen_canonical_domains = set()
        annotated = []
        for url in source_urls:
            domain = self._extract_domain(url)
            canonical = self._canonical_reputable_domain(domain)
            is_reputable = canonical is not None
            is_duplicate = is_reputable and canonical in seen_canonical_domains
            if is_reputable and not is_duplicate:
                seen_canonical_domains.add(canonical)
            annotated.append(
                {
                    "url": url,
                    "domain": domain,
                    "canonical_domain": canonical,
                    "is_reputable": is_reputable,
                    "is_duplicate_domain": is_duplicate,
                }
            )
        return annotated

    def _classify_all_sources(self, annotated: list[dict], record: dict) -> list[dict]:
        return [self._classify_one_source(entry, record) for entry in annotated]

    def _classify_one_source(self, entry: dict, record: dict) -> dict:
        url = entry["url"]
        city = record["city"]
        event_date = record["event_date"]
        metric = record["metric"]

        def nondet():
            try:
                content = gl.nondet.web.render(url, mode="text")
            except Exception:
                return {"fetch_status": "inaccessible"}
            if content is None:
                return {"fetch_status": "inaccessible"}
            content = content.strip() if isinstance(content, str) else str(content)
            if not content:
                return {"fetch_status": "empty"}

            prompt = self._build_prompt(content, city, event_date, metric)
            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            parsed = raw if isinstance(raw, dict) else json.loads(raw)

            return {
                "fetch_status": "ok",
                "location_match": parsed.get("LOCATION_MATCH", "Unclear"),
                "freshness": parsed.get("FRESHNESS", "Unknown"),
                "metric_value": parsed.get("METRIC_VALUE", ""),
                "unit": parsed.get("UNIT", "Unknown"),
            }

        try:
            result = gl.eq_principle.prompt_comparative(
                nondet, principle=self.EQUIVALENCE_PRINCIPLE
            )
        except Exception:
            result = {"fetch_status": "malformed"}

        fetch_status = result.get("fetch_status", "malformed")
        if fetch_status not in self.FETCH_STATUSES:
            fetch_status = "malformed"

        record_out = {
            "url": url,
            "domain": entry["domain"],
            "is_duplicate_domain": entry["is_duplicate_domain"],
            "is_reputable": entry["is_reputable"],
            "fetch_status": fetch_status,
            "own_verdict": None,
            "metric_value": None,
            "raw_metric_value": None,
            "raw_unit": None,
            "quality_flag": None,
        }

        if fetch_status != "ok" or not entry["is_reputable"] or entry["is_duplicate_domain"]:
            record_out["quality_flag"] = "ok" if fetch_status == "ok" else fetch_status
            return record_out

        location_match = result.get("location_match", "Unclear")
        if location_match not in self.LOCATION_MATCH_WORDS:
            location_match = "Unclear"
        freshness = result.get("freshness", "Unknown")
        if freshness not in self.FRESHNESS_WORDS:
            freshness = "Unknown"
        metric_text = result.get("metric_value", "")
        unit_text = result.get("unit", "Unknown")

        record_out["location_match"] = location_match
        record_out["freshness"] = freshness

        if location_match != "Match":
            record_out["quality_flag"] = "location_or_date_mismatch"
            return record_out
        if freshness != "Current":
            record_out["quality_flag"] = "stale_or_unknown_freshness"
            return record_out

        raw_value = self._parse_metric_value(metric_text)
        if raw_value is None:
            record_out["quality_flag"] = "metric_unparseable"
            return record_out
        record_out["raw_metric_value"] = raw_value
        record_out["raw_unit"] = unit_text

        value = self._normalize_metric_value(raw_value, unit_text, record["metric"])
        if value is None:
            record_out["quality_flag"] = "unit_unclear"
            return record_out

        record_out["metric_value"] = value
        record_out["quality_flag"] = "ok"
        record_out["own_verdict"] = self._deterministic_verdict(
            value, record["comparison"], float(record["threshold"])
        )
        return record_out

    def _parse_metric_value(self, text):
        text = (text or "").strip().lower()
        if not text:
            return None
        text = text.replace(",", "")
        num = ""
        seen_digit = False
        seen_dot = False
        for ch in text:
            if ch.isdigit():
                num += ch
                seen_digit = True
            elif ch == "." and not seen_dot and seen_digit:
                num += ch
                seen_dot = True
            elif ch == "-" and not num:
                num += ch
            elif seen_digit:
                break
        if not num or num == "-":
            return None
        try:
            return float(num)
        except ValueError:
            return None

    # Canonical unit each metric is stored/compared in, and the set of
    # units the LLM is allowed to report for it. Every value that
    # crosses the consensus boundary is converted deterministically
    # (in Python, never by the LLM) into the canonical unit before
    # ever being compared to a threshold - this is what stops two
    # sources reporting "23C" and "88F" (a real 8-degree difference,
    # not a typo) from silently agreeing just because the raw numbers
    # were never actually put on a common footing.
    METRIC_CANONICAL_UNIT = {
        "max_temp_c": "C",
        "rainfall_mm": "mm",
    }
    METRIC_ACCEPTED_UNITS = {
        "max_temp_c": ("C", "F"),
        "rainfall_mm": ("mm", "cm", "in"),
    }

    def _normalize_metric_value(self, value: float, unit_text: str, metric: str):
        """
        Converts `value` from whatever unit the LLM reported into the
        canonical unit for `metric`. Returns None (caller sets
        quality_flag='unit_unclear') if the reported unit isn't a
        recognized unit for this metric at all - an unparseable or
        missing unit is never silently assumed to already be
        canonical, since that assumption is exactly what caused a real
        23C/88F mismatch to slip through undetected during live
        testing.
        """
        unit = self._normalize_unit_text(unit_text)
        accepted = self.METRIC_ACCEPTED_UNITS.get(metric, ())
        if unit not in accepted:
            return None

        canonical = self.METRIC_CANONICAL_UNIT[metric]
        if unit == canonical:
            return value

        if metric == "max_temp_c":
            # unit must be "F" here (the only non-canonical option)
            return (value - 32.0) * 5.0 / 9.0

        if metric == "rainfall_mm":
            if unit == "cm":
                return value * 10.0
            if unit == "in":
                return value * 25.4

        return None

    def _normalize_unit_text(self, unit_text: str):
        text = (unit_text or "").strip().lower()
        if text in ("c", "celsius", "°c"):
            return "C"
        if text in ("f", "fahrenheit", "°f"):
            return "F"
        if text in ("mm", "millimeter", "millimeters", "millimetre", "millimetres"):
            return "mm"
        if text in ("cm", "centimeter", "centimeters", "centimetre", "centimetres"):
            return "cm"
        if text in ("in", "inch", "inches", '"'):
            return "in"
        return None

    def _deterministic_verdict(self, value: float, comparison: str, threshold: float) -> str:
        if comparison == "gte":
            return "PayoutTriggered" if value >= threshold else "NoPayout"
        return "PayoutTriggered" if value <= threshold else "NoPayout"

    def _aggregate(self, classified: list[dict]):
        eligible = [c for c in classified if c.get("quality_flag") == "ok"]
        triggered = sum(1 for c in eligible if c["own_verdict"] == "PayoutTriggered")
        no_payout = sum(1 for c in eligible if c["own_verdict"] == "NoPayout")
        independent_total = len(eligible)

        meta = {"independent_total": independent_total}

        if independent_total < self.MIN_INDEPENDENT_SOURCES:
            return "Indeterminate", meta
        if triggered >= self.MIN_INDEPENDENT_SOURCES and triggered > no_payout:
            return "PayoutTriggered", meta
        if no_payout >= self.MIN_INDEPENDENT_SOURCES and no_payout > triggered:
            return "NoPayout", meta
        return "Indeterminate", meta

    def _build_prompt(self, content: str, city: str, event_date: str, metric: str) -> str:
        metric_label = {
            "rainfall_mm": "total rainfall in millimeters",
            "max_temp_c": "maximum temperature in Celsius",
        }.get(metric, metric)

        return f"""You are checking a weather web page as evidence for an
on-chain parametric weather policy. Respond with ONLY a JSON object,
no other text.

Location being checked: {city}
Date being checked: {event_date}
Metric needed: {metric_label}

Page content:
---
{content[:6000]}
---

Return exactly this JSON shape:
{{
  "LOCATION_MATCH": "Match" | "Mismatch" | "Unclear",
  "FRESHNESS": "Current" | "Stale" | "Unknown",
  "METRIC_VALUE": "<the numeric {metric_label} as stated on the page, e.g. '42', 'N/A' if not present>",
  "UNIT": "<the unit the page actually used, e.g. 'C', 'F', 'mm', 'in' - whatever unit is on the page, do not convert it>"
}}

Rules:
- LOCATION_MATCH is "Match" only if this page is clearly about weather
  in {city} specifically (not a different city or a generic page).
- FRESHNESS is "Current" only if the page appears to reflect the
  actual weather data for {event_date} specifically, not a different
  date or a multi-day forecast average.
- METRIC_VALUE must be copied/paraphrased from the page, never
  invented. Do not do any unit conversion or arithmetic yourself -
  just report the number as shown on the page.
- UNIT must be the unit exactly as shown on the page (do not assume
  or convert it - if the page shows Fahrenheit, report "F", even
  though the metric requested is in Celsius).
- If the page is not about real weather data at all, set
  LOCATION_MATCH to "Unclear".
"""
