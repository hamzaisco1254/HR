"""
Mr Hamza Finance Assistant — AI Q&A grounded in current financial state.

Strategy: structured-data context injection.
  - Detect what the question is about (revenue, expenses, forecast, etc.)
  - Pull the relevant numbers from the database
  - Format as a compact JSON context
  - Send to Gemini with a system prompt asking for grounded answers

Trade-off vs full tool-calling: simpler (one round-trip, no multi-turn
function-call orchestration), works today, results are reproducible.
The downside is that we send more context than strictly needed for
broad questions. Acceptable for our data size.
"""
import json
from datetime import datetime
from typing import Dict, List, Optional

from gemini_client import is_configured, generate
import statements_engine
import forecast_engine
import db


_SYSTEM_PROMPT = """Tu es Mr Hamza, l'assistant financier IA du dirigeant d'une entreprise IT tunisienne.

ROLE :
- Tu reponds aux questions sur les finances de l'entreprise en t'appuyant UNIQUEMENT sur le contexte JSON fourni.
- Tu reponds en francais, ton professionnel mais accessible.
- Tu cites les chiffres concretement, jamais d'estimations vagues.
- Si la donnee demandee n'est pas dans le contexte, tu le dis honnetement : "Je n'ai pas cette donnee dans le contexte actuel."

REGLES :
1. Toujours montrer les chiffres en TND avec leur format (ex : "12 450 TND").
2. Pour les pourcentages, arrondir a une decimale.
3. Mentionner les hypotheses derriere une projection (methode, fiabilite).
4. Si l'utilisateur pose une question vague, demander une precision OU offrir 2-3 angles d'analyse pertinents.
5. Etre court : 3-5 phrases maximum sauf si on demande un rapport detaille.
6. JAMAIS inventer un nombre. JAMAIS prefixer ta reponse par "D'apres mes calculs..." ou similaire.
7. Terminer en proposant une action concrete OU une question de suivi pertinente, en une seule ligne.
"""


# ─── Question routing (keyword-based, fast and deterministic) ────────

_KEYWORDS = {
    'forecast': ['previs', 'forecast', 'projection', 'futur', 'prochain', 'trimestre', 'avenir', 'predire', 'predict'],
    'cashflow': ['cash flow', 'tresorerie', 'flux', 'liquidit'],
    'revenue':  ['revenu', 'chiffre d\'affaires', 'ca', 'income', 'facturat', 'encaiss'],
    'expense':  ['depense', 'cout', 'charge', 'achat', 'frais'],
    'profit':   ['profit', 'benefice', 'resultat', 'rentab', 'marge', 'ebitda'],
    'overdue':  ['retard', 'impaye', 'overdue', 'recouvr'],
    'client':   ['client', 'customer', 'plus rentable', 'concentrat'],
    'dept':     ['departement', 'department', 'equipe', 'team'],
    'compare':  ['compar', 'evolution', 'tendance', 'augment', 'baisse', 'pourquoi'],
    'risk':     ['risque', 'alerte', 'probleme', 'anomalie', 'attention'],
}


def _topics(question: str) -> List[str]:
    """Return the set of detected topics in the question."""
    q = question.lower()
    out = []
    for topic, words in _KEYWORDS.items():
        if any(w in q for w in words):
            out.append(topic)
    return out or ['overview']  # default: give a general snapshot


# ─── Context builder ─────────────────────────────────────────────────

def build_context(fx_rates, question: str) -> Dict:
    """Pick a relevant slice of the financial state based on the question.

    Returns a JSON-friendly dict that will be embedded in the prompt.
    """
    topics = _topics(question)
    today  = datetime.utcnow().date()
    year   = today.year
    ctx: Dict = {
        'as_of':           today.isoformat(),
        'detected_topics': topics,
        'currency':        'TND (toutes valeurs)',
    }

    # Always include the year-to-date P&L summary (cheap, gives grounding)
    pnl = statements_engine.compute_pnl(fx_rates, year, month=None)
    ctx['pnl_ytd'] = {
        'revenue':                 round(pnl['revenue'], 2),
        'charges_externes':        round(pnl['totals']['charges_externes'], 2),
        'charges_personnel':       round(pnl['totals']['charges_personnel'], 2),
        'cnss_employer':           round(pnl['totals'].get('cnss_employer', 0), 2),
        'taxes':                   round(pnl['totals']['taxes'], 2),
        'charges_financieres':     round(pnl['totals']['charges_financieres'], 2),
        'ebitda':                  round(pnl['totals']['ebitda'], 2),
        'net_before_tax':          round(pnl['totals'].get('net_before_tax', 0), 2),
        'corporate_tax_provision': round(pnl['totals'].get('corporate_tax_provision', 0), 2),
        'net_result':              round(pnl['totals']['net_result'], 2),
        'rates_used':              pnl['period'].get('rates', {}),
    }

    if 'cashflow' in topics or 'overview' in topics or 'forecast' in topics:
        cf = statements_engine.compute_cashflow(fx_rates, year)
        ctx['cashflow_ytd'] = {
            'months': [
                {'month': m['month'],
                 'in':    round(m['revenue_in'], 2),
                 'out':   round(m['expenses_out'], 2),
                 'net':   round(m['net_cf'], 2)}
                for m in cf['months']
            ],
            'total': {k: round(v, 2) for k, v in cf['total'].items()},
        }

    if 'revenue' in topics or 'client' in topics or 'dept' in topics or 'compare' in topics:
        rb = statements_engine.compute_revenue_breakdown(fx_rates, year)
        ctx['revenue_breakdown'] = {
            'top_clients': [
                {'name': c['client_name'], 'expected': round(c['expected_tnd'], 2),
                 'received': round(c['received_tnd'], 2), 'invoices': c['invoice_count']}
                for c in rb['by_client'][:5]
            ],
            'by_department': [
                {'name': d['department_name'], 'expected': round(d['expected_tnd'], 2),
                 'received': round(d['received_tnd'], 2)}
                for d in rb['by_department']
            ],
            'months': [
                {'month': m['month'], 'expected': round(m['expected_tnd'], 2),
                 'received': round(m['received_tnd'], 2)}
                for m in rb['by_month']
            ],
        }

    if 'expense' in topics or 'profit' in topics or 'compare' in topics:
        eb = statements_engine.compute_expense_breakdown(fx_rates, year)
        ctx['expense_breakdown'] = {
            'by_category': [
                {'category': r['category'], 'amount': round(r['amount_tnd'], 2),
                 'count': r['count']}
                for r in eb['by_category']
            ],
            'by_month': [
                {'month': m['month'], 'amount': round(m['amount_tnd'], 2)}
                for m in eb['by_month']
            ],
            'total': round(eb['grand_total'], 2),
        }

    if 'overdue' in topics or 'risk' in topics:
        overdue = db.query(
            """SELECT ci.invoice_ref, ci.due_date, ci.amount_tnd, c.name AS client_name
                 FROM customer_invoices ci
                 LEFT JOIN clients c ON c.id = ci.client_id
                WHERE ci.status IN ('sent', 'draft', 'overdue')
                  AND ci.due_date IS NOT NULL
                  AND ci.due_date < %s
                ORDER BY ci.due_date""",
            (today,),
        )
        ctx['overdue'] = [
            {'invoice_ref': r.get('invoice_ref') or '',
             'client': r.get('client_name') or '',
             'amount_tnd': round(float(r.get('amount_tnd') or 0), 2),
             'days_overdue': (today - r['due_date']).days if r.get('due_date') else 0}
            for r in overdue
        ]

    if 'forecast' in topics or 'cashflow' in topics:
        fc = forecast_engine.compute_cashflow_forecast(fx_rates, months_ahead=6)
        ctx['forecast_6m'] = {
            'method':     fc['method_used'],
            'confidence': fc['confidence'],
            'months': [
                {'year': m['year'], 'month': m['month'],
                 'revenue_expected': round(m['revenue_exp'], 2),
                 'expense_expected': round(m['expense_exp'], 2),
                 'net_expected':     round(m['net_expected'], 2),
                 'net_worst':        round(m['net_low'], 2),
                 'net_best':         round(m['net_high'], 2)}
                for m in fc['forecast']
            ],
        }

    if 'risk' in topics or 'overview' in topics:
        ctx['risk_alerts'] = forecast_engine.compute_risk_alerts(fx_rates)

    return ctx


# ─── Q&A ─────────────────────────────────────────────────────────────

def ask(fx_rates, question: str, history: Optional[List[Dict]] = None) -> Dict:
    """Run a finance question through Gemini with structured context.

    Returns:
        {answer, context_used (compact), topics, ai_used}
    """
    if not is_configured():
        return {
            'answer':       "L'assistant IA n'est pas configure. Variable GEMINI_API_KEY manquante.",
            'context_used': {},
            'topics':       [],
            'ai_used':      False,
        }

    context = build_context(fx_rates, question)
    user_prompt = (
        f"CONTEXTE FINANCIER (JSON, source : base de donnees, "
        f"as_of {context['as_of']}) :\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        f"QUESTION : {question}\n\n"
        "Reponds en t'appuyant uniquement sur ce contexte. Si la donnee n'y "
        "est pas, signale-le."
    )

    try:
        answer = generate(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            history=history or [],
            temperature=0.2,
            max_tokens=900,
        ) or "Je n'ai pas pu generer de reponse."
    except Exception as e:
        return {
            'answer':       f"Erreur de l'assistant IA : {e}",
            'context_used': context,
            'topics':       context.get('detected_topics', []),
            'ai_used':      False,
        }

    return {
        'answer':       answer,
        'context_used': context,
        'topics':       context.get('detected_topics', []),
        'ai_used':      True,
    }


# Predefined questions exposed to the UI as quick-buttons
SUGGESTED_QUESTIONS = [
    "Quel est notre resultat net cumule depuis le debut de l'annee ?",
    "Quel departement est le plus rentable ?",
    "Quelle est la projection de cash flow pour les 3 prochains mois ?",
    "Pourquoi nos depenses ont-elles augmente recemment ?",
    "Quels sont les principaux risques financiers actuels ?",
    "Quel client represente la plus grosse part de notre CA ?",
    "Quelles factures clients sont en retard ?",
    "Si nos depenses continuent au rythme actuel, quel sera notre resultat fin d'annee ?",
]
