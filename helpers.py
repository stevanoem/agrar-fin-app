import re
import unicodedata
from datetime import datetime, date


def convert_EUR(x):
  return float(x) / 117.5

def company_type(zaposleni, poslovni_prihod_eur, aktiva_eur):
    counts = {"micro": 0, "small": 0, "medium": 0, "large": 0}
    observed_sizes = []

    def classify_metric(value, thresholds):
        if value is None:
            return None
        for limit, size in thresholds:
            if value <= limit:
                return size
        return "large"

    employee_size = classify_metric(
        zaposleni,
        [(10, "micro"), (50, "small"), (250, "medium")],
    )
    revenue_size = classify_metric(
        poslovni_prihod_eur,
        [(700000, "micro"), (8000000, "small"), (40000000, "medium")],
    )
    assets_size = classify_metric(
        aktiva_eur,
        [(350000, "micro"), (4000000, "small"), (20000000, "medium")],
    )

    for size in (employee_size, revenue_size, assets_size):
        if size is None:
            continue
        counts[size] += 1
        observed_sizes.append(size)

    if not observed_sizes:
        return "small"

    required_majority = 2 if len(observed_sizes) >= 2 else 1
    for size in ("micro", "small", "medium", "large"):
        if counts[size] >= required_majority:
            return size

    # Ako nema većine jer nedostaje jedan kriterijum, zadrži konzervativniji rezultat.
    ranking = {"micro": 0, "small": 1, "medium": 2, "large": 3}
    return min(observed_sizes, key=lambda size: ranking[size])

def get_operating_revenue(client_json, year):
    year = str(year)
    segment = client_json.get("finansije", {}).get("bilans_uspeha", {})
    if not isinstance(segment, list) or not segment:
        return None
    value = segment[0].get(year)
    parsed = _to_float(value)
    if parsed is None:
        return None
    return normalize_to_thousands(parsed)
def get_assets(client_json, year):
    year = str(year)
    value = client_json.get("poslovanje_po_godinama_osnovno", {}).get(year, {}).get("ukupna_aktiva")
    parsed = _to_float(value)
    if parsed is None:
        return None
    return normalize_to_thousands(parsed)

def get_n_emp(client_json, year):
    year = str(year)
    value = client_json.get("poslovanje_po_godinama_osnovno", {}).get(year, {}).get("broj_zaposlenih")
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text)
    return text


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace("%", "").replace("\xa0", "").replace(" ", "")
    if not text:
        return None

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    elif re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", text):
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def _normalize_ratio_value(value):
    if value is None:
        return None
    value = float(value)
    if value > 1 and value <= 100:
        return value / 100.0
    return value


def _iter_finansije_rows(client_json):
    finansije = client_json.get("finansije", {})
    for section_rows in finansije.values():
        if isinstance(section_rows, list):
            for row in section_rows:
                if isinstance(row, dict) and row.get("naziv"):
                    yield row


def _get_metric_from_finansije(client_json, year, metric_aliases):
    year = str(year)
    aliases = [_normalize_text(alias) for alias in metric_aliases]

    for row in _iter_finansije_rows(client_json):
        naziv_normalized = _normalize_text(row.get("naziv"))
        if any(alias in naziv_normalized for alias in aliases):
            return _to_float(row.get(year))
    return None


def get_metric_value(client_json, metric, year):
    aliases = list(metric) if isinstance(metric, (list, tuple, set)) else [metric]
    value = _get_metric_from_finansije(client_json, year, aliases)
    if value is not None:
        return value

    year_data = client_json.get("poslovanje_po_godinama_osnovno", {}).get(str(year), {})
    if not isinstance(year_data, dict):
        return None

    normalized_aliases = {_normalize_text(alias).replace(" ", "") for alias in aliases}
    for key, raw_value in year_data.items():
        normalized_key = _normalize_text(key).replace(" ", "")
        if normalized_key in normalized_aliases:
            return _to_float(raw_value)
    return None

def normalize_to_thousands(value):
    """Konvertuje iznos u hiljade (000 RSD) sa zaokruživanjem."""
    if value is None:
        return 0
    return round(value * 1000, 2)


def _convert_sheet8_ebitda_to_rsd(value_thousand_eur, exchange_rate=None):
    if value_thousand_eur is None:
        return None
    fx = _to_float(exchange_rate)
    if fx in (None, 0):
        fx = 117.0
    return round(float(value_thousand_eur) * 1000 * fx, 2)

def _safe_div(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _get_current_assets(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Obrtna imovina",
            "G. OBRTNA IMOVINA",
        ],
        year,
    )
    return normalize_to_thousands(val)


def _get_inventory(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Zalihe",
            "I. ZALIHE",
        ],
        year,
    )
    return normalize_to_thousands(val)


def _get_current_liabilities(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Kratkorocne obaveze",
            "D. KRATKOROCNA REZERVISANJA I KRATKOROCNE OBAVEZE",
        ],
        year,
    )
    return normalize_to_thousands(val)


def _get_cogs(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Nabavna vrednost prodate robe",
            "Troskovi materijala",
        ],
        year,
    )
    return normalize_to_thousands(val)


def _get_receivables(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Potrazivanja po osnovu prodaje",
            "III. POTRAZIVANJA PO OSNOVU PRODAJE",
        ],
        year,
    )
    return normalize_to_thousands(val)


def _get_payables(client_json, year):
    val =  get_metric_value(
        client_json,
        [
            "Obaveze iz poslovanja",
            "IV. OBAVEZE IZ POSLOVANJA",
            "Obaveze prema dobavljacima",
        ],
        year,
    )
    return normalize_to_thousands(val)

def get_founding_date(client_json):
    """
    Vraća datum osnivanja kao date objekat ili None.
    """
    try:
        raw = client_json.get("osnivanje_firme", {}).get("datum_osnivanja")
        if not raw:
            return None
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def parse_analysis_date_from_filename(filename):
    """
    Pokušava da izvuče datum analize iz originalnog naziva fajla.
    Prioritet imaju obrasci tipa DD_MM_YYYY ili DD.MM.YYYY, a zatim YYYY-MM-DD.
    Ako postoji više poklapanja, uzima poslednje jer je u praksi datum analize
    najčešće pri kraju naziva fajla.
    """
    if not filename:
        return None

    text = str(filename).strip()

    day_first_matches = re.findall(r"(\d{2})[._-](\d{2})[._-](\d{4})", text)
    if day_first_matches:
        day, month, year = day_first_matches[-1]
        try:
            return datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d").date()
        except ValueError:
            pass

    iso_matches = re.findall(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso_matches:
        year, month, day = iso_matches[-1]
        try:
            return datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d").date()
        except ValueError:
            pass

    return None

def get_ebitda(client_json, year):
    year = str(year)
    val = _get_metric_from_finansije(client_json, year, ["EBITDA"])
    if val is None:
        ebitda_payload = client_json.get("ebitda", {}) or {}
        val = ebitda_payload.get("by_year", {}).get(year)
        if val is None and ebitda_payload.get("latest_year") == year:
            val = ebitda_payload.get("latest_value")
        val = _to_float(val)
        if val is None:
            return None
        return _convert_sheet8_ebitda_to_rsd(val, ebitda_payload.get("exchange_rate"))
    if val is None:
        return None
    return normalize_to_thousands(val)


def get_ebitda_margin(client_json, year):
    value = _get_metric_from_finansije(client_json, year, ["EBITDA marza", "EBITDA marža"])
    if value is not None:
        return _normalize_ratio_value(value)

    ebitda = get_ebitda(client_json, year)
    operating_revenue = _to_float(get_operating_revenue(client_json, str(year)))
    print(f"ebida {ebitda}")
    print(f"poslovni prihod {operating_revenue}")
    if ebitda is None or operating_revenue in (None, 0):
        return None
    print(f"e marza {ebitda / operating_revenue}")
    return ebitda / operating_revenue


def get_gross_profit_margin(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        ["Bruto marza na prodaju", "Bruto marža na prodaju", "Bruto marza"],
    )
    if value is not None:
        return value

    operating_revenue = _to_float(get_operating_revenue(client_json, str(year)))
    cogs = _get_cogs(client_json, year)
    print(operating_revenue)
    print(cogs)
    if operating_revenue in (None, 0) or cogs is None:
        return None
    return (operating_revenue - cogs) / operating_revenue


def get_return_on_equity(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        [
            "Neto prinos na kapital",
            "Neto prinos an kapital",
            "Stopa prinosa na sopstvena poslovna sredstva",
        ],
    )
    if value is not None:
        return value

    osnovno = client_json.get("poslovanje_po_godinama_osnovno", {}).get(str(year), {})
    neto_rezultat = _to_float(osnovno.get("neto_rezultat"))
    kapital = _to_float(osnovno.get("kapital"))
    if neto_rezultat is None or kapital in (None, 0):
        return None
    return neto_rezultat / kapital


def get_current_ratio(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        ["Opsti racio likvidnosti (Acid test)"],
    )
    if value is not None:
        return value

    current_assets = _get_current_assets(client_json, year)
    current_liabilities = _get_current_liabilities(client_json, year)
    return _safe_div(current_assets, current_liabilities)


def get_quick_ratio(client_json, year):
    value = _get_metric_from_finansije(client_json, year, ["Rigorozni racio likvidnosti"])
    if value is not None:
        return value

    current_assets = _get_current_assets(client_json, year)
    inventory = _get_inventory(client_json, year)
    current_liabilities = _get_current_liabilities(client_json, year)
    if current_assets is None or inventory is None:
        return None
    return _safe_div(current_assets - inventory, current_liabilities)


def get_net_working_capital(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        ["Neto obrtni fond", "Neto obrtna sredstva"],
    )
    if value is not None:
        return normalize_to_thousands(value)

    current_assets = _get_current_assets(client_json, year)
    current_liabilities = _get_current_liabilities(client_json, year)
    if current_assets is None or current_liabilities is None:
        return None
    return normalize_to_thousands(current_assets - current_liabilities)

def get_net_profit(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        ["NETO DOBITAK"],
    )
    return normalize_to_thousands(value)

def get_debt_to_assets_ratio(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        ["Koeficijent zaduzenosti", "Koeficijent zaduženosti"],
    )
    if value is not None:
        return value

    osnovno = client_json.get("poslovanje_po_godinama_osnovno", {}).get(str(year), {})
    obaveze = _to_float(osnovno.get("obaveze"))
    aktiva = _to_float(osnovno.get("ukupna_aktiva"))
    return _safe_div(obaveze, aktiva)


def get_debt_to_equity_ratio(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        ["Koeficijent zaduzenosti", "Koeficijent zaduženosti", "Odnos obaveza i kapitala"],
    )
    if value is not None:
        return value

    osnovno = client_json.get("poslovanje_po_godinama_osnovno", {}).get(str(year), {})
    obaveze = _to_float(osnovno.get("obaveze"))
    kapital = _to_float(osnovno.get("kapital"))
    if obaveze is None or kapital in (None, 0):
        return None
    return obaveze / kapital


def get_financial_stability_ratio(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        ["Koeficijent finansijske stabilnosti"],
    )
    if value is not None:
        return value

    fixed_assets = get_fixed_assets(client_json, year)
    equity = get_capital(client_json, year)
    long_term_liabilities = get_non_current_liabilities(client_json, year)
    if fixed_assets is None or equity is None or long_term_liabilities is None:
        return None
    return _safe_div(fixed_assets, equity + long_term_liabilities)


def get_total_financial_liabilities(client_json, year):
    value = _get_metric_from_finansije(
        client_json,
        year,
        [
            "Ukupne finansijske obaveze",
            "Ukupno finansijske obaveze",
        ],
    )
    if value is not None:
        return normalize_to_thousands(value)

    long_term = get_non_current_liabilities(client_json, year)
    short_term = get_current_financial_liabilities(client_json, year)
    if long_term is None and short_term is None:
        osnovno = client_json.get("poslovanje_po_godinama_osnovno", {}).get(str(year), {})
        return normalize_to_thousands(_to_float(osnovno.get("obaveze")))
    return normalize_to_thousands((long_term or 0.0) + (short_term or 0.0))


def get_dso(client_json, year):
    value = get_metric_value(
        client_json,
        [
            "Dani vezivanja potrazivanja od kupaca",
            "Dani vezivanja potrošača od kupaca",
            "Prosecan broj dana naplate potrazivanja",
            "Period naplate potrazivanja",
        ],
        year,
    )
    if value is not None:
        return value

    receivables = _get_receivables(client_json, year)
    operating_revenue = _to_float(get_operating_revenue(client_json, str(year)))
    ratio = _safe_div(receivables, operating_revenue)
    if ratio is None:
        return None
    return ratio * 365


def get_dio(client_json, year):
    value = get_metric_value(
        client_json,
        [
            "Dani vezivanja zaliha",
            "Prosecan broj dana trajanja jednog obrta zaliha",
            "Period prodaje zaliha",
        ],
        year,
    )
    if value is not None:
        return value

    inventory = _get_inventory(client_json, year)
    cogs = _get_cogs(client_json, year)
    ratio = _safe_div(inventory, cogs)
    if ratio is None:
        return None
    return ratio * 365


def get_dpo(client_json, year):
    value = get_metric_value(
        client_json,
        [
            "Dani vezivanja obaveza prema dobavljacima",
            "Prosecno vreme placanja obaveza prema dobavljacima",
        ],
        year,
    )
    if value is not None:
        return value

    payables = _get_payables(client_json, year)
    cogs = _get_cogs(client_json, year)
    ratio = _safe_div(payables, cogs)
    if ratio is None:
        return None
    return ratio * 365

def get_cash_conversion_cycle(client_json, year):
    dso = get_dso(client_json, year)
    dio = get_dio(client_json, year)
    dpo = get_dpo(client_json, year)

    if dso is None or dio is None or dpo is None:
        return None

    return dso + dio - dpo

def get_fixed_assets(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Stalna imovina",
            "B. STALNA IMOVINA",
        ],
        year,
    )
    return normalize_to_thousands(val)

def get_capital(client_json, year):
    value = get_metric_value(
        client_json,
        [
            "A. KAPITAL",
        ],
        year,
    )
    if value is not None:
        return normalize_to_thousands(value)

    osnovno = client_json.get("poslovanje_po_godinama_osnovno", {}).get(str(year), {})
    return normalize_to_thousands(_to_float(osnovno.get("kapital")))


def get_loss_in_excess_of_equity(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Gubitak iznad visine kapitala",
            "Dj. GUBITAK IZNAD VISINE KAPITALA",
        ],
        year,
    )
    return normalize_to_thousands(val)


def get_receivables_to_payables_ratio(client_json, year):
    value = get_metric_value(
        client_json,
        [
            "Odnos potrazivanja i obaveza iz poslovanja",
            "Odnos potraživanja i obaveza iz poslovanja",
        ],
        year,
    )
    if value is not None:
        return value

    receivables = get_metric_value(
        client_json,
        [
            "III. POTRAZIVANJA PO OSNOVU PRODAJE",
            "Potrazivanja po osnovu prodaje",
        ],
        year,
    )
    payables = get_metric_value(
        client_json,
        [
            "IV. OBAVEZE IZ POSLOVANJA",
            "Obaveze iz poslovanja",
            "Obaveze prema dobavljacima",
        ],
        year,
    )
    if receivables is None or payables in (None, 0):
        return None
    return receivables / payables


def get_non_current_liabilities(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Dugorocne obaveze",
            "Dugoročne obaveze",
            "II. DUGOROCNE OBAVEZE",
        ],
        year,
    )
    return normalize_to_thousands(val)


def get_current_financial_liabilities(client_json, year):
    val = get_metric_value(
        client_json,
        [
            "Kratkorocne finansijske obaveze",
            "Kratkoročne finansijske obaveze",
            "II. KRATKOROCNE FINANSIJSKE OBAVEZE",
        ],
        year,
    )
    return normalize_to_thousands(val)


def get_off_balance_assets_liabilities(client_json, year):
    assets = get_metric_value(
        client_json,
        [
            "Vanbilansna aktiva",
            "Dj. VANBILANSNA AKTIVA",
        ],
        year,
    )
    liabilities = get_metric_value(
        client_json,
        [
            "Vanbilansna pasiva",
            "Z. VANBILANSNA PASIVA",
        ],
        year,
    )
    return {"assets": normalize_to_thousands(assets), "liabilities": normalize_to_thousands(liabilities)}


def get_operating_income_expenses(client_json, year):
    income = get_metric_value(
        client_json,
        [
            "Visina poslovnih prihoda/rashoda",
            "Poslovni prihodi",
            "A. POSLOVNI PRIHODI",
        ],
        year,
    )
    expenses = get_metric_value(
        client_json,
        [
            "Visina poslovnih prihoda/rashoda",
            "Poslovni rashodi",
            "B. POSLOVNI RASHODI",
        ],
        year,
    )
    return {"income": normalize_to_thousands(income), "expenses": normalize_to_thousands(expenses)}


def get_operating_profit_loss(client_json, year):
    profit = get_metric_value(
        client_json,
        [
            "Visina poslovnog dobitka/gubitka",
            "Poslovni dobitak",
            "V. POSLOVNI DOBITAK",
        ],
        year,
    )
    loss = get_metric_value(
        client_json,
        [
            "Visina poslovnog dobitka/gubitka",
            "Poslovni gubitak",
            "G. POSLOVNI GUBITAK",
        ],
        year,
    )
    return {"profit": normalize_to_thousands(profit), "loss": normalize_to_thousands(loss)}
