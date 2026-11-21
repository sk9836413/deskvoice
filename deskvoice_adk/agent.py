"""DeskVoice as an ADK agent.

Same knowledge base, ticket store and safety rules as the web app, packaged so it can run
with `adk web`, be deployed to Agent Runtime, and be registered in Gemini Enterprise.

Run locally:  adk web   (from the repo root, with GOOGLE_API_KEY set)
"""
from __future__ import annotations

import os

from google.adk.agents import Agent

from app import config
from app.agent import WORKFLOWS, triage
from app.knowledge import LocalKnowledge
from app.store import CATEGORIES, PRIORITIES, ItsmStore

_kb = LocalKnowledge(config.DATA_DIR)
_store = ItsmStore(config.DB_PATH)
_requester = os.getenv("DESKVOICE_ADK_REQUESTER", "Employee")


def search_knowledge(query: str) -> list[dict]:
    """Search the company's IT knowledge base and past resolved tickets.

    Args:
        query: The employee's problem in a few English keywords.

    Returns:
        Up to 3 matches with id, title, steps and when to raise a ticket.
    """
    return [{"id": d.id, "title": d.title, "kind": d.kind, "category": d.category, "steps": d.steps[:6],
             "text": "" if d.steps else d.body[:600], "raise_ticket_if": d.escalate_if}
            for d, _ in _kb.search(query, k=3)]


def create_ticket(summary: str, details: str, category: str, priority: str, confirmed: bool) -> dict:
    """Create a helpdesk ticket. Only call with confirmed=True after the employee said yes.

    Args:
        summary: One-line summary of the problem.
        details: What the employee reported and which steps they already tried.
        category: One of Messaging, Network, Identity & Access, End-user Devices, Applications, General.
        priority: Low, Medium or High.
        confirmed: True only if the employee explicitly confirmed creating this ticket.
    """
    if not confirmed:
        return {"status": "not_created", "reason": "Ask the employee to confirm first."}
    cat, pri = triage(f"{summary} {details}", category if category in CATEGORIES else None)
    t = _store.create_ticket(_requester, summary, cat, priority if priority in PRIORITIES else pri,
                             {"issue": details, "reported_via": "DeskVoice ADK"})
    return {"status": "created", "id": t["id"], "team": t["team"], "priority": t["priority"]}


def run_workflow(kind: str, target: str, confirmed: bool) -> dict:
    """Start a self-service workflow. Only call with confirmed=True after the employee said yes.

    Args:
        kind: password_reset, account_unlock or access_request.
        target: For access_request the drive, folder or site; otherwise "my account".
        confirmed: True only if the employee explicitly confirmed.
    """
    if kind not in WORKFLOWS:
        return {"status": "error", "reason": f"Unknown workflow, use one of {list(WORKFLOWS)}"}
    if not confirmed:
        return {"status": "not_started", "reason": "Ask the employee to confirm first."}
    wf = _store.create_workflow(_requester, kind, target or "my account")
    return {"status": "started", "id": wf["id"], "kind": WORKFLOWS[kind]}


def get_ticket_status(ticket_id: str) -> dict:
    """Get the status of a ticket such as TKT-1003.

    Args:
        ticket_id: The ticket number.
    """
    t = _store.get_ticket(ticket_id)
    if not t:
        return {"found": False}
    return {"found": True, "id": t["id"], "summary": t["summary"], "status": t["status"], "team": t["team"],
            "resolution": t["resolution"]}


root_agent = Agent(
    name="deskvoice",
    model=config.GEMINI_MODEL,
    description="Voice IT helpdesk agent: answers IT questions from company knowledge, raises tickets and runs "
                "approved self-service workflows.",
    instruction=(
        "You are DeskVoice, a friendly IT helpdesk agent for employees. For any IT problem, call search_knowledge "
        "first and answer only from its results, citing the article id once. Keep replies short (under 80 words, at "
        "most 5 numbered steps) because they are spoken aloud. Reply in the user's language; for Hinglish use Latin "
        "script. If nothing relevant is found, or the user says the fix did not work, offer to raise a ticket. "
        "Before create_ticket or run_workflow, describe exactly what you will do and ask the user to confirm; only "
        "call the tool with confirmed=True after they say yes. Use get_ticket_status for status questions."
    ),
    tools=[search_knowledge, create_ticket, run_workflow, get_ticket_status],
)
