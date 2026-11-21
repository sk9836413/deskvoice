"""FastAPI app: employee chat API, admin API and the static web UI."""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .agent import DeskVoice
from .knowledge import Doc, LocalKnowledge
from .store import ItsmStore

STATIC = config.ROOT / "static"


class ChatIn(BaseModel):
    session_id: str | None = None
    text: str = Field(min_length=1, max_length=1000)
    requester: str = Field(default="Employee", max_length=60)
    lang: str = Field(default="en", max_length=10)


class FeedbackIn(BaseModel):
    session_id: str
    fixed: bool


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=3, max_length=1000)


def draft_doc(d: dict) -> Doc:
    steps = [l.split(". ", 1)[1] for l in d["body"].splitlines() if l[:1].isdigit() and ". " in l]
    return Doc(id=d["id"], title=d["title"], category=d["category"], kind="draft", body=d["body"], steps=steps)


def build(db_path=None) -> tuple[FastAPI, DeskVoice]:
    kb = LocalKnowledge(config.DATA_DIR)
    store = ItsmStore(db_path or config.DB_PATH)
    for d in store.approved_drafts():  # approved articles stay searchable after restart
        kb.add(draft_doc(d))
    agent = DeskVoice(kb, store)
    app = FastAPI(title="DeskVoice", version="0.1.0")

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": agent.mode, "model": config.GEMINI_MODEL if agent.mode == "gemini" else None,
                "articles": sum(1 for d in kb.docs if d.kind == "article")}

    @app.post("/api/chat")
    def chat(body: ChatIn):
        sid = body.session_id or uuid.uuid4().hex
        requester = body.requester.strip() or "Employee"
        store.touch_session(sid, requester, body.lang)
        t0 = time.perf_counter()
        out = agent.handle(sid, body.text, requester=requester, lang=body.lang)
        ms = int((time.perf_counter() - t0) * 1000)
        if out["sources"] or out["steps"]:
            store.set_first_answer(sid, ms)
        return {**out, "session_id": sid, "latency_ms": ms, "mode": agent.mode}

    @app.post("/api/feedback")
    def feedback(body: FeedbackIn):
        return {**agent.feedback(body.session_id, body.fixed), "session_id": body.session_id, "mode": agent.mode}

    @app.get("/api/kb/{doc_id}")
    def kb_doc(doc_id: str):
        d = kb.get(doc_id)
        if not d:
            raise HTTPException(404, "Not found")
        return {"id": d.id, "title": d.title, "category": d.category, "kind": d.kind, "steps": d.steps,
                "body": d.body}

    # ---------- admin ----------
    @app.get("/api/admin/metrics")
    def metrics():
        return store.metrics()

    @app.get("/api/admin/tickets")
    def tickets():
        return store.list_tickets()

    @app.post("/api/admin/tickets/{ticket_id}/resolve")
    def resolve(ticket_id: str, body: ResolveIn):
        t = store.get_ticket(ticket_id)
        if not t:
            raise HTTPException(404, "Ticket not found")
        t = store.resolve_ticket(ticket_id, body.resolution.strip())
        steps = [s.strip() for s in body.resolution.replace("\n", ". ").split(". ") if s.strip()]
        issue = t["details"].get("issue") or t["summary"]
        article = (f"## Symptoms\n{issue}\n\n## Steps\n" +
                   "\n".join(f"{i}. {s.rstrip('.')}." for i, s in enumerate(steps, 1)) +
                   f"\n\n## Source\nResolved ticket {t['id']}")
        draft = store.create_draft(t, f"Fix: {t['summary']}", article)
        return {"ticket": t, "draft": draft}

    @app.get("/api/admin/drafts")
    def drafts():
        return store.list_drafts()

    @app.post("/api/admin/drafts/{draft_id}/approve")
    def approve(draft_id: str):
        d = store.approve_draft(draft_id)
        if not d:
            raise HTTPException(404, "Draft not found")
        kb.add(draft_doc(d))
        return d

    # ---------- web UI ----------
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin")
    def admin():
        return FileResponse(STATIC / "admin.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app, agent


app, _agent = build()
