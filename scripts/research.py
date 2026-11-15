#!/usr/bin/env python3
"""Create versioned research drafts and check financial models offline (stdlib only)."""

import argparse
import calendar
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, DecimalException, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
COMPANIES = Path("references/research/companies")
SCHEMA = "baozhuo-research/v1"
METRICS = ("revenue", "net_parent", "deduct_net_parent", "cfo")
ALIASES = {"net_profit_parent": "net_parent",
           "net_profit_parent_ex_nonrecurring": "deduct_net_parent"}
YI = Decimal("100000000")


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str


def number(value):
    """Require decimal strings, rejecting NaN/Infinity and accidental binary floats."""
    if not isinstance(value, str):
        raise ValueError("numeric inputs must be decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("non-finite decimal")
    return result


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"expected ISO date, got {value!r}")
    return date.fromisoformat(value)


def period_end(period):
    match = re.fullmatch(r"(\d{4})(FY|H1|M9|Q[1-4])", period)
    if not match:
        raise ValueError(f"unsupported calendar-year period: {period}")
    year = int(match[1])
    month = {"FY": 12, "H1": 6, "M9": 9}.get(match[2])
    if month is None:
        month = int(match[2][1:]) * 3
    return date(year, month, calendar.monthrange(year, month)[1])


def case_stem(as_of, revision=1):
    day(as_of)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision must be a positive integer")
    return as_of + (f"-r{revision}" if revision > 1 else "")


def inside(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"path leaves project root: {path}")
    return resolved


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    def invalid_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")
    return json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=unique_object, parse_constant=invalid_constant)


def json_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)


class Check:
    def __init__(self, root, path):
        self.root, self.path = root.resolve(), path.resolve()
        self.findings = []
        self.calculated = {}

    def add(self, code, message, severity="error"):
        self.findings.append(Finding(severity, code, str(self.path.relative_to(self.root)), message))

    def equal(self, label, actual, expected, tolerance=Decimal("0.01")):
        if expected is None:
            if actual is not None:
                self.add("undefined-ratio", f"{label}: denominator <= 0; PE/ratio must be null")
        elif actual is None or abs(number(actual) - expected) > tolerance:
            self.add("arithmetic", f"{label}: stored={actual}, calculated={expected}")

    def ratio(self, actuals, key, numerator, denominator):
        expected = numerator / denominator if denominator > 0 else None
        if key in actuals:
            self.equal(key, actuals[key], expected, Decimal("0.00000001"))

    def sources(self, model, cutoff):
        sources = model.get("sources", {})
        if not isinstance(sources, dict):
            raise ValueError("sources must be an object")
        for sid, source in sources.items():
            published = day(source["date"])
            if published > cutoff:
                self.add("lookahead", f"source {sid} published after research as_of")
            url = urlparse(source["url"])
            if url.scheme not in {"http", "https"} or not url.netloc:
                self.add("source-url", f"source {sid} requires an http(s) URL")
            if source.get("retrieved_at") and day(source["retrieved_at"]) > cutoff:
                self.add("lookahead", f"source {sid} retrieved after research as_of")
            digest = source.get("sha256", source.get("downloaded_pdf_sha256"))
            if digest and not re.fullmatch(r"[a-f0-9]{64}", digest):
                self.add("source-hash", f"source {sid}: invalid SHA-256")
            if source.get("local_path"):
                local = inside(self.root, self.root / source["local_path"])
                if not local.is_file():
                    self.add("source-file", f"source {sid}: local snapshot missing")
                elif not digest or hashlib.sha256(local.read_bytes()).hexdigest() != digest:
                    self.add("source-hash", f"source {sid}: snapshot hash missing or mismatched")
        return sources

    def references(self, ids, sources, label, earliest=None):
        if not isinstance(ids, list) or not ids or not all(isinstance(s, str) for s in ids):
            self.add("source-ref", f"{label}: require nonempty source_ids")
            return
        for sid in ids:
            if sid not in sources:
                self.add("source-ref", f"{label}: unknown source {sid}")
            elif earliest and day(sources[sid]["date"]) < earliest:
                self.add("source-period", f"{label}: source {sid} predates period end")

    def report(self):
        report = self.path.with_name(self.path.name.replace("-model.json", ".md"))
        if not report.is_file():
            self.add("missing-report", "model requires a paired dated Markdown report")
            return
        for target in re.findall(r"\]\(([^)]+)\)", report.read_text(encoding="utf-8")):
            target = target.strip("<>").split("#", 1)[0]
            if not target or urlparse(target).scheme:
                continue
            local = inside(self.root, report.parent / unquote(target))
            if not local.exists():
                self.add("broken-link", f"report target missing: {target}")

    def run(self):
        try:
            self.validate(read_json(self.path))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, DecimalException) as exc:
            self.add("invalid-model", str(exc))
        return self

    def validate(self, m):
        canonical = "schema_version" in m
        if canonical and m["schema_version"] != SCHEMA:
            raise ValueError("unsupported schema_version")
        if m.get("currency") != "CNY" or m.get("raw_money_unit") != "yuan":
            raise ValueError("models must declare CNY and raw_money_unit=yuan")
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", m["ticker"]):
            raise ValueError("ticker must identify a six-digit A-share security")
        if not self.path.parent.name.startswith(m["ticker"][:6] + "-"):
            self.add("ticker-path", "company folder and ticker disagree")
        cutoff = day(m["as_of"] if canonical else m["version"][:10])
        if self.path.name != case_stem(cutoff.isoformat(), m.get("revision", 1)) + "-model.json":
            self.add("version-date", "filename and research as_of/revision disagree")
        self.report()
        sources = self.sources(m, cutoff)
        status = m.get("status", "legacy")
        if canonical and status not in {"draft", "reviewed"}:
            raise ValueError("status must be draft or reviewed")
        if canonical and status == "draft":
            self.add("draft", "draft is not a reviewed research result", "warning")
        if canonical and status == "draft" and not m.get("raw"):
            return
        market = m["market"]
        price = number(market.get("price", market.get("close_price")))
        shares = number(market["shares"])
        if price <= 0 or shares <= 0 or shares != shares.to_integral_value():
            raise ValueError("price and integral share count must be positive")
        price_date = day(market.get("date", market.get("close_date")))
        shares_date = day(market["shares_asof"])
        if price_date > cutoff or shares_date > cutoff:
            self.add("lookahead", "price/share date exceeds research as_of")
        if shares_date > price_date:
            self.add("market-date", "share snapshot is later than quote; reconcile corporate actions first")
        if (cutoff - price_date).days > 7:
            self.add("stale-price", "price is more than 7 calendar days before research as_of", "warning")
        if canonical:
            self.references(market.get("source_ids"), sources, "market")
        cap = price * shares
        for key in ("market_cap", "market_cap_cny"):
            if key in market:
                self.equal(key, market[key], cap)
        self.calculated["market_cap_cny"] = cap
        facts = {}
        for period, record in m["raw"].items():
            end = period_end(period)
            if end > cutoff:
                self.add("lookahead", f"{period}: reporting period ends after research as_of")
            ids = record.get("source_ids")
            if ids is None and not canonical:
                source = record.get("source")
                # One named compatibility alias in the retained 2026-11-15 Hans model.
                ids = (["q3_2026", "annual_2025"]
                       if source == "q3_2026_and_annual_2025_quarters" else [source])
            self.references(ids, sources, period, end)
            normalized = {ALIASES.get(k, k): v for k, v in record.items()}
            facts[period] = {k: number(normalized[k]) for k in METRICS}
        if not facts:
            raise ValueError("reviewed model requires raw financial periods")
        for year in sorted({p[:4] for p in facts}):
            for target, a, b in [("Q1", "H1", "Q2"), ("Q2", "H1", "Q1"),
                                 ("Q3", "M9", "H1"), ("Q4", "FY", "M9")]:
                if year+a in facts and year+b in facts:
                    expected = {k: facts[year+a][k]-facts[year+b][k] for k in METRICS}
                    if year+target in facts:
                        for k in METRICS:
                            self.equal(f"{year+target}.{k}", str(facts[year+target][k]), expected[k])
                    facts[year+target] = expected
        candidates = []
        for period in facts:
            year, suffix = int(period[:4]), period[4:]
            if suffix == "FY":
                candidates.append((period_end(period), facts[period]))
            elif suffix in {"M9", "H1", "Q1"}:
                previous, annual = f"{year-1}{suffix}", f"{year-1}FY"
                if previous in facts and annual in facts:
                    candidates.append((period_end(period), {
                        k: facts[annual][k]+facts[period][k]-facts[previous][k] for k in METRICS}))
        if not candidates:
            raise ValueError("need FY or comparable cumulative/prior-FY periods to calculate TTM")
        ending, ttm = max(candidates, key=lambda item: item[0])
        if ending < max(period_end(p) for p in facts):
            self.add("ttm-gap", "latest financial period lacks comparable inputs for TTM", "warning")
        self.calculated.update(ttm_end=ending.isoformat(), ttm=ttm)
        d = m.get("derived", {})
        for key, values in d.items():
            if key in facts or key.lower().startswith("ttm_ending_") or key == "ttm":
                expected = facts[key] if key in facts else ttm
                if key.lower().startswith("ttm_ending_") and key[-8:] != ending.strftime("%Y%m%d"):
                    self.add("ttm-date", f"stored TTM label {key} differs from calculated endpoint")
                for metric, actual in values.items():
                    normalized = ALIASES.get(metric, metric)
                    if normalized in METRICS:
                        self.equal(f"derived.{key}.{metric}", actual, expected[normalized])
        for key, denom in [("ttm_pe", ttm["net_parent"]),
                           ("ttm_deduct_pe", ttm["deduct_net_parent"]),
                           ("ttm_ps", ttm["revenue"])]:
            self.ratio(d, key, cap, denom)
            self.calculated[key] = cap / denom if denom > 0 else None
        bs = m.get("balance_sheet", {})
        for section in ("balance_sheet", "half_balance_sheet", "cnc", "pledge", "management_orders"):
            record = m.get(section, {})
            for key in ("date", "close_date"):
                if record.get(key) and day(record[key]) > cutoff:
                    self.add("lookahead", f"{section}.{key} exceeds research as_of")
            if "source" in record:
                self.references([record["source"]], sources, section,
                                day(record["date"]) if record.get("date") else None)
        if "equity_parent" in bs:
            self.ratio(d, "pb", cap, number(bs["equity_parent"]))
        for record in d.get("quarters", []):
            expected = facts[record["period"]]
            for metric, actual in record.items():
                normalized = ALIASES.get(metric, metric)
                if normalized in METRICS:
                    self.equal(f"quarters.{record['period']}.{metric}", actual, expected[normalized])
        self.scenarios(m, cap, price, shares, cutoff)
        if "sotp_reference_only" in m:
            s, cnc = m["sotp_reference_only"], m["cnc"]
            value = number(cnc["held_A_shares"])*number(cnc["close_A_price"])
            self.equal("held_A_shares_quote_reference_value", s["held_A_shares_quote_reference_value"], value)
            self.equal("parent_market_cap_minus_held_value", s["parent_market_cap_minus_held_value_not_remaining_business_fair_value"], cap-value)
            self.equal("haircut_held_value", s["parent_market_cap_minus_haircut_held_value"],
                       cap-value*(1-number(s["held_value_haircut_assumption"])))

    def scenarios(self, m, cap, price, shares, cutoff):
        scenarios = m.get("scenarios", [])
        if len(scenarios) < 3:
            self.add("scenario-coverage", "reviewed valuation requires at least three explicit scenarios")
        names, horizons = set(), set()
        for s in scenarios:
            if s["name"] in names:
                self.add("scenario-name", "scenario names must be unique")
            names.add(s["name"])
            horizon = day(s["horizon"])
            horizons.add(horizon)
            if horizon <= cutoff:
                self.add("scenario-date", "scenario endpoint must be after research as_of")
            margin = number(s.get("normalized_parent_net_margin", s.get("normalized_net_profit_parent_margin")))
            revenue = number(s["revenue_cny_yi"])
            pe = number(s["pe"])
            if revenue <= 0 or not 0 < margin <= 1 or pe <= 0:
                raise ValueError("positive-earnings PE scenarios require revenue>0, 0<margin<=1, PE>0")
            profit = revenue*YI*margin
            profit_key = ("normalized_parent_net_profit_cny_yi" if "normalized_parent_net_profit_cny_yi" in s
                          else "normalized_net_profit_parent_cny_yi")
            self.equal("scenario.profit", s[profit_key], profit/YI, Decimal("0.00000001"))
            target = profit/shares*pe
            self.equal("scenario.terminal_price", s["terminal_price_cny_not_discounted"], target)
            return_key = ("price_change_ex_dividend_fee_tax" if "price_change_ex_dividend_fee_tax" in s
                          else "price_change_excluding_dividend_fees_tax")
            self.equal("scenario.price_change", s[return_key], target/price-1, Decimal("0.00000001"))
            if "eps_cny" in s:
                self.equal("scenario.eps", s["eps_cny"], profit/shares, Decimal("0.00000001"))
        if len(horizons) > 1:
            self.add("scenario-date", "scenario endpoints must match for comparison")
        if not {"bear", "base", "bull"}.issubset(names):
            self.add("scenario-coverage", "include named bear, base and bull scenarios")
        for section in ("reverse", "reverse_valuation"):
            for key, actual in m.get(section, {}).items():
                match = re.fullmatch(r"profit_cny_yi_for_double_at_pe(\d+)", key)
                if match:
                    self.equal(key, actual, cap*2/Decimal(match[1])/YI, Decimal("0.00000001"))
                elif key == "doubling_price":
                    self.equal(key, actual, price*2)
                elif key.startswith("base_profit_price_at_pe"):
                    base = next((s for s in scenarios if s["name"] == "base"), None)
                    if base is None:
                        self.add("scenario-coverage", "base reverse valuation has no base scenario")
                        continue
                    margin = number(base.get("normalized_parent_net_margin", base.get("normalized_net_profit_parent_margin")))
                    pe = number(key.removeprefix("base_profit_price_at_pe"))
                    self.equal(key, actual, number(base["revenue_cny_yi"])*YI*margin/shares*pe)


def new_case(root, slug, ticker, as_of, name, revision=1):
    root = root.resolve()
    stem = case_stem(as_of, revision)
    if not re.fullmatch(r"\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("company slug must be six digits followed by lowercase hyphenated name")
    if not re.fullmatch(r"\d{6}\.(SH|SZ)", ticker) or ticker[:6] != slug[:6]:
        raise ValueError("ticker must match the company slug")
    folder = inside(root, root/COMPANIES/slug)
    report, model = folder/f"{stem}.md", folder/f"{stem}-model.json"
    if report.exists() or model.exists():
        raise ValueError("dated case already exists; use a new date/version, never overwrite history")
    payload = {"schema_version": SCHEMA, "as_of": as_of, "revision": revision, "status": "draft",
               "ticker": ticker, "currency": "CNY", "raw_money_unit": "yuan",
               "sources": {}, "market": {"date": as_of, "price": "", "shares_asof": as_of,
                                           "shares": "", "source_ids": []},
               "raw": {}, "derived": {}, "scenarios": [],
               "limitations": ["Draft: financial facts and source evidence not yet collected."]}
    prose = f"""# {name}（{ticker}）研究底稿

研究截止日：{as_of}，第{revision}版。状态：草稿；尚未形成投资结论。
数据与计算：[模型]({stem}-model.json)。

## 一页判断与可能亏损的路径

待核验：商业模式、证据支持、最大不确定性、候选状态与失败机制。

## 康波背景与朱格拉投资传导

待核验：终端需求、客户收益与资本支出、订单、交付验收、产能和回款。
分别记录长期结构、设备投资、库存与技术周期，以及替代解释。

## 财务与股东归属

待核验：累计与单季、低基数、并购、少数股东、股本稀释、利润与现金。
记录原始来源、期间、发布日期、采集时间及缺失数据，不用零填补未知。

## 三情景、现价隐含预期与催化

待核验：同一未来终点、正常化盈利、倍数及压缩情景；与今日价值分开。
独立假设与公司指引、市场一致预期分列，不重复计算子公司或现金价值。

## 验证、证伪与后续更新

待核验：关键事实、复核事件、失效条件与未知项。资金约束未知时不填写固定仓位。
发布前按研究工作流人工复核，并执行模型检查；脚本通过不等于投资结论正确。
"""
    folder.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, content in [(model, json.dumps(payload, ensure_ascii=False, indent=2)+"\n"), (report, prose)]:
            with path.open("x", encoding="utf-8") as handle:
                created.append(path)
                handle.write(content)
    except OSError:
        for path in created:
            path.unlink()
        raise
    return [str(p.relative_to(root)) for p in (report, model)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="isolated project root for a dry run")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="offline checks; never fetches or edits research")
    check.add_argument("paths", nargs="*", help="model files relative to project root")
    check.add_argument("--strict", action="store_true", help="fail on drafts/stale-price warnings")
    check.add_argument("--json", action="store_true")
    sub.add_parser("list", help="list dated research models")
    create = sub.add_parser("new", help="create paired draft files without overwriting")
    create.add_argument("slug")
    create.add_argument("--ticker", required=True)
    create.add_argument("--as-of", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--revision", type=int, default=1, help="same-day revision number; never overwrites")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "new":
            for path in new_case(root, args.slug, args.ticker, args.as_of, args.name, args.revision):
                print(path)
            return 0
        models = sorted((root/COMPANIES).glob("*/*-model.json"))
        if args.command == "list":
            for path in models:
                print(path.relative_to(root))
            return 0
        paths = [inside(root, root/p) for p in args.paths] if args.paths else [inside(root, p) for p in models]
        checks = [Check(root, path).run() for path in paths]
        findings = [asdict(f) for c in checks for f in c.findings]
        if not paths:
            findings.append(asdict(Finding("error", "no-models", str(COMPANIES), "no research models found")))
        errors = sum(f["severity"] == "error" for f in findings)
        warnings = sum(f["severity"] == "warning" for f in findings)
        result = {"models_checked": len(checks), "errors": errors, "warnings": warnings,
                  "passed": not errors and not (args.strict and warnings), "findings": findings,
                  "calculated": {str(c.path.relative_to(root)): c.calculated for c in checks}}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=json_default))
        else:
            for f in findings:
                print(f"[{f['severity'].upper()}] {f['code']} {f['path']}: {f['message']}")
            print(f"{'PASS' if result['passed'] else 'FAIL'}: {len(checks)} models, {errors} errors, {warnings} warnings")
        return 0 if result["passed"] else 1
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
