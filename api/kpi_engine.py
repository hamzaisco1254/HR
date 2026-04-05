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
