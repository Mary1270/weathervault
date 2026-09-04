# WeatherVault

A pooled, multi-source-verified **parametric weather insurance**
Intelligent Contract for GenLayer, with a real shared GEN
underwriting vault.

This is a new Intelligent Contract, built specifically to close the
one limitation my previous contract, FlightShield, disclosed and
explicitly left open:

> "Peer-to-peer only, no pooled underwriting. This is deliberately a
> bet between two named wallets, not a marketplace with a shared
> liquidity pool, partial fills, or premium pricing. A pooled version
> would need its own solvency/collateralization model - out of scope
> for a first version."

WeatherVault is that pooled version. See the full rationale in
`contract.py`'s class docstring.

## How it's different from FlightShield

| FlightShield | WeatherVault |
|---|---|
| Two named wallets, symmetric stakes | Any number of underwriters, proportional shares |
| Winner takes the whole pot | Policyholder gets a fixed `coverage_amount`; the rest stays with underwriters |
| No solvency model needed (1:1 bet) | `pool_balance >= locked_amount` invariant enforced on every state change |
| `request_cancel` (mutual consent, 2 parties) | No forced-expiry path yet (disclosed limitation - see below) |

## How it works

1. **`deposit()`** (payable) - Anyone becomes an underwriter by
   depositing GEN. Shares mint proportionally to the pool's value
   *before* the deposit (standard vault-share mechanic), so earlier
   underwriters' shares appreciate as premiums accumulate.
2. **`withdraw(shares)`** - Redeem shares for GEN, limited to
   `pool_balance - locked_amount` (an underwriter's own capital is
   always theirs, but never capital backing an active policy).
3. **`create_policy(...)`** (payable) - A policyholder pays a premium
   and requests `coverage_amount` of coverage against a weather
   trigger (city, metric, comparison, threshold, date). Only succeeds
   if the pool has enough *unlocked* capital; on success,
   `coverage_amount` is reserved (`locked_amount`) and the premium
   joins the pool immediately.
4. **`resolve_policy(policy_id, source_urls)`** - Same multi-source
   fetch → LLM classification → deterministic aggregation pipeline as
   FlightShield/OilPriceOracle, applied to weather-station readings.
   Requires 2+ independent reputable sources to agree. On
   `PayoutTriggered`, pays the policyholder out of the shared pool via
   `emit_transfer` and releases the reserve. On `NoPayout`, releases
   the reserve and the premium stays in the pool, raising every
   underwriter's share value.

## The solvency model

A pooled insurer can promise more than it can pay if it isn't
careful. WeatherVault enforces one invariant everywhere:

```
pool_balance >= locked_amount
```

`locked_amount` is the sum of `coverage_amount` across every currently
`active` policy. `create_policy` may only reserve coverage if
`pool_balance - locked_amount >= coverage_amount`. `withdraw` may only
pay out up to `pool_balance - locked_amount`. Both checks share the
same source of truth, so the pool can never be oversold, and no
underwriter can accidentally strand an active policyholder by
withdrawing capital out from under them.

## Reputable source allowlist

```
weather.gov
accuweather.com
wunderground.com
timeanddate.com
weather.com
```

Same static-allowlist, subdomain-aware matching (`_canonical_reputable_domain`)
as FlightShield - a regional/mirror subdomain of an allowlisted
domain still counts as that same source.

## Supported metrics (v1)

- `rainfall_mm` - total rainfall in millimeters
- `max_temp_c` - maximum temperature in Celsius

Comparison is `gte` (payout if actual >= threshold) or `lte` (payout
if actual <= threshold) - covers both "too much rain" and "too cold /
not enough rain" parametric triggers with one deterministic rule.

## Core GenLayer building blocks used

1. `gl.nondet.web.render()` / `gl.nondet.exec_prompt()` - trustless
   web access + in-contract LLM classification
2. `gl.eq_principle.prompt_comparative()` - Optimistic Democracy
   consensus on LLM-derived output
3. `@gl.public.write.payable` + `gl.message.value` - real GEN escrow
   for both underwriter deposits and policy premiums
4. `gl.message.sender_address` - identity binding (underwriter
   identity for withdraw, policyholder identity fixed at creation)
5. `gl.get_contract_at(Address(...)).emit_transfer(value=...)` -
   paying underwriters on withdraw and policyholders on a triggered
   claim

## Testing

75 offline unit tests across two files, run with plain `unittest`:

```bash
cd tests
python3 -m unittest test_aggregation test_end_to_end -v
```

- `test_aggregation.py` - pure deterministic logic: domain extraction
  and subdomain matching, metric-value text parsing (`"45mm"`,
  `"1,024mm"`, `"-5.5C"`), the `gte`/`lte` deterministic verdict rule,
  and the 2-of-N aggregation rule.
- `test_end_to_end.py` - full contract flow through the offline SDK
  stub, including:
  - vault share mechanics (first depositor gets 1:1 shares, later
    depositors get proportionally fewer as the pool grows from
    premiums, repeat deposits accumulate correctly)
  - withdraw limits (can't withdraw more than held, can't withdraw
    locked capital, can withdraw exactly what's available when
    partially locked)
  - `create_policy` validation (premium, city, metric/comparison
    vocabulary, numeric threshold, positive coverage, and the
    available-pool-capital check)
  - all three resolution outcomes with exact pool-balance and
    `emit_transfer` assertions (`PayoutTriggered` pays the
    policyholder and shrinks the pool; `NoPayout` releases the lock
    and keeps the premium; `Indeterminate` changes nothing)
  - a dedicated test proving the resolver's identity never affects
    the payout destination
  - a `TestSolvencyInvariant` class exercising the core guarantee
    directly: multiple simultaneous policies cannot collectively
    over-lock the pool, and an underwriter cannot withdraw below the
    locked floor even across several active policies

## Live testing plan (GenLayer Studio)

1. Deploy `contract.py`.
2. From 2-3 different addresses, call `deposit()` with different GEN
   amounts; confirm `get_underwriter(address)` and `vault_state()`
   reflect correct proportional shares.
3. From a policyholder address, call `create_policy` for a real city
   and a near-term date with a modest `coverage_amount`; confirm
   `vault_state()` shows `locked_amount` increased and `pool_balance`
   increased by the premium.
4. Try to `withdraw` more than the currently-available (unlocked)
   amount from an underwriter address; confirm it's rejected.
5. After the event date, call `resolve_policy` with 3 real
   weather.gov / accuweather.com / timeanddate.com URLs for that city
   and date; confirm the payout (or lack thereof) matches the real
   weather and that `locked_amount` drops back down either way.

### Actually done: results from live testing on Studio

Deployed and exercised end-to-end with two underwriters and two real
policies (a `weather.gov`-only-covers-the-US mismatch on the first
attempt taught the obvious lesson: `city` has to actually match what
the source covers). Confirmed: proportional share minting for two
underwriters, `create_policy` locking coverage and crediting the
premium into `pool_balance` immediately, and a real `PayoutTriggered`
resolution that paid the policyholder out of the shared pool via
`emit_transfer` while correctly decrementing both `pool_balance` and
`locked_amount`.

### Bug found and fixed during live testing: unit mismatch across sources

The first successful live resolution exposed a real bug: `weather.gov`
reported "23" and `wunderground.com` reported "88" for the same
reading - one in Celsius, one in Fahrenheit (73.4F, not 88F, would
have matched; 88F is a real ~8C-equivalent discrepancy, not just a
unit label difference). The original `_parse_metric_value` only
stripped the numeral and silently assumed every source already used
the contract's canonical unit, so both values passed the (very
permissive, `threshold=0`) test and the policy paid out - correctly,
in that specific case, but only by luck, since the two source values
were never actually placed on the same footing before being compared.

Fixed by adding a `UNIT` field to the LLM's required JSON output (the
model reports whatever unit is actually on the page, never converts
it itself) and a deterministic `_normalize_metric_value()` step in
Python that converts every value to the metric's canonical unit
(Celsius for `max_temp_c`, millimeters for `rainfall_mm`) before any
comparison happens. A source reporting a unit that isn't recognized
for its metric is excluded from consensus (`quality_flag:
"unit_unclear"`) rather than guessed at. Verified with a dedicated
test (`test_sources_reporting_different_units_still_reach_consensus`)
that reconstructs the exact 23C/73.4F scenario and asserts both
values now normalize to the same canonical temperature.

## Known limitations

- **No forced-expiry release path.** Unlike FlightShield's
  `request_cancel` (mutual consent between exactly two parties),
  WeatherVault has no equivalent yet - a policy whose evidence can
  never again show `FRESHNESS: Current` stays `active` and its
  `coverage_amount` stays locked indefinitely, tying up underwriter
  capital with no on-chain recovery. There's no single "other party"
  to get consent from in a pooled model, so this needs a different
  mechanism (e.g. underwriter-majority vote, or a long content-based
  staleness proof) - intentionally left for a future submission
  rather than rushed here.
- **No trusted on-chain clock**, for the same reason documented in
  FlightShield: `gl.block.timestamp` does not exist, so staleness is
  judged from page content (`FRESHNESS`), not a block timestamp.
- **Two metrics only** (`rainfall_mm`, `max_temp_c`) in this version;
  adding more (wind speed, snowfall) is a straightforward vocabulary
  extension but each needs its own realistic unit-parsing coverage.
- **Static allowlist**, same trade-off as every prior contract in this
  portfolio - a hand-maintained list of 5 domains, not a live
  reputation system.
- **No re-insurance or per-policy risk pricing.** All underwriters
  share risk uniformly via the vault; there's no mechanism for an
  underwriter to back only specific policies or price premiums by
  risk - the premium amount is whatever the policyholder chooses to
  pay, not computed by the contract.
