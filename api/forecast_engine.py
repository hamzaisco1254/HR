"""
Forecast engine — honest, methodologically transparent projections.

Three methods, picked automatically based on how much data exists:
    - seasonal_naive : same month last year (needs >=12 months history)
    - linear_trend   : least-squares regression (needs >=4 months)
    - moving_average : last-N-months mean (always works, weakest signal)

Each forecast returns a band (low / expected / high) reflecting historical
volatility. Confidence is reported back so the UI can disclose it.

No black-box ML, no opaque "predict next quarter" magic. The user sees
exactly which method was used and how reliable it is.
"""
import math
import statistics
from datetime import date, datetime
from typing import Dict, List, Optional

import db
from planned_expense_store import PlannedExpenseStore


_MIN_HIST_LINEAR   = 4    # min months for linear trend to be meaningful
_MIN_HIST_SEASONAL = 12   # min months for seasonal naive
_BAND_FALLBACK_PCT = 0.20 # ±20% band when stdev unavailable


# ─── Historical data pulls ───────────────────────────────────────────

def _revenue_monthly_history(months_back: int = 24) -> List[Dict]:
    """Return a list of {year, month, amount_tnd} for the last N months."""
    today = datetime.utcnow().date().replace(day=1)
    # Year/month boundary computed in Python to avoid SQL dialect drift
    start = _add_months(today, -months_back)
    rows = db.query(
        """SELECT EXTRACT(YEAR FROM period_month)::int AS y,
                  EXTRACT(MONTH FROM period_month)::int AS m,
                  SUM(amount_tnd) AS total
             FROM customer_invoices
            WHERE period_month >= %s
            GROUP BY y, m
            ORDER BY y, m""",
        (start,),
    )
    return [{'year': int(r['y']), 'month': int(r['m']),
             'amount_tnd': float(r.get('total') or 0)} for r in rows]


def _expense_monthly_history(fx_rates, months_back: int = 24) -> List[Dict]:
    today = datetime.utcnow().date().replace(day=1)
    start = _add_months(today, -months_back)
    rows = db.query(
        """SELECT EXTRACT(YEAR FROM invoice_date)::int AS y,
                  EXTRACT(MONTH FROM invoice_date)::int AS m,
                  amount, currency
             FROM invoices
            WHERE invoice_date IS NOT NULL AND invoice_date >= %s""",
        (start,),
    )
    aggregated: Dict[tuple, float] = {}
    for r in rows:
        key = (int(r['y']), int(r['m']))
        amt = float(r.get('amount') or 0)
        cur = r.get('currency') or 'TND'
        tnd = fx_rates.to_tnd(amt, cur) if fx_rates and cur != 'TND' else amt
        aggregated[key] = aggregated.get(key, 0.0) + tnd

    out = []
    for (y, m), v in sorted(aggregated.items()):
        out.append({'year': y, 'month': m, 'amount_tnd': v})
    return out


# ─── Forecast helpers ────────────────────────────────────────────────

def _add_months(d: date, n: int) -> date:
    """Add n months to a date (n may be negative). Clamps day if needed."""
    import calendar
    month_total = d.month - 1 + n
    year = d.year + month_total // 12
    month = month_total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _linear_trend(series: List[float], months_ahead: int) -> List[float]:
    """Simple least-squares: y = a + bx, project months_ahead values."""
    n = len(series)
    if n < 2:
        return [series[-1] if series else 0.0] * months_ahead
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(series) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series))
    den = sum((x - mean_x) ** 2 for x in xs)
    b = (num / den) if den > 0 else 0
    a = mean_y - b * mean_x
    # Don't let trend go negative
    return [max(0.0, a + b * (n + k)) for k in range(months_ahead)]


def _seasonal_naive(series: List[Dict], months_ahead: int) -> List[float]:
    """Same month last year × (1 + recent growth rate)."""
    if len(series) < 12:
        return _linear_trend([s['amount_tnd'] for s in series], months_ahead)
    # Compute YoY growth from the last 12 vs prior 12 (if available)
    last_12 = sum(s['amount_tnd'] for s in series[-12:])
    prev_12 = sum(s['amount_tnd'] for s in series[-24:-12]) if len(series) >= 24 else last_12
    growth = (last_12 / prev_12) if prev_12 > 0 else 1.0
    growth = max(0.5, min(2.0, growth))  # clamp to sane range

    # Build a lookup of {month -> last-year value}
    by_month: Dict[int, float] = {}
    for s in series[-12:]:
        by_month[s['month']] = s['amount_tnd']

    # Project forward from the most recent month
    last = series[-1]
    forecast: List[float] = []
    cur = date(last['year'], last['month'], 1)
    for _ in range(months_ahead):
        cur = _add_months(cur, 1)
        base = by_month.get(cur.month, sum(by_month.values()) / 12.0)
        forecast.append(max(0.0, base * growth))
    return forecast


def _moving_average(series: List[float], window: int, months_ahead: int) -> List[float]:
    """Repeat the last-window mean for each future month."""
    if not series:
        return [0.0] * months_ahead
    eff = series[-window:] if len(series) >= window else series
    avg = sum(eff) / len(eff)
    return [avg] * months_ahead


def _band(values: List[float], history: List[float]) -> List[Dict]:
    """Wrap each forecast value in a {low, expected, high} band based on
    historical volatility (stdev). Falls back to ±20% if stdev unavailable."""
    if len(history) >= 3:
        try:
            stdev = statistics.stdev(history)
            mean  = statistics.mean(history)
            cv    = (stdev / mean) if mean > 0 else _BAND_FALLBACK_PCT
            band_width = min(cv, 0.5)  # cap at ±50%
        except Exception:
            band_width = _BAND_FALLBACK_PCT
    else:
        band_width = _BAND_FALLBACK_PCT

    return [{
        'low':      max(0.0, v * (1 - band_width)),
        'expected': v,
        'high':     v * (1 + band_width),
    } for v in values]


def _pick_method(series_length: int) -> str:
    if series_length >= _MIN_HIST_SEASONAL:
        return 'seasonal_naive'
    if series_length >= _MIN_HIST_LINEAR:
        return 'linear_trend'
    return 'moving_average'


def _confidence_label(method: str, series_length: int) -> str:
    """Plain-French confidence statement for the UI."""
    if method == 'seasonal_naive':
        return f"Fiable : {series_length} mois d'historique permettent une saisonnalite."
    if method == 'linear_trend':
        return f"Moyenne : {series_length} mois d'historique. Tendance lineaire sans saisonnalite."
    return f"Faible : seulement {series_length} mois d'historique. Projection prudente (moyenne mobile)."


# ─── Public API ──────────────────────────────────────────────────────

def compute_revenue_forecast(fx_rates, months_ahead: int = 6,
                             method: Optional[str] = None) -> Dict:
    series = _revenue_monthly_history(months_back=24)
    amounts = [s['amount_tnd'] for s in series]
    chosen = method or _pick_method(len(series))

    if chosen == 'seasonal_naive':
        forecast = _seasonal_naive(series, months_ahead)
    elif chosen == 'linear_trend':
        forecast = _linear_trend(amounts, months_ahead)
    else:
        chosen = 'moving_average'
        forecast = _moving_average(amounts, window=6, months_ahead=months_ahead)

    band_data = _band(forecast, amounts)
    today = datetime.utcnow().date().replace(day=1)
    months_out = []
    for i, b in enumerate(band_data):
        d = _add_months(today, i + 1)
        months_out.append({
            'year':     d.year,
            'month':    d.month,
            'low':      b['low'],
            'expected': b['expected'],
            'high':     b['high'],
        })

    return {
        'method':       chosen,
        'confidence':   _confidence_label(chosen, len(series)),
        'history':      series,
        'forecast':     months_out,
        'months_ahead': months_ahead,
    }


def compute_expense_forecast(fx_rates, months_ahead: int = 6,
                             method: Optional[str] = None) -> Dict:
    series = _expense_monthly_history(fx_rates, months_back=24)
    amounts = [s['amount_tnd'] for s in series]
    chosen = method or _pick_method(len(series))

    if chosen == 'seasonal_naive':
        forecast = _seasonal_naive(series, months_ahead)
    elif chosen == 'linear_trend':
        forecast = _linear_trend(amounts, months_ahead)
    else:
        chosen = 'moving_average'
        forecast = _moving_average(amounts, window=6, months_ahead=months_ahead)

    # Add planned-expense commitments for those months (known with certainty)
    pes = PlannedExpenseStore(fx_rates=fx_rates)
    today = datetime.utcnow().date().replace(day=1)
    band_data = _band(forecast, amounts)
    months_out = []
    for i, b in enumerate(band_data):
        d = _add_months(today, i + 1)
        # Add planned for this single month
        last_day = _add_months(d, 1).replace(day=1)
        from datetime import timedelta
        end_of_month = last_day - timedelta(days=1)
        planned_extra = pes.total_for_period(d, end_of_month)
        months_out.append({
            'year':           d.year,
            'month':          d.month,
            'low':            b['low'] + planned_extra,
            'expected':       b['expected'] + planned_extra,
            'high':           b['high'] + planned_extra,
            'planned_known':  planned_extra,
        })

    notes = []
    if any(m['planned_known'] > 0 for m in months_out):
        notes.append("Les depenses prevues (Gantt) sont ajoutees comme engagements certains au-dessus de la projection statistique.")

    return {
        'method':       chosen,
        'confidence':   _confidence_label(chosen, len(series)),
        'history':      series,
        'forecast':     months_out,
        'months_ahead': months_ahead,
        'notes':        notes,
    }


def compute_cashflow_forecast(fx_rates, months_ahead: int = 6) -> Dict:
    """Combine revenue + expense forecasts into a net cash flow projection."""
    rev = compute_revenue_forecast(fx_rates, months_ahead)
    exp = compute_expense_forecast(fx_rates, months_ahead)

    combined = []
    for r, e in zip(rev['forecast'], exp['forecast']):
        combined.append({
            'year':           r['year'],
            'month':          r['month'],
            'revenue_low':    r['low'],
            'revenue_exp':    r['expected'],
            'revenue_high':   r['high'],
            'expense_low':    e['low'],
            'expense_exp':    e['expected'],
            'expense_high':   e['high'],
            'net_low':        r['low']  - e['high'],   # worst case
            'net_expected':   r['expected'] - e['expected'],
            'net_high':       r['high'] - e['low'],    # best case
        })

    cumulative_low      = 0.0
    cumulative_expected = 0.0
    cumulative_high     = 0.0
    for m in combined:
        cumulative_low      += m['net_low']
        cumulative_expected += m['net_expected']
        cumulative_high     += m['net_high']
        m['cumulative_low']      = cumulative_low
        m['cumulative_expected'] = cumulative_expected
        m['cumulative_high']     = cumulative_high

    return {
        'months_ahead':  months_ahead,
        'method_used': {
            'revenue': rev['method'],
            'expense': exp['method'],
        },
        'confidence': {
            'revenue': rev['confidence'],
            'expense': exp['confidence'],
        },
        'forecast': combined,
    }


# ─── Risk detection ─────────────────────────────────────────────────

def compute_risk_alerts(fx_rates) -> List[Dict]:
    """Heuristic risk signals across the data we have.

    Each alert: {severity (high|med|low), kind, title, detail, value?}
    """
    alerts: List[Dict] = []
    today = datetime.utcnow().date()

    # 1) Overdue customer invoices
    overdue = db.query(
        """SELECT ci.*, c.name AS client_name
             FROM customer_invoices ci
             LEFT JOIN clients c ON c.id = ci.client_id
            WHERE ci.status IN ('sent', 'draft', 'overdue')
              AND ci.due_date IS NOT NULL
              AND ci.due_date < %s
            ORDER BY ci.due_date""",
        (today,),
    )
    total_overdue_tnd = sum(float(r.get('amount_tnd') or 0) for r in overdue)
    if overdue:
        very_old = [r for r in overdue if r['due_date'] and (today - r['due_date']).days > 60]
        sev = 'high' if very_old else ('med' if total_overdue_tnd > 5000 else 'low')
        alerts.append({
            'severity': sev,
            'kind':     'overdue_receivable',
            'title':    f"{len(overdue)} facture{'s' if len(overdue) > 1 else ''} client en retard",
            'detail':   f"Total {total_overdue_tnd:,.0f} TND a recouvrer. ".replace(',', ' ') +
                        (f"{len(very_old)} > 60 jours." if very_old else ''),
            'value':    total_overdue_tnd,
        })

    # 2) Expense spike: this month vs 6-month average
    series = _expense_monthly_history(fx_rates, months_back=12)
    if len(series) >= 4:
        last = series[-1]['amount_tnd']
        prior_avg = sum(s['amount_tnd'] for s in series[-7:-1]) / 6 if len(series) >= 7 else \
                    sum(s['amount_tnd'] for s in series[:-1]) / max(1, len(series) - 1)
        if prior_avg > 0 and last / prior_avg >= 1.5:
            alerts.append({
                'severity': 'high' if last / prior_avg >= 2 else 'med',
                'kind':     'expense_spike',
                'title':    f"Depenses du mois +{((last/prior_avg - 1) * 100):.0f}% vs moyenne",
                'detail':   f"{last:,.0f} TND vs moyenne 6 mois {prior_avg:,.0f} TND.".replace(',', ' '),
                'value':    last - prior_avg,
            })

    # 3) Negative cash flow projection over 3 months
    cf = compute_cashflow_forecast(fx_rates, months_ahead=3)
    final_cum = cf['forecast'][-1]['cumulative_expected'] if cf['forecast'] else 0
    if final_cum < 0:
        alerts.append({
            'severity': 'high' if final_cum < -10000 else 'med',
            'kind':     'negative_cashflow_forecast',
            'title':    'Cash flow projete negatif sur 3 mois',
            'detail':   f"Solde cumule projete : {final_cum:,.0f} TND.".replace(',', ' '),
            'value':    final_cum,
        })

    # 4) Highly concentrated revenue (single client > 70%)
    rev_by_client = db.query(
        """SELECT c.name, SUM(ci.amount_tnd) AS total
             FROM customer_invoices ci
             LEFT JOIN clients c ON c.id = ci.client_id
            WHERE EXTRACT(YEAR FROM ci.period_month) = %s
            GROUP BY c.name
            ORDER BY total DESC NULLS LAST""",
        (today.year,),
    )
    if rev_by_client:
        total_rev = sum(float(r.get('total') or 0) for r in rev_by_client)
        top = rev_by_client[0]
        top_val = float(top.get('total') or 0)
        if total_rev > 0 and top_val / total_rev > 0.7:
            alerts.append({
                'severity': 'med',
                'kind':     'client_concentration',
                'title':    f"Concentration client elevee",
                'detail':   f"{top.get('name') or 'Client inconnu'} represente {100*top_val/total_rev:.0f}% du CA.",
                'value':    top_val / total_rev * 100,
            })

    # 5) Departments with declining profitability (revenue trend down)
    # (Simplified: compare last 3 months avg to prior 3 months avg by dept)
    rows = db.query(
        """SELECT d.name AS dept,
                  EXTRACT(YEAR FROM ci.period_month)::int AS y,
                  EXTRACT(MONTH FROM ci.period_month)::int AS m,
                  SUM(ci.amount_tnd) AS total
             FROM customer_invoices ci
             LEFT JOIN departments d ON d.id = ci.department_id
            WHERE d.name IS NOT NULL
              AND ci.period_month >= %s
            GROUP BY d.name, y, m""",
        (_add_months(today.replace(day=1), -6),),
    )
    dept_series: Dict[str, List[float]] = {}
    for r in rows:
        dept_series.setdefault(r['dept'], []).append(float(r.get('total') or 0))
    for dept, vals in dept_series.items():
        if len(vals) >= 6:
            last3 = sum(vals[-3:]) / 3
            prev3 = sum(vals[-6:-3]) / 3
            if prev3 > 0 and last3 / prev3 < 0.7:
                alerts.append({
                    'severity': 'low',
                    'kind':     'declining_dept',
                    'title':    f"Departement '{dept}' en baisse",
                    'detail':   f"CA des 3 derniers mois : {((last3/prev3 - 1) * 100):.0f}% vs trimestre precedent.",
                })

    # Sort by severity
    sev_order = {'high': 0, 'med': 1, 'low': 2}
    alerts.sort(key=lambda a: sev_order.get(a['severity'], 9))
    return alerts
