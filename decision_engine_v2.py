from rules import (
    ABSOLUTE_LIMIT_CAP_RSD,
    MIN_LIMIT_RSD,
    EUR_RSD,
    snap_pct_to_policy_bucket,
    evaluate_financial_rules,
    evaluate_hard_stops,
    has_single_year_revenue_profile,
    has_financial_indicators,
)
from helpers import (
    company_type,
    get_assets,
    get_current_financial_liabilities,
    get_current_ratio,
    get_debt_to_assets_ratio,
    get_dpo,
    get_dso,
    get_cash_conversion_cycle,
    get_ebitda_margin,
    get_capital,
    get_n_emp,
    get_net_profit,
    get_net_working_capital,
    get_operating_profit_loss,
    get_operating_revenue,
    get_quick_ratio,
)


def _get_result_map(report):
    return {item["id"]: item for item in report.get("results", [])}


def _status(item):
    if not item:
        return "na"
    return item.get("status", "na")


def _append_reason(reasons, text):
    if text and text not in reasons:
        reasons.append(text)


def _extract_signed_operating_result(result_payload):
    if not isinstance(result_payload, dict):
        return None
    loss = result_payload.get("loss")
    profit = result_payload.get("profit")
    if loss not in (None, 0):
        return -abs(loss)
    return profit


def _collect_positive_drivers(results):
    positives = []
    if _status(results.get("operating_revenue_trend")) == "pass":
        _append_reason(positives, "Poslovni prihodi pokazuju stabilan ili rastući trend.")
    if _status(results.get("ebitda_margin")) == "pass" or _status(results.get("operating_result_to_revenue")) == "pass":
        _append_reason(positives, "Operativna profitabilnost je zadovoljavajuća.")
    if (
        _status(results.get("receivables_to_payables_ratio")) == "pass"
        and _status(results.get("receivables_payables_position")) == "pass"
    ):
        _append_reason(positives, "Odnos potraživanja i obaveza je prihvatljiv.")
    if _status(results.get("capital_trend")) == "pass":
        _append_reason(positives, "Kapital pokazuje stabilan ili rastući trend.")
    if _status(results.get("net_working_capital")) == "pass":
        _append_reason(positives, "Neto obrtna sredstva su pozitivna.")
    return positives[:4]


def _determine_security_requirement_v2(final_limit, decision_type, overall_risk, weak_collateral_base):
    if decision_type in {"hard_stop", "young_company_no_financial_indicators"}:
        return "advance_payment_only", "Zaključak ne podržava odloženo plaćanje; preporuka je saradnja samo uz avans."
    if decision_type == "manual_review" or final_limit is None:
        return "manual_review_required", "Pre konačne odluke potreban je dodatni pregled risk tima."
    if weak_collateral_base:
        return "guarantees_required", "Slabija imovinska i likvidnosna osnova zahteva dodatna sredstva obezbeđenja."
    if overall_risk == "high":
        return "insurance_required", "Povišen rizik zahteva dodatno osiguranje potraživanja."
    if final_limit >= 15_000_000:
        return "insurance_required", "Viši iznos limita zahteva dodatno osiguranje."
    return "standard_security", "Standardna sredstva obezbeđenja su dovoljna."


def _build_core_assessment(results, revenue_rsd, current_ratio, quick_ratio, dso, dpo, ccc, single_year_revenue):
    positives = []
    warnings = []
    failures = []
    score = 0
    anomaly = None

    revenue_trend = results.get("operating_revenue_trend", {}).get("value", {})
    revenue_signals = revenue_trend.get("signals", []) if isinstance(revenue_trend, dict) else []
    yoy_change = revenue_trend.get("yoy_change") if isinstance(revenue_trend, dict) else None

    if revenue_rsd and revenue_rsd > 0:
        _append_reason(positives, "Poslovni prihodi postoje i predstavljaju glavnu bazu za procenu.")
    else:
        _append_reason(failures, "Nedostaje validan poslovni prihod za procenu.")
        score -= 5

    if single_year_revenue:
        anomaly = "single_year_revenue_profile"
        _append_reason(failures, "Poslovni prihodi su ostvareni praktično samo u poslednjoj godini.")
        score -= 4
    elif "yoy_growth_strong" in revenue_signals and yoy_change is not None and yoy_change >= 1.5:
        anomaly = "strong_revenue_jump"
        _append_reason(warnings, "Prisutan je nagli skok poslovnih prihoda koji zahteva dodatni oprez u tumačenju obima poslovanja.")
        score += 1
    elif yoy_change is not None and yoy_change <= -0.20:
        # Pad prihoda >= 20% je značajan signal čak i kad nije "severe" po standardnom pragu
        anomaly = "strong_revenue_drop"
        _append_reason(failures, "Prisutan je značajan pad poslovnih prihoda u poslednjoj godini – osnova za procenu je materijalno oslabila.")
        score -= 3
    elif _status(results.get("operating_revenue_trend")) == "pass":
        _append_reason(positives, "Trend poslovnih prihoda je povoljan.")
        score += 2
    elif _status(results.get("operating_revenue_trend")) == "warn":
        _append_reason(warnings, "Trend poslovnih prihoda zahteva oprez.")
        score -= 1
    else:
        _append_reason(failures, "Trend poslovnih prihoda je nepovoljan.")
        score -= 3

    _ebitda_ok = (
        _status(results.get("ebitda_margin")) == "pass"
        or _status(results.get("operating_result_to_revenue")) == "pass"
    )
    # EBITDA je izmereno i blizu nule – razlikujemo od "nema podataka" (na)
    _ebitda_warn = _status(results.get("ebitda_margin")) == "warn"
    _revenue_declining = _status(results.get("operating_revenue_trend")) in {"warn", "fail"}
    _op_result_trend_declining = _status(results.get("operating_result_trend")) in {"warn", "fail"}
    # Marža je slaba ako operativni rezultat nije pass (tj. warn/fail/na)
    _marginal_profitability = _status(results.get("operating_result_to_revenue")) not in {"pass"}

    if _ebitda_ok and _revenue_declining and _op_result_trend_declining and _marginal_profitability:
        # Operativna sposobnost nominalno prisutna, ali uz pad prihoda i poslovnog rezultata
        _append_reason(warnings, "Operativna sposobnost je prisutna, ali uz pad prihoda i poslovnog rezultata kroz posmatrani period.")
        score -= 1
    elif _ebitda_ok and not _ebitda_warn:
        # EBITDA prolazi ili nema podataka, a operativni rezultat je dobar – pun bonus
        _append_reason(positives, "Operativna sposobnost je zadovoljavajuća.")
        score += 2
    elif _ebitda_ok and _ebitda_warn:
        # EBITDA je izmereno i blizu nule – operativni rezultat spašava, ali bez punog bonusa
        # Kompanija ima operativni dobitak, ali nema EBITDA pufer za servisiranje duga
        _append_reason(warnings, "Operativna sposobnost je prisutna, ali EBITDA je zanemarljiv – nema dovoljnog pufera za servisiranje duga.")
        score += 1
    elif _status(results.get("ebitda_margin")) == "warn" or _status(results.get("operating_result_to_revenue")) == "warn":
        _append_reason(warnings, "Operativna sposobnost je prisutna, ali skromna.")
        score -= 1
    else:
        _append_reason(failures, "Operativna sposobnost nije dovoljno uverljiva.")
        score -= 3

    # Gotovinski model poslovanja: DSO ispod 2 dana znači da firma praktično ne prodaje na kredit.
    # Niska potraživanja u tom slučaju su strukturna karakteristika, ne kreditni rizik.
    # Quick ratio i CCC (dominiran inventarom, ne naplatom) tada nisu merodavni za kreditnu ocenu.
    structural_cash = dso is not None and dso < 2.0

    receivables_good = _status(results.get("receivables_to_payables_ratio")) == "pass" and _status(results.get("receivables_payables_position")) == "pass"
    receivables_mixed = (
        _status(results.get("receivables_to_payables_ratio")) in {"pass", "warn"}
        and _status(results.get("receivables_payables_position")) in {"pass", "warn"}
    )
    if structural_cash:
        _append_reason(warnings, "Odnos potraživanja i obaveza je nominalno slab – kompanija posluje gotovinski i nema materijalnih potraživanja od kupaca.")
    elif receivables_good:
        _append_reason(positives, "Odnos potraživanja i obaveza je uredan.")
        score += 2
    elif receivables_mixed:
        _append_reason(warnings, "Odnos potraživanja i obaveza je prihvatljiv, ali ne i komforan.")
        score -= 1
    else:
        _append_reason(failures, "Odnos potraživanja i obaveza je nepovoljan.")
        score -= 3

    # Opšti racio likvidnosti je JEDINA mera likvidnosti prema internoj politici risk tima.
    # Rigorozni racio se ne koristi u oceni.
    liquidity_fail = 0
    liquidity_warn = 0
    if current_ratio is not None and current_ratio < 1.0:
        liquidity_fail += 2
    elif current_ratio is not None and current_ratio < 1.2:
        liquidity_fail += 1
    elif current_ratio is not None and current_ratio < 1.5:
        liquidity_warn += 1

    if ccc is not None and not structural_cash and ccc > 90:
        liquidity_warn += 1

    if dso is not None and dpo is not None and dso - dpo > 30:
        liquidity_warn += 1

    # Jak operativni profil: EBITDA prolazi i operativni rezultat trend je stabilan/rastući.
    # U tom slučaju slabija likvidnost (npr. zbog sezonskog inventara ili bankarskih linija)
    # ne bi trebalo da dominira nad solidnom operativnom sposobnošću.
    # Drugi uslov: trading firme često imaju nizak EBITDA margin ali solidan operating_result_to_revenue –
    # ako je i operativni rezultat trend stabilan/rastući, profil je jak bez obzira na EBITDA warn.
    strong_operating = (
        (_ebitda_ok and not _ebitda_warn and not _op_result_trend_declining)
        or (
            _status(results.get("operating_result_to_revenue")) == "pass"
            and _status(results.get("operating_result_trend")) == "pass"
        )
    )

    if _status(results.get("liquidity_trend")) == "fail":
        if current_ratio is not None and current_ratio > 5.0:
            # Apsolutni nivo je i dalje odličan – trend pad je nominalan, ne realni likvidnosni rizik
            _append_reason(warnings, "Likvidnost pokazuje pad trenda, ali je apsolutni nivo i dalje veoma visok.")
            score -= 1
        else:
            _append_reason(failures, "Likvidnost pokazuje izražen pad ili nestabilnost kroz posmatrani period.")
            score -= 3
    elif _status(results.get("liquidity_trend")) == "warn":
        _append_reason(warnings, "Likvidnost kroz vreme zahteva oprez.")
        if not strong_operating:
            score -= 1
        # else: informativan signal, ne penalizuje se uz jak operativni profil

    if liquidity_fail >= 2:
        _append_reason(failures, "Likvidnost ukazuje na slabiju sposobnost urednog izmirenja obaveza.")
        score -= 1 if strong_operating else 3
    elif liquidity_fail == 1 or liquidity_warn >= 2:
        _append_reason(warnings, "Likvidnost zahteva oprez.")
        score -= 1
    else:
        _append_reason(positives, "Likvidnost je pod kontrolom.")
        score += 1

    # Svi ključni trendovi pozitivni – jak rastući profil koji zaslužuje višu ocenu.
    # Uslov je visok: svih 5 trendova mora biti pass.
    # Isključujemo kompanije sa ekstremnim skokovima prihoda (anomaly) jer je taj signal
    # već penalizovan (-0.5), i kombinovanje sa +3 bonusom bi ih poništilo.
    all_trends_positive = (
        _status(results.get("operating_revenue_trend")) == "pass"
        and _status(results.get("operating_result_trend")) == "pass"
        and _status(results.get("net_working_capital_trend")) == "pass"
        and _status(results.get("capital_trend")) == "pass"
        and _status(results.get("liquidity_trend")) == "pass"
        and anomaly not in {"strong_revenue_jump", "strong_revenue_drop", "single_year_revenue_profile"}
        and not single_year_revenue
    )
    if all_trends_positive:
        _append_reason(positives, "Svi ključni trendovi (prihodi, rezultat, NWC, kapital, likvidnost) su pozitivni – rastući profil.")
        score += 3

    return {
        "score": score,
        "positives": positives,
        "warnings": warnings,
        "failures": failures,
        "anomaly": anomaly,
    }


def _build_secondary_assessment(results, client_size, net_working_capital, current_financial_liabilities, debt_to_assets_ratio, operating_result_signed):
    positives = []
    warnings = []
    failures = []
    score = 0

    if _status(results.get("capital_trend")) == "pass":
        _append_reason(positives, "Trend kapitala je povoljan.")
        score += 1
    elif _status(results.get("capital_trend")) == "fail":
        _append_reason(failures, "Trend kapitala je nepovoljan.")
        score -= 1

    if _status(results.get("current_fin_liabilities_to_revenue")) == "warn":
        _append_reason(warnings, "Kratkoročne finansijske obaveze su povišene u odnosu na prihod.")
        score -= 1
    elif current_financial_liabilities == 0:
        _append_reason(positives, "Kratkoročne finansijske obaveze nisu izražene.")

    if net_working_capital is not None and net_working_capital > 0:
        _append_reason(positives, "Neto obrtna sredstva su pozitivna.")
        score += 1
    elif _status(results.get("net_working_capital")) == "fail":
        _append_reason(failures, "Neto obrtna sredstva su negativna.")
        score -= 2

    # quick_ratio je već uračunat u core assessment (liquidity_fail) — ne ponavljamo ovde

    if operating_result_signed is not None and operating_result_signed < 0:
        _append_reason(failures, "Postoji kontinuirani ili poslednji negativan operativni rezultat.")
        score -= 2
    elif _status(results.get("operating_result_trend")) == "warn":
        _append_reason(warnings, "Kontinuitet poslovnog rezultata zahteva oprez.")
        score -= 1

    if _status(results.get("fixed_assets_to_revenue")) == "fail":
        _append_reason(warnings, "Struktura imovine je slabija u odnosu na obim poslovanja.")
        score -= 1
    elif _status(results.get("fixed_assets_to_revenue")) == "warn":
        _append_reason(warnings, "Struktura imovine je osrednja.")

    if client_size == "large":
        score += 1
    elif client_size == "micro":
        _append_reason(warnings, "Veličina preduzeća zahteva konzervativniji pristup, ali nije presudna sama po sebi.")

    if debt_to_assets_ratio is not None and debt_to_assets_ratio > 0.9:
        _append_reason(warnings, "Zaduženost je povišena, ali se tretira kao dopunski signal.")
        score -= 1

    return {
        "score": score,
        "positives": positives,
        "warnings": warnings,
        "failures": failures,
    }


def _build_informative_context(client_size, n_emp):
    notes = []
    if client_size:
        _append_reason(notes, f"Veličina preduzeća je klasifikovana kao {client_size} i koristi se samo kao dopunski korektor.")
    if n_emp is not None:
        _append_reason(notes, f"Broj zaposlenih ({n_emp}) se tretira kao informativan podatak.")
    return notes


def _core_grade_from_score(score):
    if score >= 6:
        return "strong"
    if score >= 2:
        return "good"
    if score >= -1:
        return "mixed"
    return "weak"


def _baseline_pct_from_grade(core_grade):
    if core_grade == "strong":
        return 2.5
    if core_grade == "good":
        return 2.0
    if core_grade == "mixed":
        return 1.5
    return 1.0


def decide_final_limit_v2(client_json, year, founding_date, analysis_date):
    year = str(year)
    rules_report = evaluate_financial_rules(client_json, year)
    hard_stop = evaluate_hard_stops(client_json, year)
    results = _get_result_map(rules_report)

    revenue_rsd = get_operating_revenue(client_json, year)
    assets_rsd = get_assets(client_json, year)
    n_emp = get_n_emp(client_json, year)

    client_size = company_type(
        n_emp,
        revenue_rsd / EUR_RSD if revenue_rsd is not None else None,
        assets_rsd / EUR_RSD if assets_rsd is not None else None,
    )

    founding_year = founding_date.year if founding_date else None
    analysis_year = analysis_date.year if analysis_date else None
    company_age_days = (analysis_date - founding_date).days if founding_date and analysis_date else None
    is_younger_than_18_months = company_age_days is not None and company_age_days < 548
    has_financials = has_financial_indicators(client_json, year)
    single_year_revenue = has_single_year_revenue_profile(client_json, year, trend_years=3)

    current_ratio = get_current_ratio(client_json, year)
    quick_ratio = get_quick_ratio(client_json, year)
    dso = get_dso(client_json, year)
    dpo = get_dpo(client_json, year)
    ccc = get_cash_conversion_cycle(client_json, year)
    net_working_capital = get_net_working_capital(client_json, year)
    current_financial_liabilities = get_current_financial_liabilities(client_json, year)
    debt_to_assets_ratio = get_debt_to_assets_ratio(client_json, year)
    operating_result_signed = _extract_signed_operating_result(get_operating_profit_loss(client_json, year))

    collected_positive_drivers = _collect_positive_drivers(results)

    if hard_stop.get("hard_stop"):
        return {
            "engine_version": "v2",
            "year": year,
            "decision_type": "hard_stop",
            "founding_year": founding_year,
            "analysis_date": analysis_date.isoformat() if analysis_date else None,
            "company_type": client_size,
            "overall_risk": "critical",
            "risk_score": None,
            "risk_reasons": hard_stop.get("hard_stop_reasons", []),
            "revenue_rsd": revenue_rsd,
            "revenue_basis": "operating_revenue",
            "base_pct": 0.0,
            "base_limit": 0,
            "base_pct_reason": "Primenjen je hard stop pre finansijskog zaključivanja.",
            "penalty_factor": None,
            "positive_factor": None,
            "combined_score": None,
            "final_pct": 0.0,
            "model_pct_before_bucket": 0.0,
            "final_limit": 0,
            "absolute_cap_rsd": ABSOLUTE_LIMIT_CAP_RSD,
            "red_flags": rules_report.get("red_flags", []),
            "warning_flags": rules_report.get("warning_flags", []),
            "penalties_applied": [],
            "positive_factors_applied": [],
            "top_negative_drivers": hard_stop.get("hard_stop_reasons", []),
            "top_positive_drivers": collected_positive_drivers,
            "limit_reduction_summary": "Primenjen hard stop.",
            "growth_uplift_applied": False,
            "security_requirement": "advance_payment_only",
            "security_requirement_reason": "Hard stop isključuje odloženo plaćanje.",
            "recommendation": "advance_only",
            "high_risk_reasons": hard_stop.get("high_risk_reasons", []),
            "summary": "Primenjen je hard stop na osnovu dostupnih podataka.",
            "core_assessment": {},
            "secondary_assessment": {},
            "informative_notes": [],
        }

    if founding_year is not None and analysis_year is not None and founding_year == analysis_year:
        return {
            "engine_version": "v2",
            "year": year,
            "decision_type": "manual_review",
            "founding_year": founding_year,
            "analysis_date": analysis_date.isoformat() if analysis_date else None,
            "company_type": client_size,
            "overall_risk": "medium",
            "risk_score": None,
            "risk_reasons": ["Komitent je osnovan u godini analize i zahteva ručni pregled."],
            "revenue_rsd": revenue_rsd,
            "revenue_basis": "operating_revenue",
            "base_pct": None,
            "base_limit": None,
            "base_pct_reason": "Novoosnovani klijent se ne procenjuje automatskim limitom.",
            "penalty_factor": None,
            "positive_factor": None,
            "combined_score": None,
            "final_pct": None,
            "model_pct_before_bucket": None,
            "final_limit": None,
            "absolute_cap_rsd": ABSOLUTE_LIMIT_CAP_RSD,
            "red_flags": rules_report.get("red_flags", []),
            "warning_flags": rules_report.get("warning_flags", []),
            "penalties_applied": [],
            "positive_factors_applied": [],
            "top_negative_drivers": ["Komitent je osnovan u godini analize i zahteva ručni pregled."],
            "top_positive_drivers": collected_positive_drivers,
            "limit_reduction_summary": "Novoosnovani klijent se vodi na manual review.",
            "growth_uplift_applied": False,
            "security_requirement": "manual_review_required",
            "security_requirement_reason": "Potrebna je dodatna provera povezanih pravnih lica i procena risk tima.",
            "recommendation": "manual_review",
            "high_risk_reasons": hard_stop.get("high_risk_reasons", []),
            "summary": "Novoosnovan klijent se ne odbija automatski, ali zahteva ručni pregled risk tima.",
            "core_assessment": {},
            "secondary_assessment": {},
            "informative_notes": [],
        }

    if is_younger_than_18_months and not has_financials:
        return {
            "engine_version": "v2",
            "year": year,
            "decision_type": "young_company_no_financial_indicators",
            "founding_year": founding_year,
            "analysis_date": analysis_date.isoformat() if analysis_date else None,
            "company_type": client_size,
            "overall_risk": "high",
            "risk_score": None,
            "risk_reasons": ["Klijent je mlađi od 1.5 godine i nema dovoljno finansijskih pokazatelja za pouzdanu procenu."],
            "revenue_rsd": revenue_rsd,
            "revenue_basis": "operating_revenue",
            "base_pct": None,
            "base_limit": None,
            "base_pct_reason": "Bez finansijskih pokazatelja primenjuje se minimalni limit od 5.000 EUR uz obavezne garancije.",
            "penalty_factor": None,
            "positive_factor": None,
            "combined_score": None,
            "final_pct": None,
            "model_pct_before_bucket": None,
            "final_limit": MIN_LIMIT_RSD,
            "absolute_cap_rsd": ABSOLUTE_LIMIT_CAP_RSD,
            "red_flags": rules_report.get("red_flags", []),
            "warning_flags": rules_report.get("warning_flags", []),
            "penalties_applied": [],
            "positive_factors_applied": [],
            "top_negative_drivers": ["Klijent je mlađi od 1.5 godine i nema dovoljno finansijskih pokazatelja za pouzdanu procenu."],
            "top_positive_drivers": collected_positive_drivers,
            "limit_reduction_summary": "Mlada firma bez finansijskih pokazatelja dobija minimalni limit uz obavezne garancije.",
            "growth_uplift_applied": False,
            "security_requirement": "guarantees_required",
            "security_requirement_reason": "Mlada firma bez finansijskih pokazatelja – obavezne garancije (menice ili drugi instrumenti obezbeđenja).",
            "recommendation": "minimal_limit_with_security",
            "high_risk_reasons": hard_stop.get("high_risk_reasons", []),
            "summary": "Mlada firma bez finansijskih pokazatelja – odobrava se minimalni limit od 5.000 EUR uz obavezne garancije.",
            "core_assessment": {},
            "secondary_assessment": {},
            "informative_notes": [],
        }

    if revenue_rsd is None or revenue_rsd <= 0:
        security_requirement, security_requirement_reason = _determine_security_requirement_v2(
            MIN_LIMIT_RSD, "no_revenue_data", "high", True
        )
        return {
            "engine_version": "v2",
            "year": year,
            "decision_type": "no_revenue_data",
            "founding_year": founding_year,
            "analysis_date": analysis_date.isoformat() if analysis_date else None,
            "company_type": client_size,
            "overall_risk": "high",
            "risk_score": None,
            "risk_reasons": ["Nedostaje validan poslovni prihod za standardnu procenu limita."],
            "revenue_rsd": revenue_rsd,
            "revenue_basis": "operating_revenue",
            "base_pct": None,
            "base_limit": None,
            "base_pct_reason": "Bez poslovnog prihoda nije moguće izvesti standardni procenat.",
            "penalty_factor": None,
            "positive_factor": None,
            "combined_score": None,
            "final_pct": None,
            "model_pct_before_bucket": None,
            "final_limit": MIN_LIMIT_RSD,
            "absolute_cap_rsd": ABSOLUTE_LIMIT_CAP_RSD,
            "red_flags": rules_report.get("red_flags", []),
            "warning_flags": rules_report.get("warning_flags", []),
            "penalties_applied": [],
            "positive_factors_applied": [],
            "top_negative_drivers": ["Nedostaje validan poslovni prihod za standardnu procenu limita."],
            "top_positive_drivers": collected_positive_drivers,
            "limit_reduction_summary": "Bez validnog poslovnog prihoda moguć je samo minimalan, konzervativan pristup.",
            "growth_uplift_applied": False,
            "security_requirement": security_requirement,
            "security_requirement_reason": security_requirement_reason,
            "recommendation": "minimal_limit_with_security",
            "high_risk_reasons": hard_stop.get("high_risk_reasons", []),
            "summary": "Bez validnog poslovnog prihoda moguć je samo minimalan, konzervativan pristup.",
            "core_assessment": {},
            "secondary_assessment": {},
            "informative_notes": [],
        }

    core = _build_core_assessment(
        results=results,
        revenue_rsd=revenue_rsd,
        current_ratio=current_ratio,
        quick_ratio=quick_ratio,
        dso=dso,
        dpo=dpo,
        ccc=ccc,
        single_year_revenue=single_year_revenue,
    )
    secondary = _build_secondary_assessment(
        results=results,
        client_size=client_size,
        net_working_capital=net_working_capital,
        current_financial_liabilities=current_financial_liabilities,
        debt_to_assets_ratio=debt_to_assets_ratio,
        operating_result_signed=operating_result_signed,
    )
    informative_notes = _build_informative_context(client_size, n_emp)

    core_grade = _core_grade_from_score(core["score"])
    baseline_pct = _baseline_pct_from_grade(core_grade)
    adjusted_pct = baseline_pct

    if core["anomaly"] == "strong_revenue_jump":
        adjusted_pct -= 0.5
    elif core["anomaly"] == "strong_revenue_drop":
        adjusted_pct -= 0.75
    elif core["anomaly"] == "single_year_revenue_profile":
        adjusted_pct -= 1.0
    if secondary["score"] <= -3:
        adjusted_pct -= 0.5
    elif secondary["score"] <= -1:
        adjusted_pct -= 0.25
    elif secondary["score"] >= 2:
        adjusted_pct += 0.25

    if is_younger_than_18_months:
        adjusted_pct -= 0.25

    # Mikro preduzeća su konzervativnija – maksimum 2.5%
    if client_size == "micro":
        adjusted_pct = min(adjusted_pct, 2.5)

    # Mikro firma sa opadajućom likvidnošću i bez imovinske osnove – konzervativni cap na 1%
    # Pad likvidnosti >= -65% + fiksna imovina < 8% prihoda = nema kolaterala niti trenda ka stabilnosti
    if (
        client_size == "micro"
        and _status(results.get("liquidity_trend")) == "fail"
        and _status(results.get("fixed_assets_to_revenue")) == "fail"
    ):
        adjusted_pct = min(adjusted_pct, 1.0)

    adjusted_pct = max(0.0, min(3.0, adjusted_pct))

    core_problem_count = len(core["failures"])
    secondary_problem_count = len(secondary["failures"])

    overall_risk = "low"
    if core_grade == "weak" or core_problem_count >= 2:
        overall_risk = "high"
    elif core_grade == "mixed" or core["warnings"]:
        overall_risk = "medium"

    decision_type = "standard_pct_by_priority_v2"
    recommendation = "approve_with_policy_controls"

    weak_asset_base = _status(results.get("fixed_assets_to_revenue")) in {"fail", "warn"}
    weak_receivables = _status(results.get("receivables_to_payables_ratio")) == "fail"
    weak_liquidity = _status(results.get("liquidity_trend")) == "fail" or _status(results.get("current_ratio")) == "fail"

    if single_year_revenue and (weak_receivables or weak_liquidity) and weak_asset_base:
        decision_type = "advance_only_single_year_revenue_profile"
        recommendation = "advance_only"
        adjusted_pct = 0.0
    elif single_year_revenue and core_problem_count >= 2:
        decision_type = "advance_only_single_year_revenue_profile"
        recommendation = "advance_only"
        adjusted_pct = 0.0
    elif core_grade == "weak" and client_size == "micro":
        decision_type = "weak_micro_client"
        recommendation = "minimal_limit_with_security"
    elif core_problem_count >= 2 and secondary_problem_count >= 1:
        decision_type = "manual_review"
        recommendation = "manual_review"
    elif core["anomaly"] == "strong_revenue_jump" and adjusted_pct >= 2.0:
        overall_risk = "medium"

    # Jaka kapitalna osnova: kapital >= 60% aktive + dobra profitabilnost + rastući kapital
    # Za odlično kapitalizovane firme garantujemo minimum koji snaps na 3%
    # (primenjuje se tek ovde – posle overall_risk i decision_type, da ne utiče na avans/manual slučajeve)
    if decision_type == "standard_pct_by_priority_v2":
        _capital_to_assets_val = results.get("capital_to_assets", {}).get("value")
        _is_strongly_capitalized = (
            _capital_to_assets_val is not None
            and _capital_to_assets_val >= 0.60
            and _status(results.get("operating_result_to_revenue")) == "pass"
            and _status(results.get("capital_trend")) == "pass"
            # Neto obrtna sredstva moraju biti pozitivna – negativan NWC blokira override
            and _status(results.get("net_working_capital")) != "fail"
            and core.get("anomaly") not in {"strong_revenue_drop", "single_year_revenue_profile"}
            and overall_risk not in {"critical", "high"}
            and client_size != "micro"
        )
        if _is_strongly_capitalized:
            adjusted_pct = max(adjusted_pct, 2.25)  # 2.25 → snap_pct = 2.5

    final_pct = None if decision_type == "manual_review" and recommendation != "approve_with_policy_controls" else snap_pct_to_policy_bucket(adjusted_pct) if adjusted_pct > 0 else 0.0
    final_limit = None

    if decision_type == "weak_micro_client":
        final_pct = snap_pct_to_policy_bucket(1.0)
        final_limit = max(MIN_LIMIT_RSD, int(min(revenue_rsd * final_pct / 100.0, ABSOLUTE_LIMIT_CAP_RSD)))
    elif decision_type == "manual_review":
        final_limit = None
    elif recommendation == "advance_only":
        final_limit = 0
    else:
        final_limit = int(min(revenue_rsd * final_pct / 100.0, ABSOLUTE_LIMIT_CAP_RSD))

    weak_collateral_base = weak_asset_base
    security_requirement, security_requirement_reason = _determine_security_requirement_v2(
        final_limit,
        decision_type,
        overall_risk,
        weak_collateral_base,
    )

    top_negative_drivers = []
    for text in core["failures"] + core["warnings"] + secondary["failures"] + secondary["warnings"]:
        _append_reason(top_negative_drivers, text)

    top_positive_drivers = []
    for text in core["positives"] + secondary["positives"] + collected_positive_drivers:
        _append_reason(top_positive_drivers, text)

    base_limit = int(revenue_rsd * baseline_pct / 100.0)

    return {
        "engine_version": "v2",
        "year": year,
        "decision_type": decision_type,
        "founding_year": founding_year,
        "analysis_date": analysis_date.isoformat() if analysis_date else None,
        "company_type": client_size,
        "overall_risk": overall_risk,
        "risk_score": None,
        "risk_reasons": top_negative_drivers[:5],
        "revenue_rsd": revenue_rsd,
        "revenue_basis": "operating_revenue",
        "base_pct": baseline_pct,
        "base_limit": base_limit,
        "base_pct_reason": "Bazni procenat u v2 polazi od kvaliteta ključnih poslovnih signala, dok veličina firme ostaje samo korektor.",
        "penalty_factor": None,
        "positive_factor": None,
        "combined_score": None,
        "final_pct": final_pct,
        "model_pct_before_bucket": round(adjusted_pct, 4),
        "final_limit": final_limit,
        "absolute_cap_rsd": ABSOLUTE_LIMIT_CAP_RSD,
        "red_flags": rules_report.get("red_flags", []),
        "warning_flags": rules_report.get("warning_flags", []),
        "penalties_applied": [],
        "positive_factors_applied": [],
        "top_negative_drivers": top_negative_drivers[:5],
        "top_positive_drivers": top_positive_drivers[:4],
        "limit_reduction_summary": "V2 zaključuje po slojevima core / secondary / informative, bez oslanjanja na stari size-risk matriks kao glavni izvor procenta.",
        "growth_uplift_applied": False,
        "security_requirement": security_requirement,
        "security_requirement_reason": security_requirement_reason,
        "recommendation": recommendation,
        "high_risk_reasons": hard_stop.get("high_risk_reasons", []),
        "summary": "Core signali nose odluku; secondary signali koriguju procenat, a informative podaci služe za dodatni kontekst.",
        "core_assessment": core,
        "secondary_assessment": secondary,
        "informative_notes": informative_notes,
    }
