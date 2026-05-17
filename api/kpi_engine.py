"""
Financial KPI Engine — computes the top 10 KPIs for company analysis.

All monetary values are normalized to TND using the ExchangeRates helper
so that KPIs are comparable across currencies.

KPI list
--------
1.  Cash Flow                 — net inflows minus outflows for the period
2.  Net Cash Position         — total liquid assets (bank + cash)
3.  Accounts Payable Turnover — total purchases / average accounts payable
4.  Days Payable Outstanding  — 365 / AP turnover
5.  Burn Rate                 — average monthly outflow
6.  Liquidity Ratio           — liquid assets / current liabilities (unpaid invoices)
7.  Operating Cash Flow Ratio — cash flow / current liabilities
8.  Expense Distribution      — % breakdown by top suppliers
9.  Supplier Concentration    — Herfindahl-Hirschman Index
10. Payment Delay Trends      — average days between invoice_date and payment_date
"""
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from financial_store import InvoiceStore, BalanceStore, PaymentStore, ExchangeRates


def compute_all_kpis(
    inv_store: InvoiceStore,
    bal_store: BalanceStore,
    pay_store: PaymentStore,
    fx: ExchangeRates,
) -> List[Dict]:
    """
    Return a list of KPI dicts:
        { id, name, value, formatted, unit, description, trend }
    """
    invoices = inv_store.get_all()
    accounts = bal_store.get_all()
    payments = pay_store.get_all()

    # Normalize everything to TND
    def to_tnd(amount: float, cur: str) -> float:
        return fx.to_tnd(amount, cur)

    # ── Pre-computations ──────────────────────────────────────────
    total_paid_tnd = sum(
        to_tnd(float(i.get('amount', 0)), i.get('currency', 'TND'))
        for i in invoices if i.get('payment_status') == 'paid'
    )
    total_unpaid_tnd = sum(
        to_tnd(float(i.get('amount', 0)), i.get('currency', 'TND'))
        for i in invoices if i.get('payment_status') in ('unpaid', 'overdue')
    )
    total_overdue_tnd = sum(
        to_tnd(float(i.get('amount', 0)), i.get('currency', 'TND'))
        for i in invoices if i.get('payment_status') == 'overdue'
    )
    total_invoiced_tnd = sum(
        to_tnd(float(i.get('amount', 0)), i.get('currency', 'TND'))
        for i in invoices
    )
    total_liquid_tnd = sum(
        to_tnd(float(a.get('balance', 0)), a.get('currency', 'TND'))
        for a in accounts
    )

    # Payment totals by month (last 12 months)
    now = datetime.now()
    monthly_outflows: Dict[str, float] = {}
    for p in payments:
        pd = p.get('payment_date', '')[:7]  # YYYY-MM
        monthly_outflows[pd] = monthly_outflows.get(pd, 0) + float(p.get('amount', 0))

    months_with_data = len(monthly_outflows) or 1

    # ── 1. Cash Flow ──────────────────────────────────────────────
    total_inflows_tnd = total_liquid_tnd  # current liquid position
    total_outflows_tnd = total_paid_tnd   # what we've paid out
    cash_flow = total_inflows_tnd - total_unpaid_tnd

    # ── 2. Net Cash Position ──────────────────────────────────────
    net_cash = total_liquid_tnd

    # ── 3. Accounts Payable Turnover ──────────────────────────────
    avg_payable = total_unpaid_tnd if total_unpaid_tnd > 0 else 1.0
    ap_turnover = total_invoiced_tnd / avg_payable if avg_payable else 0.0

    # ── 4. Days Payable Outstanding ───────────────────────────────
    dpo = 365.0 / ap_turnover if ap_turnover > 0 else 0.0

    # ── 5. Burn Rate ──────────────────────────────────────────────
    total_monthly = sum(monthly_outflows.values())
    burn_rate = total_monthly / months_with_data if months_with_data > 0 else 0.0
    # If no payment data, estimate from invoices
    if burn_rate == 0 and total_paid_tnd > 0:
        # Rough estimate: spread paid invoices over last 6 months
        burn_rate = total_paid_tnd / 6.0

    # ── 6. Liquidity Ratio ────────────────────────────────────────
    current_liabilities = total_unpaid_tnd + total_overdue_tnd
    liquidity_ratio = (total_liquid_tnd / current_liabilities
                       if current_liabilities > 0 else float('inf'))

    # ── 7. Operating Cash Flow Ratio ──────────────────────────────
    op_cf_ratio = (cash_flow / current_liabilities
                   if current_liabilities > 0 else float('inf'))

    # ── 8. Expense Distribution ───────────────────────────────────
    supplier_totals: Dict[str, float] = {}
    for inv in invoices:
        s = inv.get('supplier_name', 'Inconnu')
        supplier_totals[s] = supplier_totals.get(s, 0) + to_tnd(
            float(inv.get('amount', 0)), inv.get('currency', 'TND'))
    top_suppliers = sorted(supplier_totals.items(), key=lambda x: x[1], reverse=True)[:5]
    expense_dist = {s: round(v / total_invoiced_tnd * 100, 1) if total_invoiced_tnd > 0 else 0
                    for s, v in top_suppliers}

    # ── 9. Supplier Concentration (HHI) ───────────────────────────
    hhi = 0.0
    if total_invoiced_tnd > 0:
        for _, amt in supplier_totals.items():
            share = amt / total_invoiced_tnd
            hhi += share ** 2
    hhi = round(hhi * 10000)  # Standard HHI scale (0–10000)

    # ── 10. Payment Delay Trends ──────────────────────────────────
    delays = []
    for p in payments:
        inv = inv_store.get_by_id(p.get('invoice_id', ''))
        if inv and inv.get('invoice_date') and p.get('payment_date'):
            try:
                inv_date = datetime.fromisoformat(inv['invoice_date'])
                pay_date = datetime.fromisoformat(p['payment_date'])
                delay = (pay_date - inv_date).days
                if delay >= 0:
                    delays.append(delay)
            except (ValueError, TypeError):
                pass
    avg_delay = sum(delays) / len(delays) if delays else 0.0

    # ── Build KPI list ────────────────────────────────────────────
    def _fmt(v: float, decimals: int = 0, suffix: str = '') -> str:
        if v == float('inf'):
            return '∞'
        if abs(v) >= 1_000_000:
            return f"{v/1_000_000:,.{decimals}f}M{suffix}"
        if abs(v) >= 1_000:
            return f"{v/1_000:,.{decimals}f}K{suffix}"
        return f"{v:,.{decimals}f}{suffix}"

    def _trend(v: float) -> str:
        if v > 0:
            return 'positive'
        if v < 0:
            return 'negative'
        return 'neutral'

    kpis = [
        {
            'id': 'cash_flow',
            'name': 'Cash Flow',
            'value': round(cash_flow, 2),
            'formatted': _fmt(cash_flow, 0, ' TND'),
            'unit': 'TND',
            'description': 'Position de trésorerie nette : actifs liquides moins les engagements impayés.',
            'trend': _trend(cash_flow),
        },
        {
            'id': 'net_cash',
            'name': 'Net Cash Position',
            'value': round(net_cash, 2),
            'formatted': _fmt(net_cash, 0, ' TND'),
            'unit': 'TND',
            'description': 'Somme totale des soldes bancaires et de la caisse, convertie en TND.',
            'trend': _trend(net_cash),
        },
        {
            'id': 'ap_turnover',
            'name': 'Accounts Payable Turnover',
            'value': round(ap_turnover, 2),
            'formatted': f"{ap_turnover:.2f}x",
            'unit': 'ratio',
            'description': 'Nombre de fois que les dettes fournisseurs sont renouvelées. Plus élevé = paiement plus rapide.',
            'trend': 'positive' if ap_turnover > 4 else 'neutral',
        },
        {
            'id': 'dpo',
            'name': 'Days Payable Outstanding',
            'value': round(dpo, 1),
            'formatted': f"{dpo:.0f} jours",
            'unit': 'days',
            'description': 'Nombre moyen de jours pour payer les fournisseurs. Un DPO modéré optimise la trésorerie.',
            'trend': 'positive' if 30 <= dpo <= 60 else ('negative' if dpo > 90 else 'neutral'),
        },
        {
            'id': 'burn_rate',
            'name': 'Burn Rate',
            'value': round(burn_rate, 2),
            'formatted': _fmt(burn_rate, 0, ' TND/mois'),
            'unit': 'TND/month',
            'description': 'Dépenses mensuelles moyennes. Indicateur clé pour la planification de trésorerie.',
            'trend': 'negative' if burn_rate > net_cash * 0.3 else 'neutral',
        },
        {
            'id': 'liquidity',
            'name': 'Liquidity Ratio',
            'value': round(liquidity_ratio, 2) if liquidity_ratio != float('inf') else 999,
            'formatted': f"{liquidity_ratio:.2f}" if liquidity_ratio != float('inf') else '∞',
            'unit': 'ratio',
            'description': 'Actifs liquides / passifs courants. > 1.0 indique une bonne capacité de paiement.',
            'trend': 'positive' if liquidity_ratio > 1.5 else ('negative' if liquidity_ratio < 1.0 else 'neutral'),
        },
        {
            'id': 'op_cf_ratio',
            'name': 'Operating Cash Flow Ratio',
            'value': round(op_cf_ratio, 2) if op_cf_ratio != float('inf') else 999,
            'formatted': f"{op_cf_ratio:.2f}" if op_cf_ratio != float('inf') else '∞',
            'unit': 'ratio',
            'description': 'Flux de trésorerie / passifs courants. Mesure la capacité à couvrir les dettes à court terme.',
            'trend': 'positive' if op_cf_ratio > 1.0 else ('negative' if op_cf_ratio < 0.5 else 'neutral'),
        },
        {
            'id': 'expense_dist',
            'name': 'Expense Distribution',
            'value': expense_dist,
            'formatted': ', '.join(f"{s}: {p}%" for s, p in expense_dist.items()) or 'N/A',
            'unit': '%',
            'description': 'Répartition des dépenses par fournisseur principal (top 5).',
            'trend': 'neutral',
        },
        {
            'id': 'supplier_hhi',
            'name': 'Supplier Concentration (HHI)',
            'value': hhi,
            'formatted': f"{hhi:,}",
            'unit': 'index',
            'description': 'Indice Herfindahl-Hirschman. < 1500 = diversifié, 1500-2500 = modéré, > 2500 = concentré.',
            'trend': 'positive' if hhi < 1500 else ('negative' if hhi > 2500 else 'neutral'),
        },
        {
            'id': 'payment_delay',
            'name': 'Payment Delay Trends',
            'value': round(avg_delay, 1),
            'formatted': f"{avg_delay:.0f} jours",
            'unit': 'days',
            'description': 'Délai moyen entre la date de facture et le paiement effectif.',
            'trend': 'positive' if avg_delay < 30 else ('negative' if avg_delay > 60 else 'neutral'),
        },
    ]

    return kpis


def get_cashflow_timeseries(
    inv_store: InvoiceStore,
    pay_store: PaymentStore,
    months: int = 12,
) -> Dict:
    """
    Return monthly inflow/outflow data for charting.
    Returns: {labels: [...], inflows: [...], outflows: [...]}
    """
    now = datetime.now()
    labels = []
    inflows = []
    outflows = []

    for i in range(months - 1, -1, -1):
        d = now - timedelta(days=30 * i)
        ym = d.strftime('%Y-%m')
        label = d.strftime('%b %Y')
        labels.append(label)

        # Outflows: payments made in this month
        month_out = sum(
            float(p.get('amount', 0))
            for p in pay_store.get_all()
            if p.get('payment_date', '')[:7] == ym
        )
        outflows.append(round(month_out, 2))

        # Inflows: invoices marked as paid in this month (approximation)
        month_in = sum(
            float(inv.get('amount', 0))
            for inv in inv_store.get_all()
            if inv.get('payment_status') == 'paid'
            and inv.get('updated_at', '')[:7] == ym
        )
        inflows.append(round(month_in, 2))

    return {'labels': labels, 'inflows': inflows, 'outflows': outflows}


def get_status_distribution(inv_store: InvoiceStore) -> Dict:
    """Return paid/unpaid/overdue counts and amounts for pie chart."""
    status_counts = {'paid': 0, 'unpaid': 0, 'overdue': 0}
    status_amounts = {'paid': 0.0, 'unpaid': 0.0, 'overdue': 0.0}

    for inv in inv_store.get_all():
        s = inv.get('payment_status', 'unpaid')
        if s not in status_counts:
            s = 'unpaid'
        status_counts[s] += 1
        status_amounts[s] += float(inv.get('amount', 0))

    return {'counts': status_counts, 'amounts': status_amounts}


# ════════════════════════════════════════════════════════════════════
# EXTENDED KPI DASHBOARD (PR-11)
# Categorized, finance-module-aware KPI surface that supersedes the
# flat compute_all_kpis() output on the dedicated KPIs page.
# ════════════════════════════════════════════════════════════════════

def _fmt_tnd(v: float) -> str:
    if v == float('inf'):
        return '∞'
    if v == float('-inf'):
        return '-∞'
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:,.2f}M TND".replace(',', ' ')
    if abs(v) >= 1_000:
        return f"{v/1_000:,.1f}K TND".replace(',', ' ')
    return f"{v:,.0f} TND".replace(',', ' ')


def _fmt_pct(v: float, decimals: int = 1) -> str:
    if v is None:
        return '—'
    return f"{v:,.{decimals}f}%".replace(',', ' ')


def _trend(value, good_above=None, good_below=None, neutral_range=None):
    """Generic trend classifier. Returns 'positive'|'neutral'|'negative'."""
    if value is None or value == float('inf'):
        return 'neutral'
    if neutral_range and neutral_range[0] <= value <= neutral_range[1]:
        return 'neutral'
    if good_above is not None and value >= good_above:
        return 'positive'
    if good_below is not None and value <= good_below:
        return 'positive'
    return 'negative'


def compute_kpi_dashboard(fx_rates, inv_store, bal_store, pay_store,
                          income_store=None, planned_store=None,
                          statements_engine=None, forecast_engine=None) -> Dict:
    """Return a categorized KPI dashboard:
        {
          'health_score': 0..100,
          'categories': [
            {'id', 'name', 'icon', 'kpis': [{id, name, value, formatted,
                                            unit, description, trend,
                                            benchmark}, ...]},
            ...
          ]
        }
    Each engine/store is optional so the function degrades gracefully
    in tests or when a module is not yet bootstrapped.
    """
    from datetime import datetime, date
    today = datetime.utcnow().date()
    year  = today.year

    def _to_tnd(amount, cur):
        try: a = float(amount or 0)
        except (TypeError, ValueError): return 0.0
        return fx_rates.to_tnd(a, cur or 'TND') if fx_rates else a

    # ── Pull data ──────────────────────────────────────────────────
    invoices = inv_store.get_all() if inv_store else []
    accounts = bal_store.get_all() if bal_store else []
    payments = pay_store.get_all() if pay_store else []

    customer_invoices = []
    if income_store:
        try: customer_invoices = income_store.list({'year': year})
        except Exception: customer_invoices = []

    pnl = None
    if statements_engine:
        try: pnl = statements_engine.compute_pnl(fx_rates, year, month=None)
        except Exception: pnl = None

    risk_alerts = []
    forecast_cf = None
    if forecast_engine:
        try: risk_alerts = forecast_engine.compute_risk_alerts(fx_rates)
        except Exception: risk_alerts = []
        try: forecast_cf = forecast_engine.compute_cashflow_forecast(fx_rates, months_ahead=3)
        except Exception: forecast_cf = None

    # ── Cash & liquidity primitives ────────────────────────────────
    paid_amt    = sum(_to_tnd(i.get('amount'), i.get('currency'))
                      for i in invoices if i.get('payment_status') == 'paid')
    unpaid_amt  = sum(_to_tnd(i.get('amount'), i.get('currency'))
                      for i in invoices if i.get('payment_status') == 'unpaid')
    overdue_amt = sum(_to_tnd(i.get('amount'), i.get('currency'))
                      for i in invoices if i.get('payment_status') == 'overdue')
    total_invoiced_amt = paid_amt + unpaid_amt + overdue_amt
    current_liabilities = unpaid_amt + overdue_amt
    net_cash = sum(_to_tnd(a.get('balance'), a.get('currency')) for a in accounts)

    # Monthly outflows for burn rate
    monthly_outflows: Dict[str, float] = {}
    for p in payments:
        pd = (p.get('payment_date') or '')[:7]
        if pd:
            monthly_outflows[pd] = monthly_outflows.get(pd, 0) + _to_tnd(p.get('amount'), p.get('currency'))
    months_with_data = max(1, len(monthly_outflows))
    burn_rate = sum(monthly_outflows.values()) / months_with_data
    if burn_rate == 0 and paid_amt > 0:
        burn_rate = paid_amt / 6.0  # rough estimate
    runway_months = (net_cash / burn_rate) if burn_rate > 0 else float('inf')

    liquidity_ratio = (net_cash / current_liabilities) if current_liabilities > 0 else float('inf')
    cash_flow      = net_cash - unpaid_amt
    op_cf_ratio    = (cash_flow / current_liabilities) if current_liabilities > 0 else float('inf')

    # ── Revenue primitives ─────────────────────────────────────────
    revenue_expected = sum(float(c.get('amount_tnd') or 0) for c in customer_invoices)
    revenue_received = sum(float(c.get('received_tnd') or 0) for c in customer_invoices)
    collection_rate  = (100 * revenue_received / revenue_expected) if revenue_expected > 0 else None
    invoice_count_ytd = len(customer_invoices)
    avg_invoice_value = (revenue_expected / invoice_count_ytd) if invoice_count_ytd > 0 else 0

    # DSO: average days between issue_date and paid_at
    dso_days = []
    for c in customer_invoices:
        if c.get('status') == 'paid' and c.get('issue_date') and c.get('paid_at'):
            try:
                d1 = datetime.fromisoformat(c['issue_date'].replace('Z', '')).date()
                d2 = datetime.fromisoformat(c['paid_at'].replace('Z', '')).date()
                delta = (d2 - d1).days
                if 0 <= delta <= 365:
                    dso_days.append(delta)
            except Exception:
                pass
    dso = sum(dso_days) / len(dso_days) if dso_days else 0

    # Overdue customer invoices
    overdue_cust = []
    if income_store:
        try: overdue_cust = income_store.overdue()
        except Exception: overdue_cust = []
    overdue_cust_amt = sum(float(c.get('amount_tnd') or 0) for c in overdue_cust)

    # ── Workforce primitives ───────────────────────────────────────
    try:
        from db import query as _db_query
        emp_rows = _db_query("SELECT COUNT(*) AS cnt, COALESCE(SUM(monthly_salary), 0) AS salary_sum FROM employees WHERE active = TRUE AND monthly_salary IS NOT NULL")
        emp_total = _db_query("SELECT COUNT(*) AS cnt FROM employees WHERE active = TRUE")
        dept_active = _db_query("SELECT COUNT(*) AS cnt FROM departments")
        proj_active = _db_query("SELECT COUNT(*) AS cnt FROM projects WHERE status = 'active'")
        headcount = int((emp_total[0]['cnt']) if emp_total else 0)
        depts     = int((dept_active[0]['cnt']) if dept_active else 0)
        projects  = int((proj_active[0]['cnt']) if proj_active else 0)
        salary_sum = float((emp_rows[0]['salary_sum']) if emp_rows else 0)
        emp_with_sal = int((emp_rows[0]['cnt']) if emp_rows else 0)
        avg_cost_emp = (salary_sum / emp_with_sal) if emp_with_sal > 0 else 0
    except Exception:
        headcount = depts = projects = 0
        avg_cost_emp = 0

    revenue_per_emp = (revenue_expected / headcount) if headcount > 0 else 0

    # ── Profitability primitives ───────────────────────────────────
    if pnl:
        revenue_pnl       = pnl.get('revenue', 0)
        charges_ext       = pnl['totals'].get('charges_externes', 0)
        charges_pers      = pnl['totals'].get('charges_personnel', 0)
        ebitda            = pnl['totals'].get('ebitda', 0)
        net_result        = pnl['totals'].get('net_result', 0)
        total_expenses    = pnl['totals'].get('total_expenses', 0)
        gross_profit      = revenue_pnl - charges_ext
        gross_margin      = (100 * gross_profit / revenue_pnl) if revenue_pnl > 0 else None
        ebitda_margin     = (100 * ebitda      / revenue_pnl) if revenue_pnl > 0 else None
        net_margin        = (100 * net_result  / revenue_pnl) if revenue_pnl > 0 else None
    else:
        revenue_pnl = charges_ext = charges_pers = ebitda = net_result = 0
        total_expenses = 0
        gross_profit = 0
        gross_margin = ebitda_margin = net_margin = None

    # ── Supplier concentration ─────────────────────────────────────
    supplier_totals: Dict[str, float] = {}
    for inv in invoices:
        s = inv.get('supplier_name') or 'Inconnu'
        supplier_totals[s] = supplier_totals.get(s, 0) + _to_tnd(inv.get('amount'), inv.get('currency'))
    supplier_hhi = 0.0
    top_supplier_share = 0.0
    if total_invoiced_amt > 0 and supplier_totals:
        shares = sorted([(s, v / total_invoiced_amt) for s, v in supplier_totals.items()],
                        key=lambda x: x[1], reverse=True)
        supplier_hhi = sum(share ** 2 for _, share in shares) * 10000
        top_supplier_share = shares[0][1] * 100 if shares else 0

    # AP turnover + DPO (legacy)
    avg_payable = max(current_liabilities, 1.0)
    ap_turnover = total_invoiced_amt / avg_payable
    dpo = 365.0 / ap_turnover if ap_turnover > 0 else 0.0

    # Payment delay
    delays = []
    for p in payments:
        inv = inv_store.get_by_id(p.get('invoice_id', '')) if inv_store else None
        if inv and inv.get('invoice_date') and p.get('payment_date'):
            try:
                d1 = datetime.fromisoformat(inv['invoice_date']).date()
                d2 = datetime.fromisoformat(p['payment_date']).date()
                delta = (d2 - d1).days
                if 0 <= delta <= 365: delays.append(delta)
            except Exception: pass
    avg_pay_delay = sum(delays) / len(delays) if delays else 0

    # ── Client concentration ───────────────────────────────────────
    client_totals: Dict[str, float] = {}
    for c in customer_invoices:
        name = c.get('client_name') or 'Inconnu'
        client_totals[name] = client_totals.get(name, 0) + float(c.get('amount_tnd') or 0)
    client_hhi = 0.0
    top_client_share = 0.0
    if revenue_expected > 0 and client_totals:
        shares = sorted([(s, v / revenue_expected) for s, v in client_totals.items()],
                        key=lambda x: x[1], reverse=True)
        client_hhi = sum(share ** 2 for _, share in shares) * 10000
        top_client_share = shares[0][1] * 100 if shares else 0

    # ── Forecast primitives ────────────────────────────────────────
    projected_q1_revenue = 0
    if forecast_cf and forecast_cf.get('forecast'):
        projected_q1_revenue = sum(m.get('revenue_exp', 0) for m in forecast_cf['forecast'])
    high_risks = sum(1 for a in risk_alerts if a.get('severity') == 'high')
    med_risks  = sum(1 for a in risk_alerts if a.get('severity') == 'med')

    # ── Compose categorized KPI list ───────────────────────────────
    def k(id, name, value, formatted, unit, description, trend, benchmark=None):
        d = {'id': id, 'name': name, 'value': value, 'formatted': formatted,
             'unit': unit, 'description': description, 'trend': trend}
        if benchmark:
            d['benchmark'] = benchmark
        return d

    categories = [
        {
            'id': 'cash',
            'name': 'Trésorerie & Liquidité',
            'icon': 'wallet',
            'kpis': [
                k('net_cash', 'Trésorerie nette', net_cash, _fmt_tnd(net_cash), 'TND',
                  'Somme des soldes bancaires et caisses (TND-converti).',
                  _trend(net_cash, good_above=0)),
                k('cash_flow', 'Cash flow', cash_flow, _fmt_tnd(cash_flow), 'TND',
                  'Position de trésorerie nette : actifs liquides moins engagements impayés.',
                  _trend(cash_flow, good_above=0)),
                k('liquidity_ratio', 'Ratio de liquidité',
                  liquidity_ratio if liquidity_ratio != float('inf') else 999,
                  f"{liquidity_ratio:.2f}" if liquidity_ratio != float('inf') else '∞',
                  'ratio',
                  'Actifs liquides / passifs courants. > 1.5 = sain.',
                  _trend(liquidity_ratio, good_above=1.5, neutral_range=(1.0, 1.5)),
                  benchmark='≥ 1.5'),
                k('op_cf_ratio', 'Ratio cash flow opérationnel',
                  op_cf_ratio if op_cf_ratio != float('inf') else 999,
                  f"{op_cf_ratio:.2f}" if op_cf_ratio != float('inf') else '∞',
                  'ratio',
                  'Cash flow / passifs courants. Capacité à couvrir les dettes court terme.',
                  _trend(op_cf_ratio, good_above=1.0, neutral_range=(0.5, 1.0)),
                  benchmark='≥ 1.0'),
                k('burn_rate', 'Burn rate mensuel', burn_rate,
                  _fmt_tnd(burn_rate) + '/mois', 'TND/mois',
                  'Dépenses mensuelles moyennes. Indicateur clé pour la planification trésorerie.',
                  _trend(-burn_rate, good_above=-(net_cash * 0.3 if net_cash > 0 else 1e9))),
                k('runway', 'Autonomie financière (Runway)',
                  runway_months if runway_months != float('inf') else 999,
                  f"{runway_months:.1f} mois" if runway_months != float('inf') else '∞',
                  'mois',
                  'Combien de mois la trésorerie tient au rythme actuel des dépenses.',
                  _trend(runway_months, good_above=12, neutral_range=(6, 12)),
                  benchmark='≥ 12 mois'),
            ],
        },
        {
            'id': 'revenue',
            'name': 'Revenus & Clients',
            'icon': 'trending-up',
            'kpis': [
                k('revenue_expected', "Chiffre d'affaires attendu (YTD)", revenue_expected,
                  _fmt_tnd(revenue_expected), 'TND',
                  "Somme des factures clients émises depuis le début de l'année.",
                  _trend(revenue_expected, good_above=0)),
                k('revenue_received', "Chiffre d'affaires encaissé", revenue_received,
                  _fmt_tnd(revenue_received), 'TND',
                  "Montant effectivement reçu en TND.",
                  _trend(revenue_received, good_above=0)),
                k('collection_rate', 'Taux de recouvrement',
                  collection_rate if collection_rate is not None else 0,
                  _fmt_pct(collection_rate),
                  '%',
                  'Pourcentage des factures émises qui ont été encaissées.',
                  _trend(collection_rate, good_above=85, neutral_range=(70, 85)),
                  benchmark='≥ 85%'),
                k('dso', 'Délai moyen de paiement client (DSO)', dso,
                  f"{dso:.0f} jours", 'jours',
                  'Nombre moyen de jours entre émission et paiement client.',
                  _trend(dso, good_below=45, neutral_range=(45, 60)),
                  benchmark='≤ 45 jours'),
                k('avg_invoice', 'Facture moyenne', avg_invoice_value,
                  _fmt_tnd(avg_invoice_value), 'TND',
                  'Montant moyen par facture émise.',
                  'neutral'),
                k('revenue_per_emp', 'CA par employé', revenue_per_emp,
                  _fmt_tnd(revenue_per_emp), 'TND',
                  "CA annuel attendu divisé par l'effectif actif. Mesure la productivité par tête.",
                  _trend(revenue_per_emp, good_above=50000)),
                k('overdue_receivables', 'Factures clients en retard', overdue_cust_amt,
                  _fmt_tnd(overdue_cust_amt) + f' ({len(overdue_cust)} fact.)', 'TND',
                  'Total des factures client échues non encore encaissées.',
                  _trend(-overdue_cust_amt, good_above=0)),
            ],
        },
        {
            'id': 'profitability',
            'name': 'Rentabilité',
            'icon': 'percent',
            'kpis': [
                k('gross_margin', 'Marge brute',
                  gross_margin if gross_margin is not None else 0,
                  _fmt_pct(gross_margin), '%',
                  '(CA − Charges externes) / CA. Marge avant charges de personnel.',
                  _trend(gross_margin, good_above=40, neutral_range=(20, 40)),
                  benchmark='≥ 40%'),
                k('ebitda_margin', 'Marge EBITDA',
                  ebitda_margin if ebitda_margin is not None else 0,
                  _fmt_pct(ebitda_margin), '%',
                  'EBITDA / CA. Rentabilité opérationnelle avant amortissements et impôts.',
                  _trend(ebitda_margin, good_above=20, neutral_range=(10, 20)),
                  benchmark='≥ 20%'),
                k('net_margin', 'Marge nette',
                  net_margin if net_margin is not None else 0,
                  _fmt_pct(net_margin), '%',
                  'Résultat net / CA. Rentabilité finale après impôts et provisions.',
                  _trend(net_margin, good_above=10, neutral_range=(5, 10)),
                  benchmark='≥ 10%'),
                k('net_result', 'Résultat net YTD', net_result,
                  _fmt_tnd(net_result), 'TND',
                  'Bottom-line du P&L depuis le début de l\'année.',
                  _trend(net_result, good_above=0)),
                k('total_expenses', 'Dépenses totales YTD', total_expenses,
                  _fmt_tnd(total_expenses), 'TND',
                  'Charges externes + personnel + taxes + financières + provision IS.',
                  'neutral'),
            ],
        },
        {
            'id': 'suppliers',
            'name': 'Fournisseurs & Achats',
            'icon': 'truck',
            'kpis': [
                k('ap_turnover', 'Rotation des dettes fournisseurs', ap_turnover,
                  f"{ap_turnover:.2f}x", 'ratio',
                  'Nombre de fois que les dettes fournisseurs sont renouvelées. Plus élevé = paiement rapide.',
                  _trend(ap_turnover, good_above=4)),
                k('dpo', 'DPO (Days Payable Outstanding)', dpo,
                  f"{dpo:.0f} jours", 'jours',
                  'Délai moyen de paiement des fournisseurs.',
                  _trend(dpo, good_below=60, neutral_range=(30, 60)),
                  benchmark='30–60 jours'),
                k('payment_delay', 'Délai de paiement effectif', avg_pay_delay,
                  f"{avg_pay_delay:.0f} jours", 'jours',
                  'Délai moyen entre date de facture et paiement effectif.',
                  _trend(avg_pay_delay, good_below=30, neutral_range=(30, 60))),
                k('supplier_hhi', 'Concentration fournisseurs (HHI)', supplier_hhi,
                  f"{int(supplier_hhi):,}".replace(',', ' '), 'index',
                  'Herfindahl-Hirschman Index. < 1500 = diversifié, > 2500 = concentré.',
                  _trend(supplier_hhi, good_below=1500, neutral_range=(1500, 2500))),
                k('top_supplier_share', 'Part du top fournisseur', top_supplier_share,
                  _fmt_pct(top_supplier_share), '%',
                  'Pourcentage du total dépenses concentré sur le plus gros fournisseur.',
                  _trend(top_supplier_share, good_below=30, neutral_range=(30, 50))),
                k('to_pay_suppliers', 'À payer fournisseurs',
                  unpaid_amt + overdue_amt,
                  _fmt_tnd(unpaid_amt + overdue_amt), 'TND',
                  'Total des factures fournisseurs non payées (impayées + en retard).',
                  _trend(-(unpaid_amt + overdue_amt), good_above=0)),
            ],
        },
        {
            'id': 'workforce',
            'name': 'Effectifs & Organisation',
            'icon': 'users',
            'kpis': [
                k('headcount', 'Effectif actif', headcount,
                  f"{headcount} pers.", 'pers',
                  'Nombre total d\'employés actifs dans le registre interne.',
                  _trend(headcount, good_above=1)),
                k('departments', 'Départements', depts,
                  f"{depts}", 'unités',
                  'Nombre de départements actifs.',
                  'neutral'),
                k('active_projects', 'Projets actifs', projects,
                  f"{projects}", 'projets',
                  'Projets en statut "actif" liés à un client.',
                  _trend(projects, good_above=1)),
                k('avg_cost_emp', 'Coût moyen par employé', avg_cost_emp,
                  _fmt_tnd(avg_cost_emp) + '/mois', 'TND/mois',
                  'Salaire mensuel moyen brut des employés actifs (hors CNSS).',
                  'neutral'),
            ],
        },
        {
            'id': 'risk',
            'name': 'Risque & Prospective',
            'icon': 'alert',
            'kpis': [
                k('active_risks', 'Alertes risques actives', high_risks + med_risks,
                  f"{high_risks + med_risks} (dont {high_risks} hauts)", 'alertes',
                  'Signaux de risque détectés automatiquement (haut + moyen).',
                  _trend(-(high_risks * 2 + med_risks), good_above=0)),
                k('projected_q_revenue', 'CA prévu (3 mois)', projected_q1_revenue,
                  _fmt_tnd(projected_q1_revenue), 'TND',
                  'Projection du chiffre d\'affaires des 3 prochains mois (méthode auto).',
                  _trend(projected_q1_revenue, good_above=0)),
                k('client_hhi', 'Concentration clients (HHI)', client_hhi,
                  f"{int(client_hhi):,}".replace(',', ' '), 'index',
                  'HHI de la concentration revenu par client. < 1500 = diversifié.',
                  _trend(client_hhi, good_below=2500, neutral_range=(1500, 2500))),
                k('top_client_share', 'Part du top client', top_client_share,
                  _fmt_pct(top_client_share), '%',
                  'Pourcentage du CA total concentré sur le plus gros client.',
                  _trend(top_client_share, good_below=40, neutral_range=(40, 70))),
            ],
        },
    ]

    # ── Composite health score (0..100) ────────────────────────────
    # Average of normalized positive/negative trends across all KPIs
    # (positive=100, neutral=50, negative=0).
    all_kpis = [kpi for c in categories for kpi in c['kpis']]
    trend_scores = []
    for kpi in all_kpis:
        t = kpi.get('trend')
        if t == 'positive': trend_scores.append(100)
        elif t == 'negative': trend_scores.append(0)
        else: trend_scores.append(50)
    health_score = round(sum(trend_scores) / len(trend_scores)) if trend_scores else 50

    return {
        'health_score':  health_score,
        'year':          year,
        'categories':    categories,
        'kpi_count':     sum(len(c['kpis']) for c in categories),
    }
