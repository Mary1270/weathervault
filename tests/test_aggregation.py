import unittest
from _bootstrap import make_contract


class TestExtractDomain(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_bare_domain(self):
        self.assertEqual(self.c._extract_domain("weather.gov"), "weather.gov")

    def test_full_url(self):
        self.assertEqual(
            self.c._extract_domain("https://www.accuweather.com/en/gb/london"),
            "accuweather.com",
        )

    def test_multi_part_suffix(self):
        self.assertEqual(
            self.c._extract_domain("https://weather.example.co.uk/x"), "example.co.uk"
        )


class TestCanonicalReputableDomain(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_exact_match(self):
        self.assertEqual(self.c._canonical_reputable_domain("weather.gov"), "weather.gov")

    def test_subdomain_match(self):
        self.assertEqual(
            self.c._canonical_reputable_domain("forecast.weather.gov"), "weather.gov"
        )

    def test_unrelated_domain(self):
        self.assertIsNone(self.c._canonical_reputable_domain("randomblog.com"))

    def test_lookalike_domain_not_matched(self):
        self.assertIsNone(self.c._canonical_reputable_domain("weather.gov.evil.tld"))


class TestParseMetricValue(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_plain_mm(self):
        self.assertEqual(self.c._parse_metric_value("45mm"), 45.0)

    def test_with_space_and_unit(self):
        self.assertEqual(self.c._parse_metric_value("45.2 millimeters"), 45.2)

    def test_negative_temperature(self):
        self.assertEqual(self.c._parse_metric_value("-5.5C"), -5.5)

    def test_na_returns_none(self):
        self.assertIsNone(self.c._parse_metric_value("N/A"))

    def test_empty_returns_none(self):
        self.assertIsNone(self.c._parse_metric_value(""))

    def test_no_digits_returns_none(self):
        self.assertIsNone(self.c._parse_metric_value("no data available"))

    def test_comma_thousands_stripped(self):
        self.assertEqual(self.c._parse_metric_value("1,024mm"), 1024.0)


class TestNormalizeUnitText(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_celsius_variants(self):
        for text in ("C", "celsius", "°C", "Celsius"):
            self.assertEqual(self.c._normalize_unit_text(text), "C")

    def test_fahrenheit_variants(self):
        for text in ("F", "fahrenheit", "°F"):
            self.assertEqual(self.c._normalize_unit_text(text), "F")

    def test_mm_variants(self):
        for text in ("mm", "millimeters", "millimetres"):
            self.assertEqual(self.c._normalize_unit_text(text), "mm")

    def test_inch_variants(self):
        for text in ("in", "inch", "inches"):
            self.assertEqual(self.c._normalize_unit_text(text), "in")

    def test_unrecognized_returns_none(self):
        self.assertIsNone(self.c._normalize_unit_text("kelvin"))

    def test_empty_returns_none(self):
        self.assertIsNone(self.c._normalize_unit_text(""))


class TestNormalizeMetricValue(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_temp_celsius_passthrough(self):
        self.assertEqual(self.c._normalize_metric_value(23.0, "C", "max_temp_c"), 23.0)

    def test_temp_fahrenheit_converted_to_celsius(self):
        # 73.4F == 23C
        result = self.c._normalize_metric_value(73.4, "F", "max_temp_c")
        self.assertAlmostEqual(result, 23.0, places=5)

    def test_temp_freezing_point_conversion(self):
        result = self.c._normalize_metric_value(32.0, "F", "max_temp_c")
        self.assertAlmostEqual(result, 0.0, places=5)

    def test_temp_rejects_rainfall_unit(self):
        self.assertIsNone(self.c._normalize_metric_value(23.0, "mm", "max_temp_c"))

    def test_temp_rejects_unknown_unit(self):
        self.assertIsNone(self.c._normalize_metric_value(23.0, "kelvin", "max_temp_c"))

    def test_rainfall_mm_passthrough(self):
        self.assertEqual(self.c._normalize_metric_value(45.0, "mm", "rainfall_mm"), 45.0)

    def test_rainfall_cm_converted_to_mm(self):
        self.assertEqual(self.c._normalize_metric_value(4.5, "cm", "rainfall_mm"), 45.0)

    def test_rainfall_inches_converted_to_mm(self):
        result = self.c._normalize_metric_value(1.0, "in", "rainfall_mm")
        self.assertAlmostEqual(result, 25.4, places=5)

    def test_rainfall_rejects_temperature_unit(self):
        self.assertIsNone(self.c._normalize_metric_value(45.0, "C", "rainfall_mm"))


class TestDeterministicVerdict(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_gte_above_threshold_triggers(self):
        self.assertEqual(self.c._deterministic_verdict(75.0, "gte", 50.0), "PayoutTriggered")

    def test_gte_exactly_at_threshold_triggers(self):
        self.assertEqual(self.c._deterministic_verdict(50.0, "gte", 50.0), "PayoutTriggered")

    def test_gte_below_threshold_no_payout(self):
        self.assertEqual(self.c._deterministic_verdict(30.0, "gte", 50.0), "NoPayout")

    def test_lte_below_threshold_triggers(self):
        self.assertEqual(self.c._deterministic_verdict(-10.0, "lte", 0.0), "PayoutTriggered")

    def test_lte_above_threshold_no_payout(self):
        self.assertEqual(self.c._deterministic_verdict(5.0, "lte", 0.0), "NoPayout")


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def _row(self, verdict, quality="ok"):
        return {"own_verdict": verdict, "quality_flag": quality}

    def test_indeterminate_below_min_sources(self):
        verdict, meta = self.c._aggregate([self._row("PayoutTriggered")])
        self.assertEqual(verdict, "Indeterminate")
        self.assertEqual(meta["independent_total"], 1)

    def test_two_agreeing_trigger(self):
        rows = [self._row("PayoutTriggered"), self._row("PayoutTriggered")]
        verdict, _ = self.c._aggregate(rows)
        self.assertEqual(verdict, "PayoutTriggered")

    def test_two_agreeing_no_payout(self):
        rows = [self._row("NoPayout"), self._row("NoPayout")]
        verdict, _ = self.c._aggregate(rows)
        self.assertEqual(verdict, "NoPayout")

    def test_tied_split_is_indeterminate(self):
        rows = [self._row("PayoutTriggered"), self._row("NoPayout")]
        verdict, _ = self.c._aggregate(rows)
        self.assertEqual(verdict, "Indeterminate")

    def test_non_ok_quality_excluded(self):
        rows = [
            self._row("PayoutTriggered"),
            self._row("PayoutTriggered", quality="stale_or_unknown_freshness"),
        ]
        verdict, meta = self.c._aggregate(rows)
        self.assertEqual(verdict, "Indeterminate")
        self.assertEqual(meta["independent_total"], 1)


if __name__ == "__main__":
    unittest.main()
