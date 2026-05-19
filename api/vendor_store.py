"""
Vendor (supplier) store — Postgres-backed.

Normalizes supplier_name strings from the invoices table into a proper
relational entity. Includes a migration helper that backfills vendors
from existing distinct supplier_names so we don't lose history.
"""
import uuid
from datetime import datetime
from typing import Optional, Dict, List

import db


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    def _dt(v):
        if v is None: return ''
        if hasattr(v, 'isoformat'): return v.isoformat()
        return str(v)
    return {
        'id':                         r['id'],
        'name':                       r['name'],
        'legal_name':                 r.get('legal_name') or '',
        'tax_id':                     r.get('tax_id') or '',
        'vat_id':                     r.get('vat_id') or '',
        'country':                    r.get('country') or 'TN',
        'email':                      r.get('email') or '',
        'phone':                      r.get('phone') or '',
        'address':                    r.get('address') or '',
        'default_payment_terms_days': int(r.get('default_payment_terms_days') or 30),
        'default_category':           r.get('default_category') or '',
        'notes':                      r.get('notes') or '',
        'active':                     bool(r.get('active', True)),
        'created_at':                 _dt(r.get('created_at')),
        'updated_at':                 _dt(r.get('updated_at')),
    }


class VendorStore:
    def __init__(self, **_): pass

    def list(self, include_inactive: bool = False) -> List[Dict]:
        if include_inactive:
            sql = """
                SELECT v.*,
                       (SELECT COUNT(*) FROM invoices i WHERE i.vendor_id = v.id) AS invoice_count,
                       (SELECT COALESCE(SUM(i.amount_tnd), 0) FROM invoices i WHERE i.vendor_id = v.id) AS total_spent_tnd
                  FROM vendors v
                 ORDER BY v.active DESC, v.name ASC
            """
        else:
            sql = """
                SELECT v.*,
                       (SELECT COUNT(*) FROM invoices i WHERE i.vendor_id = v.id) AS invoice_count,
                       (SELECT COALESCE(SUM(i.amount_tnd), 0) FROM invoices i WHERE i.vendor_id = v.id) AS total_spent_tnd
                  FROM vendors v
                 WHERE v.active = TRUE
                 ORDER BY v.name ASC
            """
        rows = db.query(sql)
        out = []
        for r in rows:
            v = _row(r) or {}
            v['invoice_count'] = int(r.get('invoice_count') or 0)
            v['total_spent_tnd'] = float(r.get('total_spent_tnd') or 0)
            out.append(v)
        return out

    def get(self, vid: str) -> Optional[Dict]:
        if not vid: return None
        return _row(db.one("SELECT * FROM vendors WHERE id = %s", (vid,)))

    def get_by_name(self, name: str) -> Optional[Dict]:
        if not name: return None
        row = db.one("SELECT * FROM vendors WHERE LOWER(name) = LOWER(%s)", (name.strip(),))
        return _row(row)

    def add(self, data: Dict, created_by: Optional[str] = None) -> Dict:
        name = (data.get('name') or '').strip()
        if not name:
            raise ValueError("Nom du fournisseur requis.")
        existing = db.one("SELECT id FROM vendors WHERE LOWER(name) = LOWER(%s)", (name,))
        if existing:
            raise ValueError(f"Un fournisseur '{name}' existe déjà.")

        vid = _new_id()
        try:
            terms = int(data.get('default_payment_terms_days') or 30)
        except (TypeError, ValueError):
            terms = 30
        db.execute(
            """INSERT INTO vendors
                 (id, name, legal_name, tax_id, vat_id, country, email, phone, address,
                  default_payment_terms_days, default_category, notes, active,
                  created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (vid, name,
             (data.get('legal_name') or '').strip() or None,
             (data.get('tax_id') or '').strip() or None,
             (data.get('vat_id') or '').strip() or None,
             (data.get('country') or 'TN').strip().upper()[:2],
             (data.get('email') or '').strip() or None,
             (data.get('phone') or '').strip() or None,
             (data.get('address') or '').strip() or None,
             terms,
             (data.get('default_category') or '').strip().lower() or None,
             (data.get('notes') or '').strip() or None,
             bool(data.get('active', True)),
             created_by,
             datetime.utcnow(),
             datetime.utcnow()),
        )
        return self.get(vid) or {'id': vid}

    def update(self, vid: str, patch: Dict) -> Optional[Dict]:
        if not vid or not self.get(vid):
            return None
        sets, params = [], []
        text_fields = ('legal_name', 'tax_id', 'vat_id', 'email', 'phone',
                       'address', 'notes', 'default_category')
        for f in text_fields:
            if f in patch:
                v = (patch[f] or '').strip()
                if f == 'default_category': v = v.lower()
                sets.append(f"{f} = %s")
                params.append(v if v else None)
        if 'name' in patch:
            v = (patch['name'] or '').strip()
            if not v: raise ValueError("Nom requis.")
            dup = db.one("SELECT id FROM vendors WHERE LOWER(name) = LOWER(%s) AND id <> %s", (v, vid))
            if dup: raise ValueError(f"Un autre fournisseur s'appelle déjà '{v}'.")
            sets.append("name = %s"); params.append(v)
        if 'country' in patch:
            sets.append("country = %s"); params.append((patch['country'] or 'TN').strip().upper()[:2])
        if 'default_payment_terms_days' in patch:
            try: t = int(patch['default_payment_terms_days'])
            except (TypeError, ValueError): t = 30
            sets.append("default_payment_terms_days = %s"); params.append(t)
        if 'active' in patch:
            sets.append("active = %s"); params.append(bool(patch['active']))

        if not sets: return self.get(vid)
        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(vid)
        db.execute(f"UPDATE vendors SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get(vid)

    def delete(self, vid: str) -> bool:
        # Invoices keep vendor_id NULL on delete (FK ON DELETE SET NULL)
        return db.execute("DELETE FROM vendors WHERE id = %s", (vid,)) > 0

    def monthly_spend_series(self, vendor_id: str, year: int) -> List[float]:
        """Return a 12-element list of TND amounts spent at this vendor
        per month of the given year (for sparklines)."""
        rows = db.query(
            """SELECT EXTRACT(MONTH FROM invoice_date)::int AS m,
                      SUM(COALESCE(amount_tnd, amount, 0)) AS s
                 FROM invoices
                WHERE vendor_id = %s
                  AND invoice_date IS NOT NULL
                  AND EXTRACT(YEAR FROM invoice_date) = %s
                GROUP BY EXTRACT(MONTH FROM invoice_date)""",
            (vendor_id, year),
        )
        by_month = {int(r['m']): float(r['s'] or 0) for r in rows}
        return [round(by_month.get(m, 0), 2) for m in range(1, 13)]

    def overview(self, year: Optional[int] = None) -> Dict:
        """Aggregate metrics across all vendors for the dashboard hero.

        Returns:
          {
            year, total_vendors, total_active, total_spent_ytd_tnd,
            avg_ticket_size, top_vendor: {name, total},
            concentration_pct (top-3 share),
            spend_by_category: [{label, amount}, ...],
            spend_by_country: [{country, amount, count}, ...],
            monthly_total: [12 floats]   (all vendors combined)
          }
        """
        from datetime import datetime as _dt
        if year is None:
            year = _dt.utcnow().year
        # Total vendors
        agg = db.one(
            """SELECT
                 (SELECT COUNT(*) FROM vendors) AS total,
                 (SELECT COUNT(*) FROM vendors WHERE active = TRUE) AS active"""
        )
        total = int((agg or {}).get('total') or 0)
        active = int((agg or {}).get('active') or 0)

        # Total spent + per vendor for the year
        per_vendor_rows = db.query(
            """SELECT COALESCE(v.id, '__none__') AS vid,
                      COALESCE(v.name, i.supplier_name, 'Inconnu') AS name,
                      COALESCE(v.country, 'TN') AS country,
                      SUM(COALESCE(i.amount_tnd, i.amount, 0)) AS total,
                      COUNT(*) AS invoice_count
                 FROM invoices i
                 LEFT JOIN vendors v ON v.id = i.vendor_id
                WHERE i.invoice_date IS NOT NULL
                  AND EXTRACT(YEAR FROM i.invoice_date) = %s
                GROUP BY v.id, v.name, i.supplier_name, v.country
                ORDER BY total DESC""",
            (year,),
        )
        total_spent = sum(float(r['total'] or 0) for r in per_vendor_rows)
        invoice_count = sum(int(r['invoice_count'] or 0) for r in per_vendor_rows)
        avg_ticket = (total_spent / invoice_count) if invoice_count > 0 else 0.0
        top_vendor = {'name': '', 'total': 0.0}
        if per_vendor_rows:
            top_vendor = {
                'name':  per_vendor_rows[0]['name'] or '',
                'total': round(float(per_vendor_rows[0]['total'] or 0), 2),
            }
        # Concentration: top-3 share
        top3_share_pct = 0.0
        if total_spent > 0:
            top3 = sum(float(r['total'] or 0) for r in per_vendor_rows[:3])
            top3_share_pct = round(100 * top3 / total_spent, 1)

        # Spend by category (uses invoices.category from AI classification)
        cat_rows = db.query(
            """SELECT COALESCE(category, 'autre') AS cat,
                      SUM(COALESCE(amount_tnd, amount, 0)) AS s
                 FROM invoices
                WHERE invoice_date IS NOT NULL
                  AND EXTRACT(YEAR FROM invoice_date) = %s
                GROUP BY category
                ORDER BY s DESC""",
            (year,),
        )
        try:
            from invoice_processor import CATEGORY_LABELS
        except Exception:
            CATEGORY_LABELS = {}
        spend_by_category = [{
            'code':   r['cat'],
            'label':  CATEGORY_LABELS.get(r['cat'], (r['cat'] or 'autre').capitalize()),
            'amount': round(float(r['s'] or 0), 2),
        } for r in cat_rows]

        # Spend by country
        country_map: Dict[str, Dict] = {}
        for r in per_vendor_rows:
            c = r.get('country') or 'TN'
            if c not in country_map:
                country_map[c] = {'country': c, 'amount': 0.0, 'count': 0}
            country_map[c]['amount'] += float(r['total'] or 0)
            country_map[c]['count']  += int(r['invoice_count'] or 0)
        spend_by_country = sorted(country_map.values(), key=lambda x: -x['amount'])
        for c in spend_by_country:
            c['amount'] = round(c['amount'], 2)

        # Monthly total combined
        monthly_rows = db.query(
            """SELECT EXTRACT(MONTH FROM invoice_date)::int AS m,
                      SUM(COALESCE(amount_tnd, amount, 0)) AS s
                 FROM invoices
                WHERE invoice_date IS NOT NULL
                  AND EXTRACT(YEAR FROM invoice_date) = %s
                GROUP BY EXTRACT(MONTH FROM invoice_date)""",
            (year,),
        )
        by_month = {int(r['m']): float(r['s'] or 0) for r in monthly_rows}
        monthly_total = [round(by_month.get(m, 0), 2) for m in range(1, 13)]

        return {
            'year':                  year,
            'total_vendors':         total,
            'total_active':          active,
            'total_spent_ytd_tnd':   round(total_spent, 2),
            'avg_ticket_size':       round(avg_ticket, 2),
            'invoice_count':         invoice_count,
            'top_vendor':            top_vendor,
            'concentration_pct':     top3_share_pct,
            'spend_by_category':     spend_by_category,
            'spend_by_country':      spend_by_country,
            'monthly_total':         monthly_total,
        }

    def list_with_sparklines(self, year: Optional[int] = None) -> List[Dict]:
        """Like list() but each vendor gets a 12-month spend series."""
        from datetime import datetime as _dt
        if year is None:
            year = _dt.utcnow().year
        vendors = self.list(include_inactive=False)
        for v in vendors:
            v['monthly_spend_tnd'] = self.monthly_spend_series(v['id'], year)
        return vendors

    def backfill_from_invoices(self) -> Dict:
        """One-shot migration: for every distinct supplier_name in invoices
        that has no vendor_id yet, create a vendor and link the invoices.
        Returns {created: N, linked: M}.
        """
        # Collect distinct supplier_name values that exist but have NULL vendor_id
        rows = db.query(
            """SELECT DISTINCT TRIM(supplier_name) AS name
                 FROM invoices
                WHERE supplier_name IS NOT NULL
                  AND TRIM(supplier_name) <> ''
                  AND vendor_id IS NULL"""
        )
        created = 0
        linked  = 0
        for r in rows:
            name = (r.get('name') or '').strip()
            if not name:
                continue
            existing = self.get_by_name(name)
            if existing:
                vid = existing['id']
            else:
                try:
                    v = self.add({'name': name})
                    vid = v['id']
                    created += 1
                except ValueError:
                    continue
            n = db.execute(
                """UPDATE invoices SET vendor_id = %s
                    WHERE vendor_id IS NULL
                      AND LOWER(TRIM(supplier_name)) = LOWER(%s)""",
                (vid, name),
            )
            linked += n
        return {'created': created, 'linked': linked}
