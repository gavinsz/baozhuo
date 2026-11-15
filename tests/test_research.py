"""Observable research-tool behavior, including financial and provenance failures."""

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import research


def fixture():
    rows = {
        "2025FY": ["1000", "100", "90", "80"],
        "2025M9": ["600", "60", "50", "40"],
        "2026Q1": ["200", "20", "18", "10"],
        "2026H1": ["500", "70", "60", "50"],
        "2026M9": ["900", "120", "110", "100"],
        "2026Q3": ["400", "50", "50", "50"],
    }
    scenarios = []
    for name, pe in [("bear", "5"), ("base", "10"), ("bull", "15")]:
        target = Decimal(pe)*2
        scenarios.append({
            "name": name, "horizon": "2027-12-31", "classification": "research_assumption",
            "revenue_cny_yi": "0.00002", "normalized_parent_net_margin": "0.1",
            "normalized_parent_net_profit_cny_yi": "0.000002", "pe": pe,
            "terminal_price_cny_not_discounted": str(target),
            "price_change_ex_dividend_fee_tax": str(target/10-1),
        })
    return {
        "schema_version": research.SCHEMA, "as_of": "2026-11-15", "status": "reviewed",
        "ticker": "002008.SZ", "currency": "CNY", "raw_money_unit": "yuan",
        "sources": {"filing": {"date": "2026-10-30", "url": "https://example.org/filing"},
                    "quote": {"date": "2026-11-13", "url": "https://example.org/quote"}},
        "market": {"date": "2026-11-13", "price": "10", "shares_asof": "2026-09-30",
                   "shares": "100", "source_ids": ["quote", "filing"], "market_cap": "1000"},
        "raw": {p: {"source_ids": ["filing"], **dict(zip(research.METRICS, v))}
                for p, v in rows.items()},
        "derived": {"ttm": {"revenue": "1300", "net_parent": "160",
                            "deduct_net_parent": "150", "cfo": "140"},
                    "2026Q2": {"revenue": "300", "net_parent": "50", "deduct_net_parent": "42", "cfo": "40"},
                    "ttm_pe": "6.25"},
        "scenarios": scenarios,
    }


class ResearchChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root/research.COMPANIES/"002008-hans-laser"
        self.folder.mkdir(parents=True)
        self.model = self.folder/"2026-11-15-model.json"
        self.report = self.folder/"2026-11-15.md"
        self.report.write_text("# Research\n\n[Model](2026-11-15-model.json)\n")

    def check(self, model=None):
        self.model.write_text(json.dumps(fixture() if model is None else model))
        return research.Check(self.root, self.model).run()

    def assert_code(self, check, code):
        self.assertIn(code, [f.code for f in check.findings], check.findings)

    def test_complete_case_calculates_ttm_without_mutation(self):
        result = self.check()
        before = self.model.read_bytes()
        self.assertEqual([], result.findings)
        self.assertEqual(Decimal("160"), result.calculated["ttm"]["net_parent"])
        self.assertEqual("2026-09-30", result.calculated["ttm_end"])
        research.Check(self.root, self.model).run()
        self.assertEqual(before, self.model.read_bytes())

    def test_cumulative_as_single_quarter_is_rejected(self):
        m = fixture()
        m["raw"]["2026Q3"]["revenue"] = "900"
        self.assert_code(self.check(m), "arithmetic")

    def test_wrong_ttm_and_market_cap_are_rejected(self):
        for field in ["ttm", "market"]:
            with self.subTest(field=field):
                m = fixture()
                if field == "ttm":
                    m["derived"]["ttm"]["net_parent"] = "120"
                else:
                    m["market"]["market_cap"] = "100"
                self.assert_code(self.check(m), "arithmetic")

    def test_scenario_unit_and_percentage_errors_are_rejected(self):
        for key, value in [("revenue_cny_yi", "2000"),
                           ("normalized_parent_net_margin", "10"),
                           ("price_change_ex_dividend_fee_tax", "100")]:
            with self.subTest(key=key):
                m = fixture()
                m["scenarios"][1][key] = value
                self.assertTrue(self.check(m).findings)

    def test_mixed_scenario_endpoints_are_rejected(self):
        m = fixture()
        m["scenarios"][1]["horizon"] = "2028-12-31"
        self.assert_code(self.check(m), "scenario-date")

    def test_ttm_label_and_missing_comparable_period_are_rejected(self):
        m = fixture()
        m["derived"]["ttm_ending_20260630"] = m["derived"].pop("ttm")
        self.assert_code(self.check(m), "ttm-date")
        m = fixture()
        del m["raw"]["2025M9"]
        self.assert_code(self.check(m), "ttm-gap")

    def test_missing_base_scenario_reports_error_without_crash(self):
        m = fixture()
        m["scenarios"][1]["name"] = "other"
        m["reverse"] = {"base_profit_price_at_pe20": "1"}
        self.assert_code(self.check(m), "scenario-coverage")

    def test_negative_ttm_pe_requires_null(self):
        m = fixture()
        m["raw"]["2025FY"]["net_parent"] = "-1000"
        m["derived"]["ttm"]["net_parent"] = "-940"
        self.assert_code(self.check(m), "undefined-ratio")
        m["derived"]["ttm_pe"] = None
        self.assertEqual([], self.check(m).findings)

    def test_future_evidence_or_price_is_rejected(self):
        for target in ["source", "market", "retrieved"]:
            with self.subTest(target=target):
                m = fixture()
                if target == "source":
                    m["sources"]["filing"]["date"] = "2026-11-16"
                elif target == "retrieved":
                    m["sources"]["filing"]["retrieved_at"] = "2026-11-16"
                else:
                    m["market"]["date"] = "2026-11-16"
                self.assert_code(self.check(m), "lookahead")

    def test_mixed_quote_share_and_auxiliary_dates_are_rejected(self):
        m = fixture()
        m["market"]["shares_asof"] = "2026-11-14"
        self.assert_code(self.check(m), "market-date")
        m = fixture()
        m["balance_sheet"] = {"date": "2026-12-31", "source": "filing"}
        self.assert_code(self.check(m), "lookahead")

    def test_invalid_reverse_multiple_reports_error_without_crash(self):
        m = fixture()
        m["reverse"] = {"profit_cny_yi_for_double_at_pe0": "1"}
        self.assert_code(self.check(m), "invalid-model")

    def test_missing_or_pre_period_source_is_rejected(self):
        m = fixture()
        m["raw"]["2026M9"]["source_ids"] = ["not-in-ledger"]
        self.assert_code(self.check(m), "source-ref")
        m = fixture()
        m["sources"]["filing"]["date"] = "2025-01-01"
        self.assert_code(self.check(m), "source-period")

    def test_nonfinite_float_and_wrong_units_fail(self):
        for value in ["NaN", "Infinity", 10.5, ""]:
            with self.subTest(value=value):
                m = fixture()
                m["market"]["price"] = value
                self.assert_code(self.check(m), "invalid-model")
        m = fixture()
        m["raw_money_unit"] = "yi"
        self.assert_code(self.check(m), "invalid-model")

    def test_duplicate_json_keys_are_rejected(self):
        self.model.write_text('{"ticker":"002008.SZ","ticker":"688536.SH"}')
        self.assert_code(research.Check(self.root, self.model).run(), "invalid-model")

    def test_missing_and_broken_report_are_rejected(self):
        self.report.unlink()
        self.assert_code(self.check(), "missing-report")
        self.report.write_text("[Missing](missing.json)")
        self.assert_code(self.check(), "broken-link")

    def test_snapshot_integrity_and_path_boundary(self):
        snapshot = self.root/"filing.txt"
        snapshot.write_text("original")
        m = fixture()
        m["sources"]["filing"].update(local_path="filing.txt", sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
        self.assertEqual([], self.check(m).findings)
        snapshot.write_text("tampered")
        self.assert_code(self.check(m), "source-hash")
        m["sources"]["filing"]["local_path"] = "../outside.txt"
        self.assert_code(self.check(m), "invalid-model")

    def test_invalid_date_and_share_count_are_rejected(self):
        for key, value in [("shares", "0"), ("shares", "1.5"), ("date", "2026-02-30")]:
            with self.subTest(key=key, value=value):
                m = fixture()
                m["market"][key] = value
                self.assert_code(self.check(m), "invalid-model")

    def test_both_retained_model_formats_pass_without_edits(self):
        models = [research.ROOT/research.COMPANIES/name/"2026-11-15-model.json"
                  for name in ("002008-hans-laser", "688536-3peak")]
        for path in models:
            with self.subTest(path=path):
                before = path.read_bytes()
                self.assertEqual([], research.Check(research.ROOT, path).run().findings)
                self.assertEqual(before, path.read_bytes())

    def test_incorrect_cnc_held_quote_value_is_rejected(self):
        original = research.ROOT/research.COMPANIES/"002008-hans-laser/2026-11-15-model.json"
        m = research.read_json(original)
        m["sotp_reference_only"]["held_A_shares_quote_reference_value"] = "100000000000"
        self.assert_code(self.check(m), "arithmetic")


class DraftCLI(unittest.TestCase):
    def test_same_day_revision_preserves_both_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = research.new_case(root, "002008-hans-laser", "002008.SZ", "2026-11-15", "大族激光")
            original = {path: (root/path).read_bytes() for path in first}
            second = research.new_case(root, "002008-hans-laser", "002008.SZ", "2026-11-15", "大族激光", revision=2)
            self.assertTrue(second[1].endswith("2026-11-15-r2-model.json"))
            result = research.Check(root, root/second[1]).run()
            self.assertEqual(["draft"], [f.code for f in result.findings])
            for path, content in original.items():
                self.assertEqual(content, (root/path).read_bytes())
            with self.assertRaises(ValueError):
                research.new_case(root, "002008-hans-laser", "002008.SZ", "2026-11-15", "大族激光", revision=2)
            for invalid in [0, -1, "2", True]:
                with self.subTest(revision=invalid), self.assertRaises(ValueError):
                    research.new_case(root, "002008-hans-laser", "002008.SZ", "2026-11-15", "大族激光", revision=invalid)

    def test_new_draft_is_explicit_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research.new_case(root, "002008-hans-laser", "002008.SZ", "2026-11-16", "大族激光")
            model = root/created[1]
            data = research.read_json(model)
            self.assertEqual("draft", data["status"])
            self.assertEqual({}, data["raw"])
            before = model.read_bytes()
            with self.assertRaises(ValueError):
                research.new_case(root, "002008-hans-laser", "002008.SZ", "2026-11-16", "大族激光")
            self.assertEqual(before, model.read_bytes())
            command = [sys.executable, str(research.ROOT/"scripts/research.py"), "--root", tmp, "check", "--json"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(1, json.loads(result.stdout)["warnings"])
            strict = subprocess.run(command+["--strict"], capture_output=True, text=True)
            self.assertEqual(1, strict.returncode)

    def test_new_rejects_path_escape_or_ticker_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            for slug, ticker in [("../../escape", "002008.SZ"), ("002008-hans-laser", "688536.SH")]:
                with self.subTest(slug=slug), self.assertRaises(ValueError):
                    research.new_case(Path(tmp), slug, ticker, "2026-11-16", "公司")

    def test_new_rejects_symlinked_company_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            folder = root/research.COMPANIES
            folder.mkdir(parents=True)
            (folder/"002008-hans-laser").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                research.new_case(root, "002008-hans-laser", "002008.SZ", "2026-11-16", "公司")
            self.assertEqual([], list(Path(outside).iterdir()))

    def test_empty_project_fails_instead_of_claiming_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(research.ROOT/"scripts/research.py"), "--root", tmp, "check", "--json"], capture_output=True, text=True)
            self.assertEqual(1, result.returncode)
            self.assertEqual("no-models", json.loads(result.stdout)["findings"][0]["code"])


if __name__ == "__main__":
    unittest.main()
