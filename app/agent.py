"""DeskVoice orchestrator.

Two brains share the same tools and safety rules:
- GeminiBrain: Gemini with function calling decides when to search, propose a ticket or a workflow.
- DemoBrain: a rule-based fallback so the prototype runs without any API key.

Safety rule for both: tools may only *propose* an action. Nothing is created until the
employee confirms ("yes" / "haan"), and the confirmation is handled here in plain code,
not by the model.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import config
from .knowledge import Doc, LocalKnowledge
from .store import CATEGORIES, PRIORITIES, ItsmStore

log = logging.getLogger("deskvoice")

YES = re.compile(r"^\s*(yes|yeah|yep|haan?|ha|han|ji|ok|okay|sure|confirm|go ahead|do it|kar do|theek hai|thik hai|please do)\b",
                 re.I)
NO = re.compile(r"^\s*(no|nope|nahi|nahin|mat|cancel|stop|ruk|rehne do|don't)\b", re.I)
NOT_FIXED = re.compile(r"(didn'?t work|did not work|not fixed|still (not|the same|stuck|happening)|abhi bhi|ab bhi|"
                       r"nahi hua|nahi hui|same problem|doesn'?t help)", re.I)
TICKET_ASK = re.compile(r"(raise|create|open|log|make) (a )?ticket|ticket (bana|banao|raise|khol)", re.I)
STATUS = re.compile(r"(status|update|kya hua|what'?s happening|kahan tak).*(ticket|request)|"
                    r"(ticket|request).*(status|update|kya hua)|\bTKT-\d+", re.I)
TICKET_ID = re.compile(r"TKT-\d+", re.I)

WORKFLOWS = {
    "password_reset": "Password reset",
    "account_unlock": "Account unlock",
    "access_request": "Access request",
}

CATEGORY_WORDS = {
    "Messaging": ["outlook", "email", "mail", "inbox", "calendar"],
    "Network": ["vpn", "wifi", "wi-fi", "internet", "network", "lan"],
    "Identity & Access": ["password", "login", "sign in", "mfa", "authenticator", "locked", "access", "permission"],
    "End-user Devices": ["printer", "laptop", "keyboard", "mouse", "monitor", "battery", "screen"],
    "Applications": ["teams", "onedrive", "excel", "word", "software", "install", "app"],
}
HIGH_WORDS = ["urgent", "asap", "everyone", "whole team", "all users", "can't work", "cannot work", "down", "outage",
              "client meeting", "jaldi"]
LOW_WORDS = ["slow", "sometimes", "minor", "when you can"]


def triage(text: str, hint: str | None = None) -> tuple[str, str]:
    t = text.lower()
    category = hint if hint in CATEGORIES else next(
        (c for c, words in CATEGORY_WORDS.items() if any(w in t for w in words)), "General")
    priority = "High" if any(w in t for w in HIGH_WORDS) else "Low" if any(w in t for w in LOW_WORDS) else "Medium"
    return category, priority


def detect_workflow(text: str) -> tuple[str, str] | None:
    t = text.lower()
    if re.search(r"\b(unlock|locked out|account (is )?locked|account band)", t):
        return "account_unlock", "my account"
    if re.search(r"(forgot|reset|expired|bhool).{0,25}password|password.{0,25}(forgot|reset|expired|bhool)", t):
        return "password_reset", "my account"
    m = re.search(r"(?:need|want|request|get|chahiye|de do|give me)\s.*?access\s+(?:to|for)?\s*(?:the\s+)?(.+)", t) or \
        re.search(r"access\s+(?:to|for)\s+(?:the\s+)?(.+?)(?:\s+(?:chahiye|please|pls)|[.?!]|$)", t) or         re.search(r"(?:mujhe\s+)?(.+?)\s+(?:ka|ki|ke)\s+access", t)
    if m:
        target = re.sub(r"\b(chahiye|please|pls|de do)\b", "", m.group(1)).strip(" .?!") or "shared folder"
        return "access_request", target[:80]
    return None


def source_ref(d: Doc) -> dict:
    return {"id": d.id, "title": d.title, "kind": d.kind}


@dataclass
class Session:
    sid: str
    requester: str = "Employee"
    lang: str = "en"
    user_msgs: list[str] = field(default_factory=list)
    last_issue: str | None = None
    last_steps: list[str] = field(default_factory=list)
    last_sources: list[dict] = field(default_factory=list)
    pending: dict | None = None
    notes: list[str] = field(default_factory=list)  # facts to pass to Gemini on the next turn
    chat: object | None = None


class DeskVoice:
    def __init__(self, kb: LocalKnowledge, store: ItsmStore):
        self.kb = kb
        self.store = store
        self.sessions: dict[str, Session] = {}
        self.client = None
        if config.gemini_enabled():
            try:
                from google import genai
                self.client = (genai.Client(vertexai=True, project=config.GOOGLE_CLOUD_PROJECT,
                                            location=config.GOOGLE_CLOUD_LOCATION)
                               if config.USE_VERTEX else genai.Client(api_key=config.GEMINI_API_KEY))
            except Exception as e:  # pragma: no cover - depends on environment
                log.warning("Gemini disabled: %s", e)

    @property
    def mode(self) -> str:
        return "gemini" if self.client else "demo"

    def session(self, sid: str, requester: str = "Employee", lang: str = "en") -> Session:
        s = self.sessions.get(sid)
        if not s:
            s = self.sessions[sid] = Session(sid=sid)
        s.requester = requester or s.requester
        s.lang = lang or s.lang
        return s

    # ------------------------------------------------------------------ public API
    def handle(self, sid: str, text: str, requester: str = "Employee", lang: str = "en") -> dict:
        s = self.session(sid, requester, lang)
        text = text.strip()[:1000]
        s.user_msgs.append(text)

        # 1. A pending action is confirmed or cancelled in code, never by the model.
        if s.pending:
            if YES.search(text):
                return self._execute_pending(s)
            if NO.search(text):
                s.pending = None
                s.notes.append("The user cancelled the proposed action.")
                return self._reply("Okay, I won't do that. Is there anything else I can help with?")
            s.pending = None  # user moved on to something else

        # 2. Let Gemini handle the turn; fall back to rules if it is off or fails.
        if self.client:
            try:
                return self._gemini_turn(s, text)
            except Exception as e:  # pragma: no cover - network dependent
                log.exception("Gemini turn failed, using demo brain: %s", e)
        return self._demo_turn(s, text)

    def feedback(self, sid: str, fixed: bool) -> dict:
        s = self.session(sid)
        if fixed:
            self.store.set_outcome(sid, "self_resolved")
            s.notes.append("The user said the fix worked.")
            return self._reply("Great, glad that worked! I've noted it as solved.")
        return self._propose_ticket(s, s.last_issue or " ".join(s.user_msgs[-2:]),
                                    intro="Sorry that didn't fix it.")

    # ------------------------------------------------------------------ shared tools
    def tool_search(self, s: Session, query: str) -> list[dict]:
        results = self.kb.search(query, k=3)
        out = []
        for d, score in results:
            out.append({"id": d.id, "title": d.title, "kind": d.kind, "category": d.category,
                        "steps": d.steps[:6], "text": d.body[:600] if not d.steps else "",
                        "raise_ticket_if": d.escalate_if, "score": score})
        if results:
            s.last_issue = s.user_msgs[-1] if s.user_msgs else query
            s.last_sources = [source_ref(d) for d, _ in results[:2]]
            s.last_steps = results[0][0].steps
        return out

    def _propose_ticket(self, s: Session, issue: str, summary: str | None = None, category: str | None = None,
                        priority: str | None = None, intro: str = "") -> dict:
        cat, pri = triage(issue, category)
        if priority in PRIORITIES:
            pri = priority
        summary = (summary or issue).strip().rstrip(".")[:160]
        s.pending = {"kind": "ticket", "summary": summary, "category": cat, "priority": pri, "issue": issue}
        msg = (f"{intro} I can raise a ticket for you: \"{summary}\", category {cat}, priority {pri}. "
               f"I'll include the steps you already tried. Shall I create it?").strip()
        return self._reply(msg, pending=self._pending_view(s))

    def _propose_workflow(self, s: Session, kind: str, target: str) -> dict:
        name = WORKFLOWS[kind]
        s.pending = {"kind": "workflow", "workflow": kind, "target": target}
        what = f"for {target}" if kind == "access_request" else "for your account"
        extra = " Your manager will be asked to approve it." if kind == "access_request" else \
            " A reset link will go to your registered phone."
        article = "an" if name[0].lower() in "aeiou" else "a"
        return self._reply(f"I can start {article} {name.lower()} {what}.{extra} Shall I go ahead?",
                           pending=self._pending_view(s))

    def _pending_view(self, s: Session) -> dict:
        p = s.pending
        if p["kind"] == "ticket":
            return {"kind": "ticket", "title": "Create ticket", "lines": [
                f"Summary: {p['summary']}", f"Category: {p['category']}", f"Priority: {p['priority']}"]}
        return {"kind": "workflow", "title": WORKFLOWS[p["workflow"]], "lines": [f"For: {p['target']}"]}

    def _execute_pending(self, s: Session) -> dict:
        p, s.pending = s.pending, None
        if p["kind"] == "ticket":
            details = {"issue": p["issue"], "steps_already_tried": s.last_steps,
                       "kb_shown": [x["id"] for x in s.last_sources], "recent_messages": s.user_msgs[-6:],
                       "language": s.lang, "reported_via": "DeskVoice"}
            t = self.store.create_ticket(s.requester, p["summary"], p["category"], p["priority"], details)
            self.store.set_outcome(s.sid, "ticket", t["id"])
            s.notes.append(f"Ticket {t['id']} was created and assigned to {t['team']}.")
            return self._reply(f"Done. Ticket {t['id']} is created and assigned to {t['team']} with "
                               f"{t['priority'].lower()} priority. You can ask me for its status anytime.", ticket=t)
        wf = self.store.create_workflow(s.requester, p["workflow"], p["target"])
        self.store.set_outcome(s.sid, "workflow")
        s.notes.append(f"Workflow {wf['id']} ({WORKFLOWS[p['workflow']]}) was submitted.")
        done = ("Your manager has been asked to approve it." if p["workflow"] == "access_request"
                else "Check your registered phone for the reset link.")
        return self._reply(f"Done. {WORKFLOWS[p['workflow']]} {wf['id']} has started. {done}", workflow=wf)

    def tool_status(self, s: Session, ticket_id: str | None) -> dict:
        if ticket_id:
            t = self.store.get_ticket(ticket_id)
            if not t:
                return {"found": False, "ticket_id": ticket_id.upper()}
            return {"found": True, "id": t["id"], "summary": t["summary"], "status": t["status"], "team": t["team"],
                    "priority": t["priority"], "resolution": t["resolution"]}
        return {"found": True, "tickets": self.store.tickets_for(s.requester)[:5]}

    @staticmethod
    def _reply(text: str, **extra) -> dict:
        base = {"reply": text, "sources": [], "steps": [], "pending": None, "ticket": None, "workflow": None,
                "ask_feedback": False}
        base.update(extra)
        return base

    # ------------------------------------------------------------------ demo brain
    def _demo_turn(self, s: Session, text: str) -> dict:
        if STATUS.search(text):
            m = TICKET_ID.search(text)
            st = self.tool_status(s, m.group(0) if m else None)
            if m and not st["found"]:
                return self._reply(f"I couldn't find ticket {st['ticket_id']}. Please check the number.")
            if m:
                res = f" Resolution: {st['resolution']}" if st.get("resolution") else ""
                return self._reply(f"Ticket {st['id']} ({st['summary']}) is {st['status']}, with {st['team']}.{res}")
            if not st["tickets"]:
                return self._reply("You don't have any tickets yet.")
            lines = [f"{t['id']}: {t['summary']} ({t['status']})" for t in st["tickets"]]
            return self._reply("Here are your latest tickets.", steps=lines)

        wf = detect_workflow(text)
        if wf:
            return self._propose_workflow(s, *wf)

        if TICKET_ASK.search(text) or (NOT_FIXED.search(text) and s.last_issue):
            issue = s.last_issue or text
            intro = "Sorry that didn't fix it." if NOT_FIXED.search(text) else ""
            return self._propose_ticket(s, issue, intro=intro)

        results = self.tool_search(s, text)
        if not results:
            s.last_issue, s.last_steps, s.last_sources = text, [], []
            return self._propose_ticket(s, text, intro="I couldn't find a known fix for that.")

        top = results[0]
        sources = s.last_sources
        if top["kind"] == "ticket":
            body = top["text"].split("Resolution:", 1)[-1].strip()
            return self._reply(f"This looks like a problem we've solved before ({top['id']}). What fixed it: {body}",
                               sources=sources, ask_feedback=True)
        return self._reply(f"Here's what usually fixes this ({top['id']}):", steps=top["steps"], sources=sources,
                           ask_feedback=True)

    # ------------------------------------------------------------------ gemini brain
    SYSTEM = (
        "You are DeskVoice, a friendly voice IT helpdesk agent for company employees.\n"
        "Rules:\n"
        "- For any IT problem, call search_knowledge first and answer ONLY from what it returns. "
        "Mention the source id (e.g. KB-1001) once. If nothing relevant is found, say so and call propose_ticket.\n"
        "- Replies are spoken aloud: keep them under 80 words. Give at most 5 short numbered steps.\n"
        "- Reply in the user's language and style. If they write Hinglish, reply in Hinglish using Latin script.\n"
        "- For password reset, account unlock or access requests call propose_workflow.\n"
        "- If the user says a fix did not work, or asks for a ticket, call propose_ticket with a clear one-line summary.\n"
        "- You can never create anything yourself. After a propose_* call, ask the user to confirm with yes or no. "
        "Never say an action is done unless a system note says so.\n"
        f"- Ticket categories: {', '.join(CATEGORIES)}. Priorities: {', '.join(PRIORITIES)}. Use High only when work "
        "is blocked for many people or something urgent is at stake.\n"
        "- Use get_ticket_status for questions about existing tickets.\n"
    )

    def _gemini_tools(self, s: Session):
        agent = self

        def search_knowledge(query: str) -> list[dict]:
            """Search the company's IT knowledge base and past resolved tickets.

            Args:
                query: The employee's problem in a few English keywords.
            """
            return agent.tool_search(s, query)

        def propose_ticket(summary: str, category: str, priority: str) -> dict:
            """Propose a helpdesk ticket. It is only created after the employee confirms.

            Args:
                summary: One-line summary of the problem.
                category: One of the allowed ticket categories.
                priority: Low, Medium or High.
            """
            issue = s.last_issue or (s.user_msgs[-1] if s.user_msgs else summary)
            agent._propose_ticket(s, issue, summary=summary, category=category, priority=priority)
            return {"status": "awaiting_user_confirmation", "proposal": s.pending}

        def propose_workflow(kind: str, target: str) -> dict:
            """Propose a self-service workflow. It only runs after the employee confirms.

            Args:
                kind: password_reset, account_unlock or access_request.
                target: For access_request, the drive, folder or site. Otherwise "my account".
            """
            if kind not in WORKFLOWS:
                return {"error": f"Unknown workflow. Use one of {list(WORKFLOWS)}"}
            agent._propose_workflow(s, kind, target or "my account")
            return {"status": "awaiting_user_confirmation", "proposal": s.pending}

        def get_ticket_status(ticket_id: str = "") -> dict:
            """Get the status of one ticket (e.g. TKT-1003), or the employee's latest tickets if no id is given.

            Args:
                ticket_id: Ticket number, or empty for the employee's recent tickets.
            """
            return agent.tool_status(s, ticket_id or None)

        return [search_knowledge, propose_ticket, propose_workflow, get_ticket_status]

    def _gemini_turn(self, s: Session, text: str) -> dict:
        from google.genai import types

        if s.chat is None:
            s.chat = self.client.chats.create(
                model=config.GEMINI_MODEL,
                config=types.GenerateContentConfig(system_instruction=self.SYSTEM, tools=self._gemini_tools(s),
                                                   temperature=0.3))
        before_sources = list(s.last_sources)
        s.pending = None
        msg = text
        if s.notes:
            msg = "[System notes: " + " ".join(s.notes) + "]\n" + text
            s.notes.clear()
        resp = s.chat.send_message(msg)
        reply = (resp.text or "").strip() or "Sorry, I didn't catch that. Could you say it again?"
        searched = s.last_sources != before_sources or (s.last_sources and "KB-" in reply)
        return self._reply(reply, sources=s.last_sources if searched else [],
                           pending=self._pending_view(s) if s.pending else None,
                           ask_feedback=bool(searched and not s.pending))
