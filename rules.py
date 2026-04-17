
from helpers import (
    company_type,
    get_assets,
    get_current_financial_liabilities,
    get_current_ratio,
    get_debt_to_assets_ratio,
    get_dio,
    get_dpo,
    get_dso,
    get_cash_conversion_cycle,
    get_ebitda_margin,
    get_financial_stability_ratio,
    get_fixed_assets,
    get_gross_profit_margin,
    get_loss_in_excess_of_equity,
    get_n_emp,
    get_net_working_capital,
    get_non_current_liabilities,
    get_off_balance_assets_liabilities,
    get_operating_income_expenses,
    get_operating_profit_loss,
    get_operating_revenue,
    get_quick_ratio,
    get_receivables_to_payables_ratio,
    get_capital,
    get_total_financial_liabilities,
    get_net_profit
)
from datetime import datetime, date
from pathlib import Path


ABSOLUTE_LIMIT_CAP_RSD = 200_000_000
MIN_LIMIT_RSD = 600_000
EUR_RSD = 117.0
ENABLE_PERCENT_BUCKETING = True
PERCENT_BUCKETS = (1.0, 1.5, 2.0, 2.5, 3.0)

# Risk tim potvrdio (beleške 07.04.2026): micro tipično 1.5%, 2% ili 2.5%, ne 3%.
# Prethodni micro low=1.0% sprečavao je sistem da dostigne više od ~1.5%
# čak ni za dobre micro klijente, što nije u skladu sa risk pravilima.
SIZE_PCT_MATRIX = {
    "micro":  {"low": 2.50, "medium": 2.00, "high": 1.50},
    "small":  {"low": 1.50, "medium": 1.25, "high": 1.00},
    "medium": {"low": 2.00, "medium": 1.60, "high": 1.00},
    "large":  {"low": 2.50, "medium": 2.25, "high": 1.25},
}

def _safe_ratio(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator

def _years_window(end_year: str, n: int):
    try:
        y = int(end_year)
    except Exception:
        return []
    n = max(1, min(int(n), 5))  # clamp 1..5
    return [str(yy) for yy in range(y - n + 1, y + 1)]


def _extract_series(client_json, years, getter, pick_key=None):
    """
    getter: funkcija tipa get_operating_income_expenses(json, year)
    pick_key: None -> uzmi scalar; ili "income"/"expenses"/"profit"/"loss" ako getter vraća dict
    Vraća listu (year, value) samo za godine gde value nije None.
    """
    out = []
    for y in years:
        v = getter(client_json, y)
        if isinstance(v, dict) and pick_key is not None:
            v = v.get(pick_key)
        if v is None:
            continue
        out.append((y, v))
    return out

def _extract_signed_operating_result_series(client_json, years):
    profit_series = _sort_series(
        _extract_series(client_json, years, get_operating_profit_loss, pick_key="profit")
    )
    loss_series = _sort_series(
        _extract_series(client_json, years, get_operating_profit_loss, pick_key="loss")
    )

    by_year = {}
    for y, p in profit_series:
        by_year[y] = p if p is not None else 0
    for y, l in loss_series:
        if l is not None and l > 0:
            by_year[y] = -abs(l)

    return _sort_series([(y, v) for y, v in by_year.items()])


def _series_values_only(series):
    return [v for _, v in series if v is not None]


def _last_n_negative(series, n=2):
    """
    series: list[(year, value)] sortirana rastuće po godini
    True ako je poslednjih n vrednosti < 0
    """
    vals = _series_values_only(series)
    if len(vals) < n:
        return False
    return all(v < 0 for v in vals[-n:])

def _sort_series(series):
    try:
        return sorted(series, key=lambda x: int(x[0]))
    except Exception:
        return series

def _result(rule_id, label, value, status, goal, note):
    return {
        "id": rule_id,
        "label": label,
        "value": value,
        "status": status,
        "goal": goal,
        "note": note,
    }

def _check_ccc(value):
    if value is None:
        return "na"
    if value <= 0:
        return "pass"
    if value <= 90:
        return "pass"
    if value <= 180:
        return "warn"
    return "fail"

def _check_min(value, minimum):
    if value is None:
        return "na"
    return "pass" if value >= minimum else "fail"

def _check_strict_min(value, minimum):
    if value is None:
        return "na"
    return "pass" if value > minimum else "fail"


def _check_max(value, maximum):
    if value is None:
        return "na"
    return "pass" if value <= maximum else "fail"


def _check_range(value, lower, upper):
    if value is None:
        return "na"
    if lower <= value <= upper:
        return "pass"
    if lower * 0.8 <= value <= upper * 1.2:
        return "warn"
    return "fail"


def _check_target(value, target, tolerance=0.15):
    if value is None:
        return "na"
    delta = abs(value - target)
    if delta <= tolerance:
        return "pass"
    if delta <= tolerance * 2:
        return "warn"
    return "fail"

def _check_receivables_payables(value):
    if value is None:
        return "na"
    if value >= 1.0:
        return "pass"
    if value >= 0.8:
        return "warn"
    return "fail"

def _check_debt_ratio(value):
    # Koeficijent zaduženosti je informativan pokazatelj, nije presudan.
    # Risk tim: "pogledati, ali nije među presudnim pokazateljima" – nikad fail.
    if value is None:
        return "na"
    if value <= 0.70:
        return "pass"
    return "warn"

def _series_values(series):
    return [v for _, v in _sort_series(series) if v is not None]


def _safe_yoy_change(prev, curr):
    if prev is None or curr is None:
        return None
    if prev == 0:
        return None
    return (curr - prev) / abs(prev)

def get_founding_year(client_json):
    raw = client_json.get("osnivanje_firme", {}).get("datum_osnivanja")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").year
    except Exception:
        try:
            return int(str(raw).split("-")[0])
        except Exception:
            return None

def propose_base_pct(company_size: str, overall_risk: str) -> float:
    matrix = SIZE_PCT_MATRIX.get(company_size, SIZE_PCT_MATRIX["small"])
    if overall_risk == "critical": 
        return 0.0
    if overall_risk == "high":
        return matrix["high"]
    if overall_risk == "medium": 
        return matrix["medium"] 
    
    return matrix["low"]

def has_single_year_revenue_profile(client_json, year, trend_years=3, min_material_revenue_ratio=0.1):
    years = _years_window(str(year), trend_years)
    revenue_series = _sort_series(_extract_series(client_json, years, get_operating_revenue))
    vals = [v for _, v in revenue_series if v is not None]

    if len(vals) < 2:
        return False

    latest = vals[-1]
    if latest is None or latest <= 0:
        return False

    materially_active_years = sum(1 for v in vals if v >= latest * min_material_revenue_ratio)

    return materially_active_years <= 1


def detect_revenue_anomalies(
    client_json,
    year,
    trend_years=3,
    jump_threshold=0.30,
    drop_threshold=0.30,
    volatility_threshold=0.30,
):
    """
    Detektuje anomalije u kretanju poslovnih prihoda:
    - single_year_only:           materijalni prihodi samo u poslednjoj godini
    - sudden_jump:                YoY rast >= jump_threshold u poslednjoj godini
    - severe_drop:                YoY pad >= drop_threshold u poslednjoj godini
    - high_volatility_up_then_down: nagli rast u Y1→Y2, pa nagli pad u Y2→Y3
    - high_volatility_down_then_up: nagli pad u Y1→Y2, pa nagli rast u Y2→Y3
    """
    year = str(year)
    years = _years_window(year, trend_years)
    revenue_series = _sort_series(_extract_series(client_json, years, get_operating_revenue))
    vals = [v for _, v in revenue_series if v is not None]

    anomalies = []
    details = {}

    if not vals:
        return {"anomalies": ["no_revenue_data"], "details": {}}

    # a) Prihodi samo u poslednjoj godini
    if has_single_year_revenue_profile(client_json, year, trend_years):
        anomalies.append("single_year_only")

    # b) Nagli skok ili pad u poslednjoj godini
    latest_yoy = None
    if len(vals) >= 2:
        latest_yoy = _safe_yoy_change(vals[-2], vals[-1])
        if latest_yoy is not None:
            details["latest_yoy_change"] = round(latest_yoy, 4)
            if latest_yoy >= jump_threshold:
                anomalies.append("sudden_jump")
            elif latest_yoy <= -drop_threshold:
                anomalies.append("severe_drop")

    # c) Visoka volatilnost kroz 3 godine (gore-dole ili dole-gore)
    if len(vals) >= 3:
        c1 = _safe_yoy_change(vals[-3], vals[-2])
        c2 = _safe_yoy_change(vals[-2], vals[-1])
        if c1 is not None and c2 is not None:
            details["3y_yoy_changes"] = [round(c1, 4), round(c2, 4)]
            if c1 >= volatility_threshold and c2 <= -volatility_threshold:
                anomalies.append("high_volatility_up_then_down")
            elif c1 <= -volatility_threshold and c2 >= volatility_threshold:
                anomalies.append("high_volatility_down_then_up")

    return {"anomalies": anomalies, "details": details}


def has_financial_indicators(client_json, year):
    """
    Mlada firma može imati makar delimične finansijske pokazatelje čak i kada
    nema punu istoriju. Dovoljno je da postoji makar jedan smislen pokazatelj
    za poslednju posmatranu godinu da se slučaj ne tretira kao potpuno bez
    finansijskih podataka.
    """
    year = str(year)

    scalar_checks = (
        get_operating_revenue(client_json, year),
        get_assets(client_json, year),
        get_ebitda_margin(client_json, year),
        get_current_ratio(client_json, year),
        get_quick_ratio(client_json, year),
        get_net_working_capital(client_json, year),
    )
    if any(value is not None for value in scalar_checks):
        return True

    operating_profit_loss = get_operating_profit_loss(client_json, year)
    if isinstance(operating_profit_loss, dict) and any(
        value is not None for value in operating_profit_loss.values()
    ):
        return True

    return False


def _count_negative_periods(series):
    return sum(1 for _, value in _sort_series(series or []) if value is not None and value < 0)


def _build_reduction_summary(base_pct, adjusted_pct, penalties_applied):
    if adjusted_pct is None or base_pct is None:
        return "Nije moguće izračunati korekciju limita."
    if not penalties_applied:
        return f"Limit nije dodatno umanjen; bazni procenat {base_pct:.2f}% je zadržan."
    reasons = "; ".join(item["reason"] for item in penalties_applied)
    return (
        f"Bazni procenat {base_pct:.2f}% je korigovan na {adjusted_pct:.2f}% "
        f"zbog sledećih negativnih signala: {reasons}"
    )


def snap_pct_to_policy_bucket(pct):
    if pct is None:
        return None
    if not ENABLE_PERCENT_BUCKETING:
        return round(pct, 4)
    if pct <= 0:
        return 0.0
    for bucket in PERCENT_BUCKETS:
        if pct <= bucket:
            return float(bucket)
    return float(PERCENT_BUCKETS[-1])


def qualifies_for_growth_uplift(limit_decision: dict, positive_info: dict) -> bool:
    report = limit_decision.get("rules_report", {})
    results = {r["id"]: r for r in report.get("results", [])}
    red_flags = set(report.get("red_flags", []))
    warning_flags = set(report.get("warning_flags", []))
    positive_names = {item.get("name") for item in positive_info.get("positive_factors_applied", [])}

    core_red_flags = {
        "operating_profit_loss",
        "operating_result_trend",
        "operating_revenue_trend",
        "loss_in_excess_of_equity",
        "operating_result_to_revenue",
    }
    disqualifying_red_flags = {
        "net_working_capital",
        "receivables_payables_position",
        "fixed_assets_to_revenue",
    }

    return (
        limit_decision.get("overall_risk") in {"low", "medium"}
        and not (red_flags & core_red_flags)
        and not (red_flags & disqualifying_red_flags)
        and results.get("operating_result_to_revenue", {}).get("status") == "pass"
        and (
            "revenue_growth_strong" in positive_names
            or "operating_result_improvement_strong" in positive_names
        )
        and len(warning_flags) <= 7
    )


def _determine_security_requirement(limit_decision, final_limit):
    risk = limit_decision.get("overall_risk")
    red_flags = set(limit_decision.get("rules_report", {}).get("red_flags", []))
    if final_limit <= 0:
        return "advance_payment_only", "Limit nije odobren; preporuka je saradnja samo uz avans."
    if risk == "high":
        return "insurance_required", "Visok rizik zahteva dodatno osiguranje potraživanja."
    if (
        "fixed_assets_to_revenue" in red_flags
        or "receivables_payables_position" in red_flags
        or "loss_in_excess_of_equity" in red_flags
    ):
        return "guarantees_required", "Slabija kolateralna osnova zahteva dodatna sredstva obezbeđenja."
    if final_limit >= 15_000_000:
        return "insurance_required", "Veći iznos limita zahteva osiguranje kao dodatno sredstvo obezbeđenja."
    return "standard_security", "Standardna sredstva obezbeđenja su dovoljna."


def should_route_to_manual_review(limit_decision: dict) -> bool:
    rules_report = limit_decision.get("rules_report", {})
    red_flags = set(rules_report.get("red_flags", []))
    warning_flags = set(rules_report.get("warning_flags", []))
    high_risk_reasons = limit_decision.get("high_risk_reasons", [])
    strong_operating_profile = has_strong_operating_profile(rules_report)
    core_red_flags = {
        "operating_profit_loss",
        "operating_result_trend",
        "operating_revenue_trend",
        "loss_in_excess_of_equity",
        "operating_result_to_revenue",
    }
    return (
        limit_decision.get("overall_risk") in {"medium", "high"}
        and len(warning_flags) >= 5
        and len(red_flags) >= 2
        and (
            "fixed_assets_to_revenue" in red_flags
            or len(high_risk_reasons) >= 1
            or len(red_flags & core_red_flags) >= 1
        )
        and not strong_operating_profile
    )


def _is_near_breakeven_from_report(report, threshold=-0.005):
    """
    True ako je poslovni gubitak manji od 0,5% prihoda (efektivno prelomna tačka).
    Beleške sa sastanka: 'Poslovni dobitak jeste bitan, ali nije presudan.'
    Minimalni gubitak na prelomnoj tački ne treba tretirati kao pravi poslovni gubitak.
    """
    results = {r["id"]: r for r in report.get("results", [])}
    val = results.get("operating_result_to_revenue", {}).get("value")
    return val is not None and threshold <= val < 0


def _is_structural_cash_retail(report):
    """
    True ako potraživanja od kupaca čine manje od 5% obaveza iz poslovanja.
    Karakteristika gotovinskih maloprodavaca: kupci plaćaju odmah, nema B2B kredita.
    Nizak odnos potraživanja/obaveza u ovom slučaju nije kreditni rizik.
    """
    results = {r["id"]: r for r in report.get("results", [])}
    val = results.get("receivables_to_payables_ratio", {}).get("value")
    return val is not None and val < 0.05


def is_weak_micro_client(limit_decision: dict) -> bool:
    if limit_decision.get("company_type") != "micro":
        return False
    rules_report = limit_decision.get("rules_report", {})
    red_flags = set(rules_report.get("red_flags", []))
    warning_flags = set(rules_report.get("warning_flags", []))

    near_breakeven = _is_near_breakeven_from_report(rules_report)
    structural_cash_retail = _is_structural_cash_retail(rules_report)

    weakness_hits = 0
    # Gubitak < 0.5% prihoda (prelomna tačka) ne računa se kao slabost
    if not near_breakeven and (
        "operating_profit_loss" in red_flags or "operating_result_to_revenue" in red_flags
    ):
        weakness_hits += 1
    if "fixed_assets_to_revenue" in red_flags or "fixed_assets_to_revenue" in warning_flags:
        weakness_hits += 1
    # Gotovinska maloprodaja: nulta potraživanja su strukturalna, ne kreditni rizik
    if not structural_cash_retail and (
        "receivables_payables_position" in red_flags or "receivables_payables_position" in warning_flags
    ):
        weakness_hits += 1
    if "operating_revenue_trend" in red_flags or "operating_revenue_trend" in warning_flags:
        weakness_hits += 1
    return weakness_hits >= 3 or limit_decision.get("risk_score", 0) >= 70


def has_strong_operating_profile(report: dict) -> bool:
    results = {r["id"]: r for r in report.get("results", [])}
    red_flags = set(report.get("red_flags", []))

    return (
        "operating_profit_loss" not in red_flags
        and "operating_result_to_revenue" not in red_flags
        and "loss_in_excess_of_equity" not in red_flags
        and "operating_result_trend" not in red_flags
        and results.get("operating_result_to_revenue", {}).get("status") == "pass"
        and results.get("operating_revenue_trend", {}).get("status") == "pass"
    )

# naprednija trend analiza
def assess_trend(
    series,
    direction="higher_is_better",
    severe_drop_pct=0.30,
    mild_drop_pct=0.15,
):
    """
    Vraća strukturirani trend signal.
    direction:
      - higher_is_better
      - lower_is_better
    """
    series = _sort_series(series or [])
    vals = _series_values(series)

    if len(vals) < 2:
        return {
            "status": "na",
            "signals": [],
            "yoy_change": None,
            "latest": vals[-1] if vals else None,
        }

    latest = vals[-1]
    prev = vals[-2]
    yoy = _safe_yoy_change(prev, latest)

    signals = []

    # YoY signal
    if yoy is not None:
        if direction == "higher_is_better":
            if yoy <= -severe_drop_pct:
                signals.append("yoy_drop_severe")
            elif yoy < 0:
                signals.append("yoy_drop")
            elif yoy >= severe_drop_pct:
                signals.append("yoy_growth_strong")
            elif yoy > 0:
                signals.append("yoy_growth")
        elif direction == "lower_is_better":
            if yoy >= severe_drop_pct:
                signals.append("yoy_worsening_severe")
            elif yoy > 0:
                signals.append("yoy_worsening")
            elif yoy <= -severe_drop_pct:
                signals.append("yoy_improvement_strong")
            elif yoy < 0:
                signals.append("yoy_improvement")

    # 3Y direction
    if len(vals) >= 3:
        first = vals[0]
        if direction == "higher_is_better":
            if latest < first:
                signals.append("worse_than_3y_ago")
            elif latest > first:
                signals.append("better_than_3y_ago")
        elif direction == "lower_is_better":
            if latest > first:
                signals.append("worse_than_3y_ago")
            elif latest < first:
                signals.append("better_than_3y_ago")

    # Status
    if "yoy_drop_severe" in signals or "yoy_worsening_severe" in signals:
        status = "fail"
    elif (
        "yoy_drop" in signals
        or "yoy_worsening" in signals
        or "worse_than_3y_ago" in signals
    ):
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "signals": signals,
        "yoy_change": yoy,
        "latest": latest,
    }

# poslovni rezultat trend
def assess_operating_result_trend(series):
    """
    series = signed operating result series
    profit > 0
    loss < 0
    """
    series = _sort_series(series or [])
    vals = _series_values(series)

    if len(vals) < 2:
        return {
            "status": "na",
            "signals": [],
            "yoy_change": None,
            "latest": vals[-1] if vals else None,
        }

    latest = vals[-1]
    prev = vals[-2]
    yoy = _safe_yoy_change(prev, latest)

    signals = []

    # Poslednje 2 godine negativno = vrlo jak signal
    if len(vals) >= 2 and vals[-1] < 0 and vals[-2] < 0:
        signals.append("negative_two_years")
    if _count_negative_periods(series) >= 3:
        signals.append("negative_three_years")

    # Poslednja godina negativna
    if latest < 0:
        signals.append("latest_negative")

    # YoY promene
    if yoy is not None:
        if yoy <= -0.30:
            signals.append("yoy_drop_severe")
        elif yoy < 0:
            signals.append("yoy_drop")
        elif yoy >= 0.30:
            signals.append("yoy_growth_strong")
        elif yoy > 0:
            signals.append("yoy_growth")

    # 3Y poređenje
    if len(vals) >= 3:
        first = vals[0]
        if latest < first:
            signals.append("worse_than_3y_ago")
        elif latest > first:
            signals.append("better_than_3y_ago")

    if "negative_three_years" in signals:
        status = "fail"
    elif "negative_two_years" in signals:
        status = "fail"
    elif "latest_negative" in signals or "yoy_drop_severe" in signals:
        status = "warn"
    elif "yoy_drop" in signals or "worse_than_3y_ago" in signals:
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "signals": signals,
        "yoy_change": yoy,
        "latest": latest,
    }

def evaluate_position_signals(client_json, year):
    year = str(year)

    revenue = get_operating_revenue(client_json, year)
    assets = get_assets(client_json, year)
    fixed_assets = get_fixed_assets(client_json, year)
    capital = get_capital(client_json, year)
    receivables_to_payables = get_receivables_to_payables_ratio(client_json, year)
    current_fin_liab = get_current_financial_liabilities(client_json, year)

    operating_profit_loss = get_operating_profit_loss(client_json, year)
    op_profit = None
    op_loss = None
    if isinstance(operating_profit_loss, dict):
        op_profit = operating_profit_loss.get("profit")
        op_loss = operating_profit_loss.get("loss")

    signed_operating_result = None
    if op_loss is not None and op_loss > 0:
        signed_operating_result = -abs(op_loss)
    elif op_profit is not None:
        signed_operating_result = op_profit

    fixed_assets_to_revenue = _safe_ratio(fixed_assets, revenue)
    capital_to_assets = _safe_ratio(capital, assets)
    current_fin_liab_to_revenue = _safe_ratio(current_fin_liab, revenue)
    operating_result_to_revenue = _safe_ratio(signed_operating_result, revenue)

    results = []

    # 1) Stalna imovina u odnosu na prihod
    if fixed_assets_to_revenue is None:
        fa_status = "na"
    elif fixed_assets_to_revenue < 0.02:
        fa_status = "fail"
    elif fixed_assets_to_revenue < 0.08:
        fa_status = "warn"
    else:
        fa_status = "pass"

    results.append(_result(
        "fixed_assets_to_revenue",
        "Fixed Assets / Operating Revenue",
        fixed_assets_to_revenue,
        fa_status,
        ">= 8%",
        "Niska stalna imovina u odnosu na poslovne prihode može ukazivati na slabiju kolateralnu osnovu."
    ))

    # 2) Kapital u odnosu na aktivu
    if capital_to_assets is None:
        cap_status = "na"
    elif capital_to_assets < 0.10:
        cap_status = "warn"
    else:
        cap_status = "pass"

    results.append(_result(
        "capital_to_assets",
        "Capital / Total Assets",
        capital_to_assets,
        cap_status,
        ">= 10%",
        "Veći udeo kapitala u aktivi ukazuje na stabilniju finansijsku strukturu."
    ))

    # 3) Kratkoročne finansijske obaveze u odnosu na prihod
    if current_fin_liab_to_revenue is None:
        cfl_status = "na"
    elif current_fin_liab_to_revenue > 0.30:
        cfl_status = "warn"
    else:
        cfl_status = "pass"

    results.append(_result(
        "current_fin_liabilities_to_revenue",
        "Current Financial Liabilities / Operating Revenue",
        current_fin_liab_to_revenue,
        cfl_status,
        "<= 30%",
        "Visoke kratkoročne finansijske obaveze u odnosu na prihod mogu ukazivati na pritisak na likvidnost."
    ))

    # 4) Poslovni rezultat u odnosu na prihod
    if operating_result_to_revenue is None:
        opr_status = "na"
    elif operating_result_to_revenue < 0:
        opr_status = "fail"
    elif operating_result_to_revenue < 0.02:
        opr_status = "warn"
    else:
        opr_status = "pass"

    results.append(_result(
        "operating_result_to_revenue",
        "Operating Result / Operating Revenue",
        operating_result_to_revenue,
        opr_status,
        "> 0, poželjno >= 2%",
        "Veoma nizak ili negativan poslovni rezultat u odnosu na prihod ukazuje na slabu operativnu profitabilnost."
    ))

    # 5) Potraživanja vs obaveze ostaje postojeći odnos
    if receivables_to_payables is None:
        rtp_status = "na"
    elif receivables_to_payables < 0.5:
        rtp_status = "fail"
    elif receivables_to_payables < 1.0:
        rtp_status = "warn"
    else:
        rtp_status = "pass"

    results.append(_result(
        "receivables_payables_position",
        "Receivables vs Payables Position",
        receivables_to_payables,
        rtp_status,
        ">= 1.0 preferred",
        "Poželjno je da potraživanja pokrivaju obaveze ili da budu veća od njih."
    ))

    return results

def evaluate_financial_rules(client_json, year, trend_years=3):
    year = str(year)
    years = _years_window(year, trend_years)

    company_size = company_type(
        get_n_emp(client_json, year),
        get_operating_revenue(client_json, year) / EUR_RSD,
        get_assets(client_json, year) / EUR_RSD,
    )
    print(f"Velicina firme: {company_size}")

    # pokazatelji profitabilnosti
    ebitda_margin = get_ebitda_margin(client_json, year)
    gross_margin = get_gross_profit_margin(client_json, year)
    operating_profit_loss = get_operating_profit_loss(client_json, year)

    # likvidnost i obrtna sredstva
    current_ratio = get_current_ratio(client_json, year)
    quick_ratio = get_quick_ratio(client_json, year)
    net_working_capital = get_net_working_capital(client_json, year)
    receivables_to_payables = get_receivables_to_payables_ratio(client_json, year)

    # efikasost upravljanja (ciklus gotovine)
    dso = get_dso(client_json, year)
    dio = get_dio(client_json, year)
    dpo = get_dpo(client_json, year)
    ccc = get_cash_conversion_cycle(client_json, year)

    # zaduzenost i struktura kapitala
    debt_to_assets_ratio = get_debt_to_assets_ratio(client_json, year)
    financial_stability_ratio = get_financial_stability_ratio(client_json, year)
    total_financial_liabilities = get_total_financial_liabilities(client_json, year)
    non_current_liabilities = get_non_current_liabilities(client_json, year)
    current_financial_liabilities = get_current_financial_liabilities(client_json, year)

    # pozicije iz bilansa stanja i uspeha
    non_current_assets = get_fixed_assets(client_json, year)
    capital = get_capital(client_json, year)
    loss_in_excess_of_equity = get_loss_in_excess_of_equity(client_json, year)
    off_balance = get_off_balance_assets_liabilities(client_json, year)
    operating_income_expenses = get_operating_income_expenses(client_json, year)

    # trendovi
    capital_series = _sort_series(_extract_series(client_json, years, get_capital))
    current_ratio_series = _sort_series(_extract_series(client_json, years, get_current_ratio))
    nwc_series = _sort_series(_extract_series(client_json, years, get_net_working_capital))
    revenue_series = _sort_series(_extract_series(client_json, years, get_operating_revenue))
    operating_result_series = _extract_signed_operating_result_series(client_json, years)
    net_profit_series = _sort_series(_extract_series(client_json, years, get_net_profit))

    operating_result_trend = assess_operating_result_trend(operating_result_series)
    revenue_trend = assess_trend(revenue_series, direction="higher_is_better")
    capital_trend = assess_trend(capital_series, direction="higher_is_better")
    liquidity_trend = assess_trend(current_ratio_series, direction="higher_is_better")
    nwc_trend = assess_trend(nwc_series, direction="higher_is_better")
    net_profit_trend = assess_trend(net_profit_series, direction="higher_is_better")

    position_results = evaluate_position_signals(client_json, year)
    
    results = [
        # veca vrednost pozeljna
        _result(
            "ebitda_margin",
            "EBITDA Margin",
            ebitda_margin,
            (
                "na"
                if ebitda_margin is None
                else ("fail" if ebitda_margin < 0 else ("warn" if ebitda_margin < 0.03 else "pass"))
            ),
            "Industry dependent",
            "Higher is better by industry.",
        ),
        # procenat kojim dobit prelazi operativne troškove bez amortizacije
        # bruto marza na prodaju
        _result(
            "gross_profit_margin",
            "Gross Profit Margin",
            gross_margin,
            "na" if gross_margin is None else ("pass" if gross_margin > 0 else "fail"),
            "> 0",
            "Should exceed production/purchase costs.",
        ),
        _result(
        # opsti racio likvidnosti – informativan, acid test je bitniji
            "current_ratio",
            "Current Ratio",
            current_ratio,
            (
                "na" if current_ratio is None
                else ("pass" if current_ratio >= 1.5
                      else ("warn" if current_ratio >= 1.0
                            else "fail"))
            ),
            ">= 1.5 prihvatljivo, >= 2.0 poželjno",
            "Opšti racio likvidnosti – acid test (quick ratio) je primarni pokazatelj.",
        ),
        # acid test / rigorozni racio – PRIMARNI pokazatelj likvidnosti
        _result(
            "quick_ratio",
            "Quick Ratio (Acid Test)",
            quick_ratio,
            _check_min(quick_ratio, 1.0),
            ">= 1.0",
            "Rigorozni racio likvidnosti – najvažniji pokazatelj sposobnosti izmirenja kratkoročnih obaveza.",
        ),
        _result(
            "net_working_capital",
            "Net Working Capital",
            net_working_capital,
            _check_strict_min(net_working_capital, 0.0),
            "> 0",
            "Neto obrtni fond should be positive.",
        ),
        # koeficijent zaduzenosti
        # pokazuje koliko je duga pokriveno 1 dinarom sopstvenih sredstava
        _result(
            "debt_to_assets_ratio",
            "Debt-to-Assets Ratio",
            debt_to_assets_ratio,
            _check_debt_ratio(debt_to_assets_ratio),
            "<= 0.50 preferred",
            "Odnos ukupnih obaveza i ukupnih sredstava; niža vrednost ukazuje na manji stepen zaduženosti.",
        ),
        _result(
            "financial_stability_ratio",
            "Financial Stability Ratio",
            financial_stability_ratio,
            _check_target(financial_stability_ratio, 1.0),
            "~ 1.0",
            "Koeficijent finansijske stabilnosti.",
        ),
        _result(
            "dso",
            "Days Sales Outstanding (DSO)",
            dso,
            "na" if dso is None else ("pass" if dso <= 60 else "warn"),
            "Lower is better",
            "Dani vezivanja potraživanja od kupaca.",
        ),
        _result(
        # dani zaliha
            "dio",
            "Days Inventory Outstanding (DIO)",
            dio,
            "na",
            "Optimized turnover",
            "Industry and product-cycle dependent.",
        ),
        _result(
            "dpo",
            "Days Payable Outstanding (DPO)",
            dpo,
            "na" if dpo is None else ("pass" if dpo <= 60 else "warn"),
            "Lower is better",
            "Kraći rok izmirenja obaveza može ukazivati na bolju likvidnost.",
        ),
                _result(
            "cash_conversion_cycle",
            "Cash Conversion Cycle (CCC)",
            ccc,
            _check_ccc(ccc),
            "Lower is better",
            "CCC = DSO + DIO - DPO. Niži broj znači brži povrat gotovine iz operativnog ciklusa.",
        ),
        _result(
        # ukupne finasijske obaveze
            "total_financial_liabilities",
            "Total Financial Liabilities",
            total_financial_liabilities,
            "na",
            "Context metric",
            "Long-term + short-term debt and leases.",
        ),
        # stalna imovina
        _result(
            "non_current_assets",
            "Non-current Assets",
            non_current_assets,
            _check_min(non_current_assets, 0.0),
            ">= 0",
            "Stalna imovina, collateral quality focus.",
        ),
        _result(
        # osnovni kapital
            "capital",
            "Capital",
            capital,
            _check_min(capital, 0.0),
            ">= 0",
            "Osnovni kapital for creditor safety.",
        ),
        _result(
        # gubitak iznad visine kapitala
            "loss_in_excess_of_equity",
            "Loss in Excess of Equity",
            loss_in_excess_of_equity,
            _check_max(loss_in_excess_of_equity, 0.0),
            "== 0",
            "Warning sign for zero/negative equity.",
        ),
        _result(
            "receivables_to_payables_ratio",
            "Receivables to Payables Ratio",
            receivables_to_payables,
            _check_receivables_payables(receivables_to_payables),
            ">= 1.0 preferred",
            "Poželjno je da potraživanja budu veća od obaveza.",
        ),
        _result(
            "non_current_liabilities",
            "Non-current Liabilities",
            non_current_liabilities,
            "na",
            "Context metric",
            "Dugoročne obaveze.",
        ),
        _result(
            "current_financial_liabilities",
            "Current Financial Liabilities",
            current_financial_liabilities,
            "na",
            "Context metric",
            "Kratkoročne finansijske obaveze.",
        ),
        _result(
            "off_balance_items",
            "Off-balance Sheet Assets/Liabilities",
            off_balance,
            (
                "na"
                if off_balance["assets"] is None and off_balance["liabilities"] is None
                else (
                    "pass"
                    if (off_balance["assets"] or 0) == 0 and (off_balance["liabilities"] or 0) == 0
                    else "warn"
                )
            ),
            "No material contingent risk",
            "Vanbilansna aktiva/pasiva.",
        ),
        _result(
            "operating_income_expenses",
            "Operating Income/Expenses",
            operating_income_expenses,
            (
                "na"
                if operating_income_expenses["income"] is None
                or operating_income_expenses["expenses"] is None
                else (
                    "pass"
                    if operating_income_expenses["income"] >= operating_income_expenses["expenses"]
                    else "fail"
                )
            ),
            "Income >= Expenses",
            "Poslovni prihodi/rashodi trend proxy.",
        ),
        _result(
            "operating_profit_loss",
            "Operating Profit/Loss",
            operating_profit_loss,
            (
                "na"
                if operating_profit_loss["profit"] is None and operating_profit_loss["loss"] is None
                else ("fail" if (operating_profit_loss["loss"] or 0) > 0 else "pass")
            ),
            "Profit > 0, Loss = 0",
            "Visina poslovnog dobitka/gubitka.",
        ),
        # trendovi
        _result(
            "operating_result_trend",
            "Operating Result Trend",
            operating_result_trend,
            operating_result_trend["status"],
            "Stable/improving over last 3 years",
            "Ključni trend poslovnog rezultata.",
        ),
        _result(
            "operating_revenue_trend",
            "Operating Revenue Trend",
            revenue_trend,
            revenue_trend["status"],
            "Growing over last 3 years",
            "Poželjan je rast poslovnih prihoda.",
        ),
        _result(
            "net_working_capital_trend",
            "Net Working Capital Trend",
            nwc_trend,
            nwc_trend["status"],
            "Stable/growing over last 3 years",
            "Poželjan je stabilan ili rastući trend neto obrtnih sredstava.",
        ),
        _result(
            "capital_trend",
            "Capital Trend",
            capital_trend,
            capital_trend["status"],
            "Growing over last 3 years",
            "Poželjan je rastući trend kapitala.",
        ),
        _result(
            "liquidity_trend",
            "Liquidity Trend",
            liquidity_trend,
            liquidity_trend["status"],
            "Stable/growing over last 3 years",
            "Likvidnost ne bi trebalo da opada kroz vreme.",
        ),
        _result(
            "net_profit_trend",
            "Net Profit Trend",
            net_profit_trend,
            net_profit_trend["status"],
            "Growing over last 3 years",
            "Pomoćni signal trenda neto dobiti.",
        ),
    ]


    all_results = results + position_results

    red_flags = [item["id"] for item in all_results if item["status"] == "fail"]
    warning_flags = [item["id"] for item in all_results if item["status"] == "warn"]

    return {
        "year": year,
        "company_type": company_size,
        "results": all_results,
        "red_flags": red_flags,
        "warning_flags": warning_flags,
    }


def evaluate_hard_stops(client_json, year, trend_years=3):
    year = str(year)
    years = _years_window(year, trend_years)

    stop = False
    reasons = []
    high_risk_reasons = []

    # 1) Aktivna blokada
    blokade = client_json.get("blokade_od_2010")

    if isinstance(blokade, list):
        # Prolazimo kroz listu istorije blokada
        for stavka in blokade:
            # Proveravamo da li je polje "Do" string "None" ili stvarno None vrednost
            # što ukazuje na to da blokada još uvek traje
            do_datum = str(stavka.get("Do", "")).strip()
            
            if do_datum == "None" or do_datum == "":
                stop = True
                reasons.append("Aktivna blokada računa u momentu analize (nema datuma prestanka).")
                break  # Našli smo aktivnu blokadu, nema potrebe da gledamo dalje

    elif isinstance(blokade, str):
        # Ako je string, pretpostavljamo da nema blokada (kao u tvojim primerima),
        # ali možemo dodati proveru za svaki slučaj ako se pojavi neki specifičan status
        pass


    # 2) Zabeležbe: stečaj / likvidacija / bankrot
    notes = client_json.get("zabelezbe", {})
    notes_text = str(notes).lower()
    if any(term in notes_text for term in ["stečaj", "stecaj", "likvidacija", "bankrot"]):
        stop = True
        reasons.append("Zabeležba ukazuje na stečaj, likvidaciju ili bankrot.")

    # 3) Poslovni rezultat poslednje 2 godine
    op_profit_series = _sort_series(
        _extract_series(client_json, years, get_operating_profit_loss, pick_key="profit")
    )
    op_loss_series = _sort_series(
        _extract_series(client_json, years, get_operating_profit_loss, pick_key="loss")
    )

    # Pretvori profit/loss u jedinstvenu signed seriju:
    # profit > 0 => pozitivan broj, loss > 0 => negativan broj
    signed_op_result = []
    by_year = {y: 0 for y in years}
    for y, p in op_profit_series:
        by_year[y] = p if p is not None else by_year[y]
    for y, l in op_loss_series:
        if l is not None and l > 0:
            by_year[y] = -abs(l)

    for y in years:
        if y in by_year:
            signed_op_result.append((y, by_year[y]))

    signed_op_result = _sort_series(signed_op_result)

    if _last_n_negative(signed_op_result, n=2):
        #stop = True
        #reasons.append("Negativan poslovni rezultat u poslednje 2 godine.")
        high_risk_reasons.append("Negativan poslovni rezultat u poslednje 2 godine.")
    if _count_negative_periods(signed_op_result) >= 3:
        high_risk_reasons.append("Poslovni rezultat je negativan u najmanje 3 posmatrane godine.")

    # 4) Neto obrtna sredstva poslednje 2 godine
    nwc_series = _sort_series(
        _extract_series(client_json, years, get_net_working_capital)
    )
    if _last_n_negative(nwc_series, n=2):
        # po novim komentarima risk-a: ne mora automatski 0
        high_risk_reasons.append("Negativna neto obrtna sredstva u poslednje 2 godine.")

    # 5) Prihodi praktično samo u poslednjoj godini
    if has_single_year_revenue_profile(client_json, year, trend_years=trend_years):
        high_risk_reasons.append(
            "Poslovni prihodi su ostvareni praktično samo u poslednjoj godini."
        )

    return {
        "hard_stop": stop,
        "hard_stop_reasons": reasons,
        "high_risk_reasons": high_risk_reasons,
    }

def _format_value(value):
    if isinstance(value, dict):
        # Lepši prikaz za trend objekte
        if {"status", "signals", "yoy_change", "latest"}.issubset(value.keys()):
            yoy = value.get("yoy_change")
            yoy_str = "N/A" if yoy is None else f"{yoy * 100:.1f}%"
            signals = ", ".join(value.get("signals", [])) or "none"
            latest = value.get("latest")
            latest_str = "N/A" if latest is None else str(round(latest, 2))
            return f"status={value['status']}; latest={latest_str}; yoy={yoy_str}; signals={signals}"
        return str(value)

    if isinstance(value, list):
        try:
            return ", ".join([f"{y}: {v}" for y, v in value])
        except Exception:
            return str(value)

    if value is None:
        return "N/A"

    if isinstance(value, float):
        return f"{value:.4f}"

    return str(value)

def format_rules_report_md(report, company_name=None):
    title_name = company_name or "Unknown Company"
    lines = [
        f"# Financial Rules Report - {title_name}",
        "",
        f"- Year: `{report['year']}`",
        f"- Company Type: `{report['company_type']}`",
        f"- Red Flags: `{len(report['red_flags'])}`",
        f"- Warning Flags: `{len(report['warning_flags'])}`",
        "",
        "## Rule Results",
        "",
        "| Rule | Value | Status | Goal | Note |",
        "|---|---:|---|---|---|",
    ]

    for item in report["results"]:
        value_str = _format_value(item["value"])

        lines.append(
            f"| {item['label']} | {value_str} | {item['status']} | {item['goal']} | {item['note']} |"
        )

    if report["red_flags"]:
        lines.extend(["", "## Red Flags", ""])
        for flag in report["red_flags"]:
            lines.append(f"- `{flag}`")

    if report["warning_flags"]:
        lines.extend(["", "## Warning Flags", ""])
        for flag in report["warning_flags"]:
            lines.append(f"- `{flag}`")

    lines.extend(["", f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_", ""])
    return "\n".join(lines)


def save_rules_report(report, output_path, file_format="md", company_name=None):
    fmt = file_format.lower()
    if fmt not in ("md", "txt"):
        raise ValueError("file_format must be 'md' or 'txt'")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "md":
        content = format_rules_report_md(report, company_name=company_name)
    else:
        content = format_rules_report_md(report, company_name=company_name).replace("# ", "").replace("## ", "")

    path.write_text(content, encoding="utf-8")
    return str(path)
    
def calculate_penalty_factor(client_json, report, year, trend_years=3, max_penalties=3):
    year = str(year)
    years = _years_window(year, trend_years)

    penalties = []

    operating_result_series = _extract_signed_operating_result_series(client_json, years)
    operating_result_trend = assess_operating_result_trend(operating_result_series)

    revenue_series = _sort_series(_extract_series(client_json, years, get_operating_revenue))
    revenue_trend = assess_trend(revenue_series, direction="higher_is_better")

    capital_series = _sort_series(_extract_series(client_json, years, get_capital))
    capital_trend = assess_trend(capital_series, direction="higher_is_better")

    current_ratio_series = _sort_series(_extract_series(client_json, years, get_current_ratio))
    liquidity_trend = assess_trend(current_ratio_series, direction="higher_is_better")

    def add_penalty(name, factor, reason, severity):
        if any(p["name"] == name for p in penalties):
            return
        penalties.append({
            "name": name,
            "factor": factor,
            "reason": reason,
            "severity": severity,
        })

    red_flags = set(report.get("red_flags", []))
    warning_flags = set(report.get("warning_flags", []))
    strong_operating_profile = has_strong_operating_profile(report)

    # Near-breakeven and structural cash retail — reduce penalties to avoid over-penalizing
    near_breakeven = _is_near_breakeven_from_report(report)
    structural_cash_retail = _is_structural_cash_retail(report)

    # ---- 1) Snapshot red/warn iz rules report-a ----
    if "current_ratio" in red_flags:
        add_penalty(
            "low_current_ratio",
            0.95 if strong_operating_profile else 0.92,
            "Opšti racio likvidnosti je ispod preporučenog nivoa.",
            45,
        )

    if "quick_ratio" in red_flags:
        add_penalty(
            "low_quick_ratio",
            0.95,
            "Brzi racio likvidnosti je ispod preporučenog nivoa.",
            30,
        )

    if "ebitda_margin" in red_flags:
        add_penalty(
            "negative_ebitda_margin",
            0.80,
            "EBITDA marža je negativna.",
            70,
        )
    elif "ebitda_margin" in warning_flags:
        add_penalty(
            "weak_ebitda_margin",
            0.96 if strong_operating_profile else 0.93,
            "EBITDA marža je niska u odnosu na poslovne prihode.",
            35,
        )

    if "operating_profit_loss" in red_flags:
        if near_breakeven:
            add_penalty(
                "operating_loss_latest",
                0.97,
                "Poslednja godina pokazuje marginalni poslovni gubitak (ispod 0,5% prihoda).",
                20,
            )
        else:
            add_penalty(
                "operating_loss_latest",
                0.75,
                "Poslednja godina pokazuje poslovni gubitak.",
                90,
            )

    if "net_working_capital" in red_flags:
        add_penalty(
            "negative_nwc_latest",
            0.85,
            "Neto obrtna sredstva su negativna u poslednjoj godini.",
            75,
        )

    if "receivables_to_payables_ratio" in red_flags:
        add_penalty(
            "receivables_below_payables",
            0.94 if strong_operating_profile else 0.90,
            "Potraživanja su nepovoljnija u odnosu na obaveze.",
            50,
        )

    if "fixed_assets_to_revenue" in red_flags:
        add_penalty(
            "weak_fixed_asset_base",
            0.85,
            "Stalna imovina je veoma niska u odnosu na poslovne prihode.",
            65,
        )
    elif "fixed_assets_to_revenue" in warning_flags:
        add_penalty(
            "light_fixed_asset_base",
            0.93,
            "Stalna imovina je niska u odnosu na poslovne prihode.",
            35,
        )

    if "loss_in_excess_of_equity" in red_flags:
        add_penalty(
            "loss_exceeds_equity",
            0.70,
            "Gubitak iznad visine kapitala predstavlja ozbiljan rizik.",
            95,
        )

    # ---- 2) Trend analiza ----
    if "negative_two_years" in operating_result_trend["signals"]:
        add_penalty(
            "operating_loss_two_years",
            0.60,
            "Poslovni rezultat je negativan u poslednje 2 godine.",
            95,
        )
    if "negative_three_years" in operating_result_trend["signals"]:
        add_penalty(
            "operating_loss_three_years",
            0.55,
            "Poslovni rezultat je negativan tokom najmanje 3 posmatrane godine.",
            100,
        )
    elif "latest_negative" in operating_result_trend["signals"]:
        if near_breakeven:
            add_penalty(
                "operating_loss_latest_trend",
                0.97,
                "Trend poslovnog rezultata pokazuje marginalni negativan ishod u poslednjoj godini.",
                20,
            )
        else:
            add_penalty(
                "operating_loss_latest_trend",
                0.75,
                "Poslednja godina pokazuje negativan poslovni rezultat.",
                85,
            )
    elif "yoy_drop_severe" in operating_result_trend["signals"]:
        add_penalty(
            "operating_result_drop_over_30pct",
            0.80,
            "Poslovni rezultat je značajno pogoršan u odnosu na prethodnu godinu.",
            70,
        )

    if "yoy_drop_severe" in revenue_trend["signals"]:
        add_penalty(
            "revenue_drop_over_30pct",
            0.82 if strong_operating_profile else 0.75,
            "Poslovni prihodi su pali više od 30% u odnosu na prethodnu godinu.",
            55 if strong_operating_profile else 65,
        )
    elif "yoy_drop" in revenue_trend["signals"]:
        add_penalty(
            "revenue_drop",
            0.94 if strong_operating_profile else 0.88,
            "Poslovni prihodi su pali u odnosu na prethodnu godinu.",
            30 if strong_operating_profile else 45,
        )

    if "worse_than_3y_ago" in revenue_trend["signals"] and not strong_operating_profile:
        add_penalty(
            "revenue_below_3y_base",
            0.88,
            "Poslovni prihodi su niži nego pre 3 godine.",
            50,
        )

    if "worse_than_3y_ago" in capital_trend["signals"]:
        add_penalty(
            "capital_decline_3y",
            0.95,
            "Kapital je niži nego pre 3 godine.",
            20,
        )

    if "worse_than_3y_ago" in liquidity_trend["signals"]:
        add_penalty(
            "liquidity_decline_3y",
            0.96,
            "Likvidnost je slabija nego pre 3 godine.",
            20,
        )

    # ---- 3) Blokade u prethodnom periodu, ako nisu aktivne ----
    blokade = client_json.get("blokade_od_2010", {})
    blokade_text = str(blokade).lower()
    if any(term in blokade_text for term in ["blokada", "blokiran", "blokadi"]):
        add_penalty(
            "historical_blockades",
            0.90,
            "Postoje indikacije prethodnih blokada računa.",
            35,
        )

    # ---- 4) Uzmi najviše 3 najbitnija ----
    chosen = sorted(penalties, key=lambda x: x["severity"], reverse=True)[:max_penalties]

    factor = 1.0
    for p in chosen:
        factor *= p["factor"]

    factor = max(factor, 0.40)

    return {
        "penalty_factor": round(factor, 4),
        "penalties_applied": chosen,
        "all_penalty_candidates": sorted(penalties, key=lambda x: x["severity"], reverse=True),
    }

def calculate_risk_score(limit_decision: dict) -> dict:
    score = 0
    weighted_reasons = []

    rules_report = limit_decision.get("rules_report", {})
    red_flags = set(rules_report.get("red_flags", []))
    warning_flags = set(rules_report.get("warning_flags", []))
    hard_stop_reasons = limit_decision.get("hard_stop_reasons", [])
    high_risk_reasons = limit_decision.get("high_risk_reasons", [])
    penalty_factor = limit_decision.get("penalty_factor", 1.0)

    near_breakeven = _is_near_breakeven_from_report(rules_report)
    structural_cash_retail = _is_structural_cash_retail(rules_report)

    def add_reason(points: int, text: str):
        weighted_reasons.append((points, text))

    # hard stop
    if limit_decision.get("hard_stop"):
        return {
            "risk_score": 100,
            "risk_reasons": hard_stop_reasons or ["Hard stop signal."],
        }

    # core poslovni signali
    if "operating_profit_loss" in red_flags:
        if near_breakeven:
            score += 5
            add_reason(5, "Poslednja godina pokazuje marginalni poslovni gubitak (ispod 0,5% prihoda).")
        else:
            score += 30
            add_reason(30, "Poslednja godina pokazuje poslovni gubitak.")

    if "operating_result_trend" in red_flags:
        score += 35
        add_reason(35, "Trend poslovnog rezultata je izrazito nepovoljan.")
    elif "operating_result_trend" in warning_flags:
        if near_breakeven:
            score += 10
            add_reason(10, "Trend poslovnog rezultata je graničan, uz marginalni gubitak u poslednjoj godini.")
        else:
            score += 20
            add_reason(20, "Trend poslovnog rezultata zahteva oprez.")

    if "operating_revenue_trend" in red_flags:
        score += 30
        add_reason(30, "Prisutan je značajan pad poslovnih prihoda.")
    elif "operating_revenue_trend" in warning_flags:
        score += 15
        add_reason(15, "Poslovni prihodi pokazuju slabiji trend.")

    if "net_working_capital" in red_flags:
        score += 14
        add_reason(14, "Neto obrtna sredstva su negativna.")

    # high risk razlozi - uzmi najjači signal, ne sabiraj sve
    high_risk_added = 0
    high_risk_reason_text = None

    for reason in high_risk_reasons:
        if "najmanje 3 posmatrane godine" in reason:
            if high_risk_added < 25:
                high_risk_added = 25
                high_risk_reason_text = reason
        elif "Negativan poslovni rezultat u poslednje 2 godine" in reason:
            if high_risk_added < 20:
                high_risk_added = 20
                high_risk_reason_text = reason
        else:
            if high_risk_added < 15:
                high_risk_added = 15
                high_risk_reason_text = reason

    if high_risk_added > 0:
        score += high_risk_added
        add_reason(high_risk_added, high_risk_reason_text)

    if "loss_in_excess_of_equity" in red_flags:
        score += 35
        add_reason(35, "Postoji gubitak iznad visine kapitala.")

    if not structural_cash_retail:
        if "receivables_to_payables_ratio" in red_flags:
            score += 10
            add_reason(10, "Obaveze su nepovoljnije u odnosu na potraživanja.")
        elif "receivables_to_payables_ratio" in warning_flags:
            score += 8
            add_reason(8, "Odnos potraživanja i obaveza nije idealan.")

    if "operating_result_to_revenue" in red_flags:
        if near_breakeven:
            score += 5
            add_reason(5, "Poslovni rezultat je marginalno negativan u odnosu na poslovne prihode.")
        else:
            score += 25
            add_reason(25, "Poslovni rezultat je negativan u odnosu na poslovne prihode.")
    elif "operating_result_to_revenue" in warning_flags:
        score += 12
        add_reason(12, "Poslovni rezultat je nizak u odnosu na poslovne prihode.")

    if "fixed_assets_to_revenue" in red_flags:
        score += 18
        add_reason(18, "Stalna imovina je veoma niska u odnosu na obim poslovanja.")
    elif "fixed_assets_to_revenue" in warning_flags:
        score += 10
        add_reason(10, "Stalna imovina je niska u odnosu na obim poslovanja.")

    if "capital_to_assets" in warning_flags:
        score += 8
        add_reason(8, "Udeo kapitala u aktivi je nizak.")

    if "current_fin_liabilities_to_revenue" in warning_flags:
        score += 10
        add_reason(10, "Kratkoročne finansijske obaveze su visoke u odnosu na prihod.")

    if not structural_cash_retail:
        if "receivables_payables_position" in red_flags:
            score += 12
            add_reason(12, "Obaveze značajno prevazilaze potraživanja.")
        elif "receivables_payables_position" in warning_flags:
            score += 10
            add_reason(10, "Odnos potraživanja i obaveza zahteva oprez.")

    # supporting ratio signali
    if "current_ratio" in red_flags:
        score += 3
        add_reason(3, "Opšti racio likvidnosti je ispod poželjnog nivoa.")
    elif "current_ratio" in warning_flags:
        score += 1
        add_reason(1, "Opšti racio likvidnosti je graničan.")

    if "quick_ratio" in red_flags:
        score += 3
        add_reason(3, "Brzi racio likvidnosti je ispod poželjnog nivoa.")
    elif "quick_ratio" in warning_flags:
        score += 1
        add_reason(1, "Brzi racio likvidnosti je graničan.")

    if "debt_to_assets_ratio" in red_flags:
        score += 3
        add_reason(3, "Udeo ukupnih obaveza u ukupnim sredstvima je visok.")
    elif "debt_to_assets_ratio" in warning_flags:
        score += 1
        add_reason(1, "Udeo ukupnih obaveza u ukupnim sredstvima je povišen.")

    if "financial_stability_ratio" in red_flags:
        score += 3
        add_reason(3, "Koeficijent finansijske stabilnosti je nepovoljan.")
    elif "financial_stability_ratio" in warning_flags:
        score += 1
        add_reason(1, "Koeficijent finansijske stabilnosti odstupa od ciljne vrednosti.")

    if "cash_conversion_cycle" in red_flags:
        score += 2
        add_reason(2, "Cash conversion cycle je nepovoljan.")
    elif "cash_conversion_cycle" in warning_flags:
        score += 1
        add_reason(1, "Cash conversion cycle zahteva oprez.")

    if "ebitda_margin" in red_flags:
        score += 15
        add_reason(15, "EBITDA marža je negativna.")
    elif "ebitda_margin" in warning_flags:
        score += 6
        add_reason(6, "EBITDA marža je niska.")

    # dodatni signal iz penalty layer-a
    if penalty_factor <= 0.60:
        score += 20
        add_reason(20, "Kombinacija negativnih signala značajno umanjuje limit.")
    elif penalty_factor <= 0.85:
        score += 5
        add_reason(5, "Prisutni su negativni signali koji umanjuju limit.")

    score = min(score, 100)

    # top 5 jedinstvenih razloga po tezini
    unique_reasons = []
    seen = set()

    for points, reason in sorted(weighted_reasons, key=lambda x: x[0], reverse=True):
        if reason not in seen:
            seen.add(reason)
            unique_reasons.append(reason)

    return {
        "risk_score": score,
        "risk_reasons": unique_reasons[:5],
    }

def evaluate_limit_decision(client_json, year, trend_years=3):
    rules_report = evaluate_financial_rules(client_json, year, trend_years=trend_years)
    hard_stop = evaluate_hard_stops(client_json, year, trend_years=trend_years)
    penalty_info = calculate_penalty_factor(
        client_json,
        rules_report,
        year,
        trend_years=trend_years,
        max_penalties=3,
    )

    temp_limit_decision = {
        "rules_report": rules_report,
        "hard_stop": hard_stop["hard_stop"],
        "hard_stop_reasons": hard_stop["hard_stop_reasons"],
        "high_risk_reasons": hard_stop["high_risk_reasons"],
        "penalty_factor": penalty_info["penalty_factor"],
    }

    risk_info = calculate_risk_score(temp_limit_decision)

    risk_score = risk_info["risk_score"]
    print(f"Risk score: {risk_score}")

    overall_risk = "low"
    if hard_stop["hard_stop"]:
        overall_risk = "critical"
    elif risk_score >= 80:
        overall_risk = "high"
    elif risk_score >= 50:
        overall_risk = "medium"
    else:
        overall_risk = "low"

    return {
        "year": str(year),
        "company_type": rules_report["company_type"],
        "rules_report": rules_report,
        "hard_stop": hard_stop["hard_stop"],
        "hard_stop_reasons": hard_stop["hard_stop_reasons"],
        "high_risk_reasons": hard_stop["high_risk_reasons"],
        "penalty_factor": penalty_info["penalty_factor"],
        "penalties_applied": penalty_info["penalties_applied"],
        "risk_score": risk_score,
        "risk_reasons": risk_info["risk_reasons"],
        "overall_risk": overall_risk,
    }





def calculate_positive_factor(report, block_positive=False, max_bonuses=3):
    """
    Pozitivni signali služe prvenstveno za objašnjenje / komentar,
    a tek sekundarno za minimalnu korekciju limita.

    Ideja:
    - pozitivne stvari treba prepoznati i vratiti za LLM komentar
    - njihov uticaj na limit treba da bude vrlo mali
    - ako je slučaj specifično rizičan (npr. prihodi praktično samo u poslednjoj godini),
      pozitivni signali se i dalje mogu prikazati, ali bez značajnog bonusa na limit
    """
    positives = []

    def add_positive(name, factor, reason, strength):
        positives.append({
            "name": name,
            "factor": factor,
            "reason": reason,
            "strength": strength,
        })

    results = {r["id"]: r for r in report.get("results", [])}
    red_flags = set(report.get("red_flags", []))
    disqualifying_red_flags = {
        "operating_profit_loss",
        "operating_result_to_revenue",
        "loss_in_excess_of_equity",
        "fixed_assets_to_revenue",
    }

    if block_positive or (red_flags & disqualifying_red_flags):
        return {
            "positive_factor": 1.0,
            "positive_factors_applied": [],
            "all_positive_candidates": [],
        }

    revenue_trend = results.get("operating_revenue_trend", {}).get("value", {})
    operating_result_trend = results.get("operating_result_trend", {}).get("value", {})
    net_profit_trend = results.get("net_profit_trend", {}).get("value", {})
    capital_trend = results.get("capital_trend", {}).get("value", {})
    liquidity_trend = results.get("liquidity_trend", {}).get("value", {})
    ccc_result = results.get("cash_conversion_cycle", {})
    current_ratio_result = results.get("current_ratio", {})
    quick_ratio_result = results.get("quick_ratio", {})
    capital_assets_result = results.get("capital_to_assets", {})
    fixed_assets_result = results.get("fixed_assets_to_revenue", {})
    operating_result_to_revenue_result = results.get("operating_result_to_revenue", {})

    revenue_signals = revenue_trend.get("signals", []) if isinstance(revenue_trend, dict) else []
    operating_result_signals = operating_result_trend.get("signals", []) if isinstance(operating_result_trend, dict) else []
    net_profit_signals = net_profit_trend.get("signals", []) if isinstance(net_profit_trend, dict) else []
    capital_signals = capital_trend.get("signals", []) if isinstance(capital_trend, dict) else []
    liquidity_signals = liquidity_trend.get("signals", []) if isinstance(liquidity_trend, dict) else []

    # 1) Rast prihoda
    if "yoy_growth_strong" in revenue_signals:
        add_positive(
            "revenue_growth_strong",
            1.01,
            "Snažan rast poslovnih prihoda.",
            70,
        )
    elif "yoy_growth" in revenue_signals:
        add_positive(
            "revenue_growth",
            1.005,
            "Rast poslovnih prihoda.",
            55,
        )

    # 2) Poboljšanje poslovnog rezultata
    if (
        "latest_negative" not in operating_result_signals
        and "negative_two_years" not in operating_result_signals
    ):
        if "yoy_growth_strong" in operating_result_signals:
            add_positive(
                "operating_result_improvement_strong",
                1.01,
                "Snažno poboljšanje poslovnog rezultata.",
                75,
            )
        elif "yoy_growth" in operating_result_signals:
            add_positive(
                "operating_result_improvement",
                1.005,
                "Poboljšanje poslovnog rezultata.",
                60,
            )

    # 3) Neto dobit
    if "yoy_growth_strong" in net_profit_signals:
        add_positive(
            "net_profit_growth_strong",
            1.005,
            "Snažan rast neto dobiti.",
            45,
        )
    elif "yoy_growth" in net_profit_signals:
        add_positive(
            "net_profit_growth",
            1.003,
            "Rast neto dobiti.",
            35,
        )

    # 4) Rast kapitala
    if (
        capital_assets_result.get("status") == "pass"
        and fixed_assets_result.get("status") != "fail"
    ):
        if "yoy_growth_strong" in capital_signals or "better_than_3y_ago" in capital_signals:
            add_positive(
                "capital_growth",
                1.005,
                "Rast kapitala i stabilna kapitalna pozicija.",
                40,
            )

    # 5) Zdrava operativna profitabilnost
    if operating_result_to_revenue_result.get("status") == "pass":
        add_positive(
            "healthy_operating_profitability",
            1.005,
            "Zdrav odnos poslovnog rezultata i poslovnih prihoda.",
            50,
        )

    # 6) Dobra likvidnost / obrt gotovine
    if (
        current_ratio_result.get("status") == "pass"
        and quick_ratio_result.get("status") == "pass"
        and ccc_result.get("status") == "pass"
        and "yoy_drop_severe" not in liquidity_signals
    ):
        add_positive(
            "healthy_liquidity_profile",
            1.003,
            "Likvidnost i obrt gotovine su zadovoljavajući.",
            30,
        )

    chosen = sorted(positives, key=lambda x: x["strength"], reverse=True)[:max_bonuses]

    factor = 1.0
    for p in chosen:
        factor *= p["factor"]

    # Vrlo mali plafon - pozitivno ne sme mnogo da popravi limit
    factor = min(factor, 1.01)

    return {
        "positive_factor": round(factor, 4),
        "positive_factors_applied": chosen,
        "all_positive_candidates": sorted(positives, key=lambda x: x["strength"], reverse=True),
    }


def is_very_bad_client(limit_decision: dict) -> bool:
    penalty_factor = limit_decision.get("penalty_factor", 1.0)
    risk_score = limit_decision.get("risk_score", 0)
    company_type = limit_decision.get("company_type")
    red_flags = set(limit_decision.get("rules_report", {}).get("red_flags", []))

    core_red_flags = {
        "operating_profit_loss",
        "operating_result_trend",
        "operating_revenue_trend",
        "loss_in_excess_of_equity",
        "operating_result_to_revenue",
    }

    core_red_count = len(red_flags & core_red_flags)
    weak_asset_base = "fixed_assets_to_revenue" in red_flags
    weak_receivable_position = "receivables_payables_position" in red_flags

    # strože samo za ozbiljne profile
    if risk_score >= 80 and core_red_count >= 2:
        return True

    if penalty_factor <= 0.70 and core_red_count >= 2:
        return True

    if core_red_count >= 3:
        return True

    if weak_asset_base and weak_receivable_position and "operating_profit_loss" in red_flags:
        return True

    # za mikro ne želimo lako da upadne u "very bad"
    if company_type == "micro":
        return False

    return False


def adjust_base_pct(
    base_pct: float,
    penalty_factor: float,
    positive_factor: float,
    neutral_score: float = 0.85,
    sensitivity: float = 2.0,
    lo: float = 0.5,
    hi: float = 2.5,
):
    """
    Finansije koriguju bazni procenat gore/dole,
    ali finalni procenat ostaje u konzervativnom opsegu.

    score:
      penalty_factor * positive_factor

    neutral_score:
      oko ove vrednosti procenat ostaje blizu base_pct

    sensitivity:
      koliko jako score pomera procenat
    """
    score = penalty_factor * positive_factor

    adjusted_pct = base_pct + (score - neutral_score) * sensitivity
    adjusted_pct = max(lo, min(hi, adjusted_pct))

    return round(adjusted_pct, 4), round(score, 4)


def decide_final_limit(client_json, year, founding_date, analysis_date):
    """
    Finalna odluka:
    - hard stop -> 0
    - novoosnovan u tekućoj godini -> 0
    - osnovan prošle godine -> 600000
    - single-year revenue profile + više jakih negativnih signala -> 0 / advance only
    - veoma loš komitent -> 600000
    - ostali -> kontinualan procenat 1%-3%
    """

    founding_year = founding_date.year if founding_date else None
    analysis_year = analysis_date.year if analysis_date else None
    company_age_days = None
    if founding_date and analysis_date:
        company_age_days = (analysis_date - founding_date).days
    is_younger_than_18_months = (
        company_age_days is not None and company_age_days < 548
    )

    # 1) Pravilo za novoosnovane
    if founding_year is not None and analysis_year is not None and founding_year == analysis_year:
        return {
            "decision_type": "manual_review",
            "final_limit": None,
            "final_pct": None,
            "requires_guarantees": False,
            "security_requirement": "manual_review_required",
            "security_requirement_reason": "Komitent je osnovan u godini analize; potreban je ručni pregled risk tima i provera povezanih pravnih lica.",
            "recommendation": "manual_review",
            "reason": "Komitent je osnovan u godini analize; slučaj ne ide automatski na 0 već zahteva ručni pregled risk tima."
        }

    if is_younger_than_18_months and not has_financial_indicators(client_json, year):
        return {
            "decision_type": "young_company_no_financial_indicators",
            "final_limit": 0,
            "final_pct": 0.0,
            "requires_guarantees": True,
            "security_requirement": "advance_payment_only",
            "security_requirement_reason": "Komitent je mlađi od 1.5 godine i nema finansijske pokazatelje za pouzdanu procenu odloženog plaćanja.",
            "recommendation": "advance_only",
            "reason": "Od osnivanja do datuma analize nije prošlo 1.5 godina, a nema finansijskih pokazatelja; preporuka je saradnja samo uz avansno plaćanje."
        }

    # 2) Finansijska analiza
    limit_decision = evaluate_limit_decision(client_json, year)
    rules_report = limit_decision["rules_report"]

    # 3) Hard stop
    if limit_decision["hard_stop"]:
        return {
            "year": year,
            "decision_type": "hard_stop",
            "final_limit": 0,
            "final_pct": 0.0,
            "reason": "Primenjen hard stop.",
            "hard_stop_reasons": limit_decision.get("hard_stop_reasons", []),
            "overall_risk": limit_decision.get("overall_risk"),
            "risk_score": limit_decision.get("risk_score"),
            "risk_reasons": limit_decision.get("risk_reasons", []),
            "founding_year": founding_year,
            "security_requirement": "advance_payment_only",
            "security_requirement_reason": "Hard stop isključuje odloženo plaćanje.",
        }

    red_flags = set(rules_report.get("red_flags", []))
    warning_flags = set(rules_report.get("warning_flags", []))

    # 4) Strogi override:
    # prihodi praktično samo u poslednjoj godini + više jakih negativnih signala
    single_year_revenue = has_single_year_revenue_profile(client_json, year, trend_years=3)

    if (
        single_year_revenue
        and len(red_flags) >= 3
        and (
            len(limit_decision.get("high_risk_reasons", [])) >= 1
            or "liquidity_trend" in red_flags
            or "receivables_to_payables_ratio" in red_flags
        )
    ):
        return {
            "year": year,
            "decision_type": "advance_only_single_year_revenue_profile",
            "final_limit": 0,
            "final_pct": 0.0,
            "reason": "Poslovni prihodi su ostvareni praktično samo u poslednjoj godini, uz više izraženih negativnih pokazatelja; preporuka je saradnja samo uz avansno plaćanje.",
            "founding_year": founding_year,
            "overall_risk": limit_decision.get("overall_risk"),
            "risk_score": limit_decision.get("risk_score"),
            "risk_reasons": limit_decision.get("risk_reasons", []),
            "red_flags": list(red_flags),
            "warning_flags": list(warning_flags),
            "requires_guarantees": True,
            "security_requirement": "advance_payment_only",
            "security_requirement_reason": "Prihodi su koncentrisani u poslednjoj godini uz izražene negativne pokazatelje.",
            "recommendation": "advance_only",
        }

    if is_very_bad_client(limit_decision):
        return {
            "year": year,
            "decision_type": "very_bad_client",
            "final_limit": MIN_LIMIT_RSD,
            "final_pct": None,
            "reason": "Komitent je ocenjen kao veoma rizičan, bez hard stop razloga.",
            "overall_risk": limit_decision.get("overall_risk"),
            "penalty_factor": limit_decision.get("penalty_factor"),
            "red_flags": rules_report.get("red_flags", []),
            "warning_flags": rules_report.get("warning_flags", []),
            "founding_year": founding_year,
            "security_requirement": "guarantees_required",
            "security_requirement_reason": "Vrlo rizičan profil zahteva minimalan limit uz dodatno obezbeđenje.",
            "recommendation": "minimal_limit_with_security",
        }

    if is_weak_micro_client(limit_decision):
        return {
            "year": year,
            "decision_type": "weak_micro_client",
            "final_limit": MIN_LIMIT_RSD,
            "final_pct": None,
            "reason": "Mikro komitent sa slabijim finansijskim pokazateljima upućuje na minimalni limit.",
            "overall_risk": limit_decision.get("overall_risk"),
            "penalty_factor": limit_decision.get("penalty_factor"),
            "risk_score": limit_decision.get("risk_score"),
            "risk_reasons": limit_decision.get("risk_reasons", []),
            "red_flags": rules_report.get("red_flags", []),
            "warning_flags": rules_report.get("warning_flags", []),
            "founding_year": founding_year,
            "security_requirement": "guarantees_required",
            "security_requirement_reason": "Slabiji mikro profil zahteva minimalan limit uz dodatno obezbeđenje.",
            "recommendation": "minimal_limit_with_security",
        }

    # 5) Standardna procena po prihodu + finansijska korekcija
    prihod_rsd = get_operating_revenue(client_json, year)
    if prihod_rsd is None or prihod_rsd <= 0:
        return {
            "year": year,
            "decision_type": "no_revenue_data",
            "final_limit": MIN_LIMIT_RSD,
            "final_pct": None,
            "reason": "Nedostaje validan poslovni prihod; primenjen fallback limit.",
            "founding_year": founding_year,
            "security_requirement": "guarantees_required",
            "security_requirement_reason": "Bez validnog poslovnog prihoda odobren je samo minimalan limit uz dodatno obezbeđenje.",
        }

    base_pct = propose_base_pct(
        company_size=limit_decision["company_type"],
        overall_risk=limit_decision["overall_risk"],
    )

    block_positive = has_single_year_revenue_profile(client_json, year, trend_years=3)

    positive_info = calculate_positive_factor(
        rules_report,
        block_positive=block_positive
    )

    adjusted_pct, combined_score = adjust_base_pct(
        base_pct=base_pct,
        penalty_factor=limit_decision["penalty_factor"],
        positive_factor=positive_info["positive_factor"],
        neutral_score=0.90,
        sensitivity=1.2,
        lo=0.0,
        hi=2.5,
    )
    uplift_applied = False
    if qualifies_for_growth_uplift(limit_decision, positive_info):
        adjusted_pct = min(adjusted_pct + 0.20, 2.5)
        uplift_applied = True
    final_pct = snap_pct_to_policy_bucket(adjusted_pct)

    base_limit = prihod_rsd * base_pct / 100.0
    raw_limit = prihod_rsd * final_pct / 100.0
    final_limit = int(min(raw_limit, ABSOLUTE_LIMIT_CAP_RSD))
    security_requirement, security_requirement_reason = _determine_security_requirement(limit_decision, final_limit)
    top_negative_drivers = [item["reason"] for item in limit_decision.get("penalties_applied", [])]
    top_positive_drivers = [item["reason"] for item in positive_info.get("positive_factors_applied", [])]
    limit_reduction_summary = _build_reduction_summary(base_pct, final_pct, limit_decision.get("penalties_applied", []))
    if uplift_applied:
        limit_reduction_summary += " Pozitivni trendovi su opravdali ograničeno povećanje finalnog procenta."
    decision_type = "standard_pct_by_size_and_risk"

    if should_route_to_manual_review(limit_decision):
        decision_type = "manual_review"
        security_requirement = "manual_review_required"
        security_requirement_reason = "Kombinacija mešovitih signala zahteva dodatnu proveru risk tima pre konačnog odobrenja."
    elif is_younger_than_18_months:
        decision_type = "manual_review"
        security_requirement = "manual_review_required"
        security_requirement_reason = "Komitent je mlađi od 1.5 godine; finansijska analiza je urađena, ali je potrebna dodatna provera risk tima uz pojačan oprez."

    return {
        "year": year,
        "decision_type": decision_type,
        "founding_year": founding_year,
        "analysis_date": analysis_date.isoformat() if analysis_date else None,
        "overall_risk": limit_decision.get("overall_risk"),
        "company_type": limit_decision.get("company_type"),
        "risk_score": limit_decision.get("risk_score"),
        "risk_reasons": limit_decision.get("risk_reasons", []),

        "revenue_rsd": prihod_rsd,
        "revenue_basis": "operating_revenue",

        "base_pct": round(base_pct, 4),
        "base_limit": int(base_limit),
        "base_pct_reason": (
            f"Bazni procenat je određen na osnovu veličine komitenta "
            f"({limit_decision.get('company_type')}) i nivoa rizika ({limit_decision.get('overall_risk')})."
        ),

        "penalty_factor": limit_decision["penalty_factor"],
        "positive_factor": positive_info["positive_factor"],
        "combined_score": combined_score,

        "final_pct": final_pct,
        "model_pct_before_bucket": adjusted_pct,
        "final_limit": final_limit,
        "absolute_cap_rsd": ABSOLUTE_LIMIT_CAP_RSD,

        "red_flags": rules_report.get("red_flags", []),
        "warning_flags": rules_report.get("warning_flags", []),
        "penalties_applied": limit_decision.get("penalties_applied", []),
        "positive_factors_applied": positive_info.get("positive_factors_applied", []),
        "top_negative_drivers": top_negative_drivers,
        "top_positive_drivers": top_positive_drivers,
        "limit_reduction_summary": limit_reduction_summary,
        "growth_uplift_applied": uplift_applied,
        "security_requirement": security_requirement,
        "security_requirement_reason": security_requirement_reason,
        "recommendation": "manual_review" if decision_type == "manual_review" else "approve_with_policy_controls",
    }
