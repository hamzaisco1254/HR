"""
Project store + project assignments — Postgres-backed.

Projects are revenue-bearing engagements linked to a single client.
Assignments are an N:M relationship between employees and projects,
with a percentage allocation for cost-tracking later.

Schema created by db_schema.py.
"""
import uuid
from datetime import datetime
from typing import Optional

import db


VALID_CURRENCIES = ('TND', 'EUR', 'USD', 'GBP', 'CHF')
VALID_STATUSES   = ('active', 'paused', 'closed')


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _norm_currency(value: str) -> str:
    code = (value or '').strip().upper()
    return code if code in VALID_CURRENCIES else 'EUR'


def _norm_status(value: str) -> str:
    v = (value or '').strip().lower()
    return v if v in VALID_STATUSES else 'active'


def _date(value):
    if value in (None, ''):
        return None
    return value


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _proj_row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {
        'id':                r['id'],
        'code':              r.get('code') or '',
        'name':              r['name'],
        'client_id':         r.get('client_id'),
        'description':       r.get('description') or '',
        'start_date':        str(r.get('start_date') or '') or None,
        'end_date':          str(r.get('end_date') or '') or None,
        'status':            r.get('status') or 'active',
        'contract_value':    float(r['contract_value']) if r.get('contract_value') is not None else None,
        'contract_currency': r.get('contract_currency') or 'EUR',
        'notes':             r.get('notes') or '',
        'created_at':        str(r.get('created_at') or ''),
        'updated_at':        str(r.get('updated_at') or ''),
    }


def _assignment_row(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {
        'id':              r['id'],
        'project_id':      r['project_id'],
        'employee_id':     r['employee_id'],
        'allocation_pct':  float(r['allocation_pct']),
        'role':            r.get('role') or '',
        'start_date':      str(r.get('start_date') or '') or None,
        'end_date':        str(r.get('end_date') or '') or None,
        'hourly_rate':     float(r['hourly_rate']) if r.get('hourly_rate') is not None else None,
        'rate_currency':   r.get('rate_currency') or 'EUR',
        'notes':           r.get('notes') or '',
        'created_at':      str(r.get('created_at') or ''),
    }


class ProjectStore:
    def __init__(self, **_):
        pass

    # ─── projects ────────────────────────────────────────────────

    def list_projects(self, status: Optional[str] = None, client_id: Optional[str] = None) -> list:
        sql = """
            SELECT p.*,
                   c.name AS client_name,
                   c.country AS client_country,
                   (SELECT COUNT(*) FROM project_assignments WHERE project_id = p.id) AS assignment_count
              FROM projects p
              LEFT JOIN clients c ON c.id = p.client_id
        """
        clauses, params = [], []
        if status:
            clauses.append("p.status = %s"); params.append(status)
        if client_id:
            clauses.append("p.client_id = %s"); params.append(client_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY p.status ASC, p.created_at DESC"

        rows = db.query(sql, tuple(params) if params else None)
        result = []
        for r in rows:
            row = _proj_row(r) or {}
            row['client_name']      = r.get('client_name') or ''
            row['client_country']   = r.get('client_country') or ''
            row['assignment_count'] = int(r.get('assignment_count') or 0)
            result.append(row)
        return result

    def get(self, pid: str) -> Optional[dict]:
        if not pid:
            return None
        return _proj_row(db.one("SELECT * FROM projects WHERE id = %s", (pid,)))

    def add(self, data: dict, created_by: Optional[str] = None) -> dict:
        name = (data.get('name') or '').strip()
        if not name:
            raise ValueError("Nom du projet requis.")
        client_id = (data.get('client_id') or '').strip()
        if not client_id:
            raise ValueError("Client requis.")
        # Verify client exists
        if not db.one("SELECT 1 FROM clients WHERE id = %s", (client_id,)):
            raise ValueError("Client introuvable.")

        code = (data.get('code') or '').strip()
        if code:
            dup = db.one("SELECT id FROM projects WHERE code = %s", (code,))
            if dup:
                raise ValueError(f"Un projet avec le code '{code}' existe deja.")

        pid = _new_id()
        db.execute(
            """INSERT INTO projects
                 (id, code, name, client_id, description, start_date, end_date,
                  status, contract_value, contract_currency, notes,
                  created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (pid, code or None, name, client_id,
             (data.get('description') or '').strip() or None,
             _date(data.get('start_date')),
             _date(data.get('end_date')),
             _norm_status(data.get('status', 'active')),
             _decimal(data.get('contract_value')),
             _norm_currency(data.get('contract_currency', 'EUR')),
             (data.get('notes') or '').strip() or None,
             created_by,
             datetime.utcnow(),
             datetime.utcnow()),
        )
        return self.get(pid) or {'id': pid}

    def update(self, pid: str, patch: dict) -> Optional[dict]:
        if not pid or not self.get(pid):
            return None
        sets, params = [], []

        if 'name' in patch:
            v = (patch['name'] or '').strip()
            if not v:
                raise ValueError("Nom du projet requis.")
            sets.append("name = %s"); params.append(v)

        if 'code' in patch:
            v = (patch['code'] or '').strip()
            if v:
                dup = db.one("SELECT id FROM projects WHERE code = %s AND id <> %s", (v, pid))
                if dup:
                    raise ValueError(f"Un autre projet utilise deja le code '{v}'.")
            sets.append("code = %s"); params.append(v or None)

        if 'client_id' in patch:
            v = (patch['client_id'] or '').strip()
            if not v or not db.one("SELECT 1 FROM clients WHERE id = %s", (v,)):
                raise ValueError("Client introuvable.")
            sets.append("client_id = %s"); params.append(v)

        if 'description' in patch:
            sets.append("description = %s"); params.append((patch['description'] or '').strip() or None)
        if 'notes' in patch:
            sets.append("notes = %s"); params.append((patch['notes'] or '').strip() or None)

        for f in ('start_date', 'end_date'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(_date(patch[f]))

        if 'status' in patch:
            sets.append("status = %s"); params.append(_norm_status(patch['status']))
        if 'contract_value' in patch:
            sets.append("contract_value = %s"); params.append(_decimal(patch['contract_value']))
        if 'contract_currency' in patch:
            sets.append("contract_currency = %s"); params.append(_norm_currency(patch['contract_currency']))

        if not sets:
            return self.get(pid)
        sets.append("updated_at = %s"); params.append(datetime.utcnow())
        params.append(pid)
        db.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get(pid)

    def delete(self, pid: str) -> bool:
        # Assignments cascade via FK
        return db.execute("DELETE FROM projects WHERE id = %s", (pid,)) > 0

    # ─── assignments ─────────────────────────────────────────────

    def list_assignments(self, project_id: Optional[str] = None, employee_id: Optional[str] = None) -> list:
        sql = """
            SELECT a.*,
                   e.full_name AS employee_name,
                   p.name AS project_name,
                   p.code AS project_code
              FROM project_assignments a
              LEFT JOIN employees e ON e.id = a.employee_id
              LEFT JOIN projects  p ON p.id = a.project_id
        """
        clauses, params = [], []
        if project_id:
            clauses.append("a.project_id = %s"); params.append(project_id)
        if employee_id:
            clauses.append("a.employee_id = %s"); params.append(employee_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY a.start_date DESC"

        rows = db.query(sql, tuple(params) if params else None)
        result = []
        for r in rows:
            a = _assignment_row(r) or {}
            a['employee_name'] = r.get('employee_name') or ''
            a['project_name']  = r.get('project_name') or ''
            a['project_code']  = r.get('project_code') or ''
            result.append(a)
        return result

    def add_assignment(self, data: dict, created_by: Optional[str] = None) -> dict:
        project_id  = (data.get('project_id') or '').strip()
        employee_id = (data.get('employee_id') or '').strip()
        if not project_id or not db.one("SELECT 1 FROM projects WHERE id = %s", (project_id,)):
            raise ValueError("Projet introuvable.")
        if not employee_id or not db.one("SELECT 1 FROM employees WHERE id = %s", (employee_id,)):
            raise ValueError("Employe introuvable.")

        try:
            pct = float(data.get('allocation_pct'))
        except (TypeError, ValueError):
            raise ValueError("Pourcentage d'allocation invalide.")
        if pct < 0 or pct > 100:
            raise ValueError("Le pourcentage d'allocation doit etre entre 0 et 100.")

        start_date = _date(data.get('start_date'))
        if not start_date:
            raise ValueError("Date de debut requise.")

        aid = _new_id()
        db.execute(
            """INSERT INTO project_assignments
                 (id, project_id, employee_id, allocation_pct, role,
                  start_date, end_date, hourly_rate, rate_currency, notes,
                  created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (aid, project_id, employee_id, pct,
             (data.get('role') or '').strip() or None,
             start_date,
             _date(data.get('end_date')),
             _decimal(data.get('hourly_rate')),
             _norm_currency(data.get('rate_currency', 'EUR')),
             (data.get('notes') or '').strip() or None,
             created_by,
             datetime.utcnow()),
        )
        return self.get_assignment(aid) or {'id': aid}

    def get_assignment(self, aid: str) -> Optional[dict]:
        if not aid:
            return None
        row = db.one(
            """SELECT a.*, e.full_name AS employee_name, p.name AS project_name, p.code AS project_code
                 FROM project_assignments a
                 LEFT JOIN employees e ON e.id = a.employee_id
                 LEFT JOIN projects  p ON p.id = a.project_id
                WHERE a.id = %s""",
            (aid,),
        )
        if not row:
            return None
        a = _assignment_row(row) or {}
        a['employee_name'] = row.get('employee_name') or ''
        a['project_name']  = row.get('project_name') or ''
        a['project_code']  = row.get('project_code') or ''
        return a

    def update_assignment(self, aid: str, patch: dict) -> Optional[dict]:
        if not aid or not self.get_assignment(aid):
            return None
        sets, params = [], []

        if 'allocation_pct' in patch:
            try:
                pct = float(patch['allocation_pct'])
            except (TypeError, ValueError):
                raise ValueError("Pourcentage invalide.")
            if pct < 0 or pct > 100:
                raise ValueError("Le pourcentage doit etre entre 0 et 100.")
            sets.append("allocation_pct = %s"); params.append(pct)

        if 'role' in patch:
            sets.append("role = %s"); params.append((patch['role'] or '').strip() or None)
        if 'notes' in patch:
            sets.append("notes = %s"); params.append((patch['notes'] or '').strip() or None)
        for f in ('start_date', 'end_date'):
            if f in patch:
                sets.append(f"{f} = %s"); params.append(_date(patch[f]))
        if 'hourly_rate' in patch:
            sets.append("hourly_rate = %s"); params.append(_decimal(patch['hourly_rate']))
        if 'rate_currency' in patch:
            sets.append("rate_currency = %s"); params.append(_norm_currency(patch['rate_currency']))

        if not sets:
            return self.get_assignment(aid)
        params.append(aid)
        db.execute(f"UPDATE project_assignments SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return self.get_assignment(aid)

    def delete_assignment(self, aid: str) -> bool:
        return db.execute("DELETE FROM project_assignments WHERE id = %s", (aid,)) > 0
