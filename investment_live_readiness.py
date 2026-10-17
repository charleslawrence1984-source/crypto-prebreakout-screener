"""Read-only, per-share publication guard for CL Signal Investment BUY labels.

This is a UI safety overlay, never an Investment strategy, backfill, price,
portfolio or trading change. Absence of verified published evidence fails closed.
A recent processing timestamp does not guarantee a fresh last price candle.
"""
from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

TECH_MAX_AGE_HOURS = 96  # accommodates a normal weekend; never silent indefinite reuse
PRICE_MAX_AGE_HOURS = 96
MAX_PRICE_SESSION_AGE_DAYS = 4  # Friday close remains acceptable through a normal Monday
FUNDAMENTAL_MAX_AGE_DAYS = 7
CLOCK_SKEW_MINUTES = 5
RESEARCH_ONLY_REASON = "RESEARCH ONLY — Investment publication is not verified"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _parse_aware(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        point = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if point.tzinfo is None or point.utcoffset() is None:
        return None
    return point.astimezone(timezone.utc)


def _recent(value, now, *, hours):
    point = _parse_aware(value)
    if point is None:
        return False
    age = now - point
    return -timedelta(minutes=CLOCK_SKEW_MINUTES) <= age <= timedelta(hours=hours)


def _finite_positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def daily_price_session_issue(value, now: datetime, *, max_age_days: int = MAX_PRICE_SESSION_AGE_DAYS) -> str:
    """Validate the actual Yahoo daily bar SESSION DATE, not the time we fetched it.

    Daily histories can have a date-only (timezone-naive) index. Never fabricate
    a UTC close timestamp from midnight. A missing date, future date, or an old
    session must suppress an Investment BUY, including across normal weekends.
    """
    if not isinstance(value, str) or not value.strip():
        return "per-share daily price candle date is missing"
    try:
        session = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return "per-share daily price candle date is invalid"
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    age = (now.date() - session).days
    if age < 0:
        return "per-share daily price candle date is in the future"
    if age > max_age_days:
        return "per-share daily price candle is stale"
    return ""

class InvestmentPublication:
    """Evidence indexes are by *exact listing*; no symbol/venue substitution."""

    def __init__(self, root: Path, *, now: datetime | None = None,
                 expected_valuation_model: str | None = None):
        self.root = Path(root)
        self.expected_valuation_model = expected_valuation_model
        self.now = now or datetime.now(timezone.utc)
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must be timezone aware")
        self.now = self.now.astimezone(timezone.utc)
        self.fundamental = _read_json(self.root / "prepared_scans" / "manifest.json")
        self.technical = _read_json(self.root / "prepared_investment_technicals" / "manifest.json")
        self.priority = _read_json(self.root / "prepared_priority_investments" / "manifest.json")
        self.summary = _read_json(self.root / "prepared_priority_investments" / "summary.json")
        self.tech_by_listing = {}
        self.priority_by_listing = {}
        self.venues_by_ticker = {}
        self._source_frames = {}  # lazy: only exchanges with candidate BUY checks

        for kind, meta in self.technical.get("exchanges", {}).items():
            if not isinstance(meta, dict):
                continue
            path = self.root / "prepared_investment_technicals" / (kind + ".csv.gz")
            if not path.exists():
                continue
            try:
                frame = pd.read_csv(path, compression="gzip")
            except (OSError, ValueError, EOFError):
                continue
            if "Ticker" not in frame or frame["Ticker"].duplicated().any():
                continue
            for _, row in frame.iterrows():
                sym = str(row.get("Ticker") or "").strip()
                if not sym or sym.lower() == "nan":
                    continue
                self.tech_by_listing[(kind, sym)] = row.to_dict()
                self.venues_by_ticker.setdefault(sym, set()).add(kind)

        for item in self.summary.get("rows", []):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("Prepared exchange") or "")
            sym = str(item.get("Ticker") or "")
            if kind and sym and (kind, sym) not in self.priority_by_listing:
                self.priority_by_listing[(kind, sym)] = item
                self.venues_by_ticker.setdefault(sym, set()).add(kind)

    def _current_source_fingerprint(self, kind: str, symbol: str) -> str:
        """Recompute the worker's exact eligible-row fingerprint, read-only."""
        if kind not in self._source_frames:
            path = self.root / "prepared_scans" / (kind + ".csv.gz")
            try:
                self._source_frames[kind] = pd.read_csv(path, compression="gzip")
            except (OSError, ValueError, EOFError):
                self._source_frames[kind] = pd.DataFrame()
        frame = self._source_frames[kind]
        required = {"Ticker", "Action", "Hard gates", "Price"}
        if not required.issubset(frame.columns):
            return ""
        matches = frame.loc[frame["Ticker"].astype(str).eq(symbol)]
        if matches.empty:
            return ""
        row = matches.iloc[-1]
        if (row["Hard gates"] != "PASS" or row["Action"] not in {"BUY CANDIDATE", "WAIT"}
                or not _finite_positive(row["Price"])):
            return ""
        return hashlib.sha256(row.to_json().encode()).hexdigest()

    def issue(self, symbol: str, *, kind: str | None = None) -> str:
        """Empty string means the current publication prerequisites all pass."""
        symbol = str(symbol or "").strip()
        if not symbol:
            return "share ticker is missing"
        if kind is None:
            kinds = self.venues_by_ticker.get(symbol, set())
            if len(kinds) != 1:
                return "listing or exchange cannot be verified uniquely"
            kind = next(iter(kinds))

        fund = self.fundamental.get("exchanges", {}).get(kind)
        tech = self.technical.get("exchanges", {}).get(kind)
        price = self.priority.get("exchanges", {}).get(kind)
        if not all(isinstance(x, dict) for x in (fund, tech, price)):
            return "one or more Investment source manifests are missing"
        if fund.get("status") != "complete" or fund.get("remaining_symbols") != 0:
            return "fundamental source is incomplete"
        if not _recent(fund.get("completed_at"), self.now, hours=24 * FUNDAMENTAL_MAX_AGE_DAYS):
            return "fundamental source is stale or undated"
        if tech.get("status") not in {"complete", "partial"}:
            return "technical exchange is not ready"
        if not _recent(self.technical.get("updated_at"), self.now, hours=TECH_MAX_AGE_HOURS):
            return "technical publication is stale or undated"
        if tech.get("source_total_eligible_symbols") != fund.get("eligible_symbols"):
            return "technical and fundamental source versions disagree"
        if tech.get("source_provider_deferred_symbols") != fund.get("provider_deferred_symbols", 0):
            return "source-deferred counts disagree"

        record = self.tech_by_listing.get((kind, symbol))
        if not record:
            return "verified technical row is missing for this listing"
        if record.get("Technical build") != self.technical.get("technical_build"):
            return "technical build does not match the published manifest"
        if record.get("Fundamental source updated at") != fund.get("completed_at"):
            return "technical input has not been reconciled with fundamentals"
        fingerprint = record.get("Fundamental fingerprint")
        if pd.isna(fingerprint) or str(fingerprint).strip().lower() in {"", "nan", "none"}:
            return "technical fundamental fingerprint is missing"
        source_fingerprint = self._current_source_fingerprint(kind, symbol)
        if not source_fingerprint or str(fingerprint) != source_fingerprint:
            return "technical fingerprint no longer matches the current verified fundamentals"
        if not _recent(record.get("Technical calculated at"), self.now, hours=TECH_MAX_AGE_HOURS):
            return "technical calculation is stale or undated"
        if not _finite_positive(record.get("trade_score")):
            return "technical calculation has no valid score"
        if not _finite_positive(record.get("rsi")):
            return "technical RSI is missing or invalid"

        if price.get("price_refresh_status") != "complete":
            return "priority quote refresh is incomplete"
        if not isinstance(price.get("price_refresh_updated"), int) or price.get("price_refresh_updated") < 1:
            return "priority quote refresh did not confirm updated prices"
        if price.get("price_refresh_missing") != 0 or price.get("price_refresh_rate_limit_errors", 0) != 0:
            return "priority quote refresh has unresolved failures"
        if not _recent(price.get("price_refreshed_at"), self.now, hours=PRICE_MAX_AGE_HOURS):
            return "priority quote refresh is stale or undated"
        if self.summary.get("refreshed_at") != self.priority.get("price_refreshed_at"):
            return "priority summary does not match the current price publication"
        priority = self.priority_by_listing.get((kind, symbol))
        if not priority or priority.get("Action") != "BUY CANDIDATE":
            return "this listing has no matching validated priority BUY"
        if (self.expected_valuation_model is not None
                and priority.get("Valuation model version") != self.expected_valuation_model):
            return "priority valuation model version is not current"
        if priority.get("Valuation FX status") != "PASS" or priority.get("Valuation gate") != "PASS":
            return "FX-safe valuation is not verified"
        if not _finite_positive(priority.get("Price")):
            return "validated priority quote is missing"
        # A recent workflow timestamp is NOT evidence that Yahoo returned a
        # recent candle for this specific listing. Verify the saved session.
        candle_issue = daily_price_session_issue(priority.get("Price candle date"), self.now)
        if candle_issue:
            return candle_issue
        if not _finite_positive(priority.get("Base margin of safety %")):
            return "positive margin-of-safety evidence is missing"
        try:
            mos = float(priority["Base margin of safety %"])
            required = float(priority["Required margin of safety %"])
            bear = float(priority["Bear margin of safety %"])
            quality = float(priority["Quality score"])
        except (ValueError, TypeError, KeyError):
            return "complete valuation and quality evidence is missing"
        if not all(math.isfinite(x) for x in (mos, required, bear, quality)):
            return "non-finite valuation or quality evidence"
        if mos < required or bear < 0 or quality < 70:
            return "valuation or quality prerequisites no longer pass"
        return ""


def withhold_unverified_buys(frame: pd.DataFrame, evidence: InvestmentPublication,
                             exchange_by_label: dict) -> pd.DataFrame:
    """Home and Opportunities use one guarded loader and identical BUY counts."""
    result = frame.copy()
    if result.empty or "Action" not in result.columns:
        return result
    result["Investment readiness"] = "RESEARCH ONLY"
    for index, row in result[result["Action"].astype(str).eq("BUY CANDIDATE")].iterrows():
        kind = exchange_by_label.get(str(row.get("Exchange") or ""))
        reason = evidence.issue(row.get("Ticker"), kind=kind) if kind else "exchange cannot be verified"
        if reason:
            result.at[index, "Action"] = "REVALUE"
            result.at[index, "Valuation gate"] = "REVALUE"
            result.at[index, "Decision reason"] = RESEARCH_ONLY_REASON + ": " + reason
            result.at[index, "Investment readiness"] = "AWAITING VERIFIED DATA"
        else:
            result.at[index, "Investment readiness"] = "VERIFIED PUBLICATION — MANUAL REVIEW REQUIRED"
    return result


def guard_quick_investment_decision(summary: dict, symbol: str,
                                    evidence: InvestmentPublication) -> dict:
    """Quick Analysis and watchlist may not bypass the prepared BUY gate."""
    output = dict(summary)
    if str(output.get("action") or "").upper() != "BUY CANDIDATE":
        return output
    reason = evidence.issue(symbol)
    if reason:
        output["action"] = "REVALUE"
        output["reason"] = RESEARCH_ONLY_REASON + ": " + reason
    else:
        output["reason"] = str(output.get("reason") or "") + " Published technical and priority price evidence verified."
    return output



def guard_portfolio_investment_adds(frame: pd.DataFrame,
                                    evidence: InvestmentPublication) -> pd.DataFrame:
    """Withhold owned-position ADD labels when independent publication is unverified.

    Portfolio HOLD, REASSESS and REVIEW FOR SALE are not overwritten: a
    missing BUY verification must never suppress an existing sell warning.
    P&L, position sizes and the saved browser portfolio remain unchanged.
    """
    output = frame.copy()
    if output.empty or "Action" not in output.columns:
        return output
    output["Investment readiness"] = "RESEARCH ONLY"
    adds = output["Action"].astype(str).isin({"BUY MORE", "ADD CANDIDATE"})
    for index, row in output[adds].iterrows():
        resolved = str(row.get("Resolved ticker") or row.get("Ticker") or "").strip()
        reason = evidence.issue(resolved)
        if reason:
            output.at[index, "Action"] = "REASSESS"
            previous = str(row.get("Review reason") or "").strip()
            note = RESEARCH_ONLY_REASON + ": " + reason
            output.at[index, "Review reason"] = "; ".join(x for x in (previous, note) if x)
            output.at[index, "Investment readiness"] = "AWAITING VERIFIED DATA"
        else:
            output.at[index, "Investment readiness"] = "VERIFIED PUBLICATION — MANUAL REVIEW REQUIRED"
    return output
