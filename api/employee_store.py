"""
Employee + Department stores — Postgres-backed.

Houses two related stores: DepartmentStore and EmployeeStore. Schema
created by db_schema.py — this module only handles CRUD.

Salary is sensitive: callers must filter `monthly_salary` and
`salary_currency` out of API responses for non-admin users. Helper
`strip_sensitive()` is provided for that.
"""
import uuid
from datetime import datetime
from typing import Optional

import db


VALID_CURRENCIES = ('TND', 'EUR', 'USD', 'GBP', 'CHF')


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _norm_currency(value: str) -> str:
    code = (value or '').strip().upper()
    return code if code in VALID_CURRENCIES else 'TND'


def _date(value):
    """Pass YYYY-MM-DD strings or datetime/date objects through; '' -> None."""
    if value in (None, ''):
        return None
    return value


def _decimal(value):
    """Coerce '' / None to None; numeric strings to float."""
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ─── Department ──────────────────────────────────────────────────────

def _dept_row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {
        'id':          r['id'],
        'name':        r['name'],
        'cost_center': r.get('cost_center') or '',
        'manager_id':  r.get('manager_id'),
        'notes':       r.get('notes') or '',
        'created_at':  str(r.get('created_at') or ''),
        'updated_at':  str(r.get('updated_at') or ''),
    }


class DepartmentStore:
    def __init__(self, **_):
        pass

    def list_departments(self) -> list:
        rows = db.query(
            """SELECT d.*,
                      e.full_name AS manager_name,
                      (SELECT COUNT(*) FROM employees WHERE department_id = d.id AND active = TRUE) AS employee_count
                 FROM departments d
                 LEFT JOIN employees e ON e.id = d.manager_id
                ORDER BY d.name ASC"""
        )
        result = []
        for r in rows:
            d = _dept_row(r) or {}
            d['manager_name']   = r.get('manager_name') or ''
            d['employee_count'] = int(r.get('employee_count') or 0)
            result.append(d)
        return result

    def get(self, did: str) -> Optional[dict]:
        if not did:
            return None
        return _dept_row(db.one("SELECT * FROM departments WHERE id = %s", (did,)))

    def add(self, data: dict, created_by: Optional[str] = None) -> dict:
        name = (data.get('name') or '').strip()
        if not name:
            raise ValueError("Nom du departement requis.")
        if db.one("SELECT id FROM departments WHERE LOWER(name) = LOWER(%s)", (name,)):
            raise ValueError(f"Un departement '{name}' existe deja.")

        did = _new_id()
        db.execute(
            """INSERT INTO departments
                 (id, name, cost_center, manager_id, notes, created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (did, name,
             (data.get('cost_center') or '').strip() or None,
             (data.get('manager_id') or None),
             (data.get('notes') or '').strip() or None,
             created_by,
             datetime.utcnow(),
             datetime.utcnow()),
        )
        return self.get(did) or {'id': did}

    def update(self, did: str, patch: dict) -> Optional[dict]:
        if not did or not self.get(did):
            return None
        sets, params = [], []
        if 'name' in patch:
            v = (patch['name'] or '').strip()
            if not v:
                raise ValueError("Nom du departement requis.")
            dup = db.one(
                "SELECT id FROM departments WHERE LOWER(name) = LOWER(%s) AND id <> %s",
                (v, did),
            )
            if dup:
                raise ValueError(f"Un autre departement porte deja le nom '{v}'.")
            sets.append("name = %s"); params.append(v)
        if 'cost_center' in patch:
            sets.append("cost_center = %s"); params.append((patch['cost_center'] or '').strip() or None)
        if 'manager_id' in patch:
            sets.append("manager_id = %s"); params.append(patch['manager_id'] or None)
        if 'notes' in patch:
            sets.append("notes = %s"); params.append((patch['notes'] or '').strip() or None)

        if not sets:
            return self.get(did)
        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(did)
        db.execute(f"UPDATE departments SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get(did)

    def delete(self, did: str) -> bool:
        # Employees hold ON DELETE SET NULL — they survive but lose dept link
        return db.execute("DELETE FROM departments WHERE id = %s", (did,)) > 0


# ─── Employee ────────────────────────────────────────────────────────

_EMP_TEXT_FIELDS = (
    'matricule', 'cin', 'full_name', 'email', 'phone', 'position', 'notes',
)


def _emp_row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {
        'id':              r['id'],
        'matricule':       r.get('matricule') or '',
        'cin':             r.get('cin') or '',
        'full_name':       r['full_name'],
        'email':           r.get('email') or '',
        'phone':           r.get('phone') or '',
        'position':        r.get('position') or '',
        'department_id':   r.get('department_id'),
        'manager_id':      r.get('manager_id'),
        'start_date':      str(r.get('start_date') or '') or None,
        'end_date':        str(r.get('end_date') or '') or None,
        'monthly_salary':  float(r['monthly_salary']) if r.get('monthly_salary') is not None else None,
        'salary_currency': r.get('salary_currency') or 'TND',
        'user_id':         r.get('user_id'),
        'notes':           r.get('notes') or '',
        'active':          bool(r.get('active', True)),
        'created_at':      str(r.get('created_at') or ''),
        'updated_at':      str(r.get('updated_at') or ''),
    }


def strip_sensitive(emp: dict) -> dict:
    """Return a copy of the employee dict with salary fields removed."""
    if not emp:
        return emp
    out = dict(emp)
    out.pop('monthly_salary', None)
    out.pop('salary_currency', None)
    return out


class EmployeeStore:
    def __init__(self, **_):
        pass

    def list_employees(self, include_inactive: bool = False, department_id: Optional[str] = None) -> list:
        sql = """
            SELECT e.*,
                   d.name AS department_name,
                   m.full_name AS manager_name
              FROM employees e
              LEFT JOIN departments d ON d.id = e.department_id
              LEFT JOIN employees   m ON m.id = e.manager_id
        """
        clauses, params = [], []
        if not include_inactive:
            clauses.append("e.active = TRUE")
        if department_id:
            clauses.append("e.department_id = %s"); params.append(department_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY e.active DESC, e.full_name ASC"

        rows = db.query(sql, tuple(params) if params else None)
        result = []
        for r in rows:
            row = _emp_row(r) or {}
            row['department_name'] = r.get('department_name') or ''
            row['manager_name']    = r.get('manager_name') or ''
            result.append(row)
        return result

    def get(self, eid: str) -> Optional[dict]:
        if not eid:
            return None
        return _emp_row(db.one("SELECT * FROM employees WHERE id = %s", (eid,)))

    def add(self, data: dict, created_by: Optional[str] = None) -> dict:
        full_name = (data.get('full_name') or '').strip()
        if not full_name:
            raise ValueError("Nom complet requis.")

        # Soft uniqueness on matricule (when provided)
        matricule = (data.get('matricule') or '').strip()
        if matricule:
            dup = db.one(
                "SELECT id FROM employees WHERE matricule = %s AND active = TRUE",
                (matricule,),
            )
            if dup:
                raise ValueError(f"Un employe actif avec le matricule '{matricule}' existe deja.")

        eid = _new_id()
        db.execute(
            """INSERT INTO employees
                 (id, matricule, cin, full_name, email, phone, position,
                  department_id, manager_id, start_date, end_date,
                  monthly_salary, salary_currency, user_id, notes, active,
                  created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (eid,
             matricule or None,
             (data.get('cin') or '').strip() or None,
             full_name,
             (data.get('email') or '').strip() or None,
             (data.get('phone') or '').strip() or None,
             (data.get('position') or '').strip() or None,
             data.get('department_id') or None,
             data.get('manager_id') or None,
             _date(data.get('start_date')),
             _date(data.get('end_date')),
             _decimal(data.get('monthly_salary')),
             _norm_currency(data.get('salary_currency', 'TND')),
             data.get('user_id') or None,
             (data.get('notes') or '').strip() or None,
             bool(data.get('active', True)),
             created_by,
             datetime.utcnow(),
             datetime.utcnow()),
        )
        return self.get(eid) or {'id': eid}

    def update(self, eid: str, patch: dict) -> Optional[dict]:
        if not eid or not self.get(eid):
            return None
        sets, params = [], []

        for f in _EMP_TEXT_FIELDS:
            if f in patch:
                v = (patch[f] or '').strip()
                if f == 'full_name' and not v:
                    raise ValueError("Nom complet requis.")
                if f == 'matricule' and v:
                    dup = db.one(
                        "SELECT id FROM employees WHERE matricule = %s AND id <> %s AND active = TRUE",
                        (v, eid),
                    )
                    if dup:
                        raise ValueError(f"Un autre employe actif a deja le matricule '{v}'.")
                sets.append(f"{f} = %s")
                params.append(v if v else None)

        for f in ('department_id', 'manager_id', 'user_id'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(patch[f] or None)

        for f in ('start_date', 'end_date'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(_date(patch[f]))

        if 'monthly_salary' in patch:
            sets.append("monthly_salary = %s"); params.append(_decimal(patch['monthly_salary']))
        if 'salary_currency' in patch:
            sets.append("salary_currency = %s"); params.append(_norm_currency(patch['salary_currency']))
        if 'active' in patch:
            sets.append("active = %s"); params.append(bool(patch['active']))

        if not sets:
            return self.get(eid)
        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(eid)
        db.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get(eid)

    def deactivate(self, eid: str) -> bool:
        return db.execute(
            "UPDATE employees SET active = FALSE, end_date = COALESCE(end_date, %s), updated_at = %s WHERE id = %s",
            (datetime.utcnow().date(), datetime.utcnow(), eid),
        ) > 0

    def delete(self, eid: str) -> bool:
        return db.execute("DELETE FROM employees WHERE id = %s", (eid,)) > 0
