"""SQLite store for tickets, workflow requests, conversation metrics and draft articles.

This stands in for the company's ITSM (ServiceNow / Jira / own ITSM via MCP). The
agent only talks to it through `ItsmStore`, so a real adapter can replace it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

CATEGORIES = ["Messaging", "Network", "Identity & Access", "End-user Devices", "Applications", "General"]
TEAMS = {
    "Messaging": "Messaging & Collaboration",
    "Network": "Network Operations",
    "Identity & Access": "Identity & Access",
    "End-user Devices": "Desktop Support",
    "Applications": "Application Support",
    "General": "Service Desk L1",
}
PRIORITIES = ["Low", "Medium", "High"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE, created_at REAL, requester TEXT, summary TEXT, category TEXT,
  priority TEXT, team TEXT, details TEXT, status TEXT, resolution TEXT, resolved_at REAL
);
CREATE TABLE IF NOT EXISTS workflows (
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE, created_at REAL, requester TEXT, kind TEXT, target TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, started_at REAL, requester TEXT, first_answer_ms INTEGER,
  outcome TEXT, ticket_id TEXT, lang TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE, created_at REAL, ticket_id TEXT, title TEXT, category TEXT, body TEXT, status TEXT
);
"""


class ItsmStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return [dict(r) for r in cur.fetchall()]

    # ---------- tickets ----------
    def create_ticket(self, requester: str, summary: str, category: str, priority: str, details: dict) -> dict:
        category = category if category in CATEGORIES else "General"
        priority = priority if priority in PRIORITIES else "Medium"
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO tickets (created_at, requester, summary, category, priority, team, details, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), requester, summary[:200], category, priority, TEAMS[category], json.dumps(details), "New"))
            tid = f"TKT-{1000 + cur.lastrowid}"
            self._db.execute("UPDATE tickets SET id=? WHERE n=?", (tid, cur.lastrowid))
            self._db.commit()
        return self.get_ticket(tid)

    def get_ticket(self, ticket_id: str) -> dict | None:
        rows = self._q("SELECT * FROM tickets WHERE id=?", (ticket_id.upper(),))
        if not rows:
            return None
        t = rows[0]
        t["details"] = json.loads(t["details"] or "{}")
        return t

    def tickets_for(self, requester: str) -> list[dict]:
        return self._q("SELECT id, summary, status, team, priority FROM tickets WHERE requester=? ORDER BY n DESC",
                       (requester,))

    def list_tickets(self) -> list[dict]:
        rows = self._q("SELECT * FROM tickets ORDER BY n DESC")
        for t in rows:
            t["details"] = json.loads(t["details"] or "{}")
        return rows

    def resolve_ticket(self, ticket_id: str, resolution: str) -> dict | None:
        self._q("UPDATE tickets SET status='Resolved', resolution=?, resolved_at=? WHERE id=?",
                (resolution, time.time(), ticket_id))
        return self.get_ticket(ticket_id)

    # ---------- workflows ----------
    def create_workflow(self, requester: str, kind: str, target: str) -> dict:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO workflows (created_at, requester, kind, target, status) VALUES (?,?,?,?,?)",
                (time.time(), requester, kind, target, "Submitted"))
            wid = f"WF-{2000 + cur.lastrowid}"
            self._db.execute("UPDATE workflows SET id=? WHERE n=?", (wid, cur.lastrowid))
            self._db.commit()
        return self._q("SELECT * FROM workflows WHERE id=?", (wid,))[0]

    # ---------- sessions / metrics ----------
    def touch_session(self, sid: str, requester: str, lang: str) -> None:
        self._q("INSERT OR IGNORE INTO sessions (id, started_at, requester, lang) VALUES (?,?,?,?)",
                (sid, time.time(), requester, lang))

    def set_first_answer(self, sid: str, ms: int) -> None:
        self._q("UPDATE sessions SET first_answer_ms=? WHERE id=? AND first_answer_ms IS NULL", (ms, sid))

    def set_outcome(self, sid: str, outcome: str, ticket_id: str | None = None) -> None:
        # outcome: "self_resolved" | "ticket" | "workflow"
        # A conversation that needed a ticket stays counted as "ticket", even if a later request was self-served.
        self._q("UPDATE sessions SET outcome=CASE WHEN outcome='ticket' THEN 'ticket' ELSE ? END, "
                "ticket_id=COALESCE(?, ticket_id) WHERE id=?", (outcome, ticket_id, sid))

    def metrics(self) -> dict:
        s = self._q("SELECT COUNT(*) total, "
                    "SUM(outcome='self_resolved') solved, SUM(outcome='workflow') wf, SUM(outcome='ticket') tk, "
                    "AVG(first_answer_ms) avg_ms FROM sessions")[0]
        decided = (s["solved"] or 0) + (s["wf"] or 0) + (s["tk"] or 0)
        top = self._q("SELECT category, COUNT(*) c FROM tickets GROUP BY category ORDER BY c DESC LIMIT 5")
        return {
            "conversations": s["total"] or 0,
            "self_resolution_rate": round(100 * ((s["solved"] or 0) + (s["wf"] or 0)) / decided) if decided else None,
            "avg_first_answer_ms": round(s["avg_ms"]) if s["avg_ms"] else None,
            "tickets_created": len(self._q("SELECT id FROM tickets")),
            "open_tickets": len(self._q("SELECT id FROM tickets WHERE status!='Resolved'")),
            "top_categories": top,
        }

    # ---------- draft knowledge articles ----------
    def create_draft(self, ticket: dict, title: str, body: str) -> dict:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO drafts (created_at, ticket_id, title, category, body, status) VALUES (?,?,?,?,?,?)",
                (time.time(), ticket["id"], title, ticket["category"], body, "Draft"))
            did = f"KB-D{100 + cur.lastrowid}"
            self._db.execute("UPDATE drafts SET id=? WHERE n=?", (did, cur.lastrowid))
            self._db.commit()
        return self._q("SELECT * FROM drafts WHERE id=?", (did,))[0]

    def list_drafts(self) -> list[dict]:
        return self._q("SELECT * FROM drafts ORDER BY n DESC")

    def approve_draft(self, draft_id: str) -> dict | None:
        self._q("UPDATE drafts SET status='Approved' WHERE id=?", (draft_id,))
        rows = self._q("SELECT * FROM drafts WHERE id=?", (draft_id,))
        return rows[0] if rows else None

    def approved_drafts(self) -> list[dict]:
        return self._q("SELECT * FROM drafts WHERE status='Approved'")
