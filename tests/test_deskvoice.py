import os

os.environ["DESKVOICE_MODE"] = "demo"  # tests never call Gemini

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agent import DeskVoice, detect_workflow, triage
from app.knowledge import LocalKnowledge
from app.main import build
from app.store import ItsmStore


@pytest.fixture()
def kb():
    return LocalKnowledge(config.DATA_DIR)


@pytest.fixture()
def bot(kb, tmp_path):
    return DeskVoice(kb, ItsmStore(tmp_path / "t.db"))


@pytest.fixture()
def client(tmp_path):
    app, _ = build(tmp_path / "api.db")
    return TestClient(app)


def test_search_finds_outlook_article(kb):
    top = kb.search("my outlook is stuck on loading profile")[0][0]
    assert top.id == "KB-1001"
    assert any("safe" in s for s in top.steps)


def test_hinglish_query_finds_article(kb):
    top = kb.search("vpn baar baar kat raha hai")[0][0]
    assert top.id == "KB-1003"


def test_gibberish_finds_nothing(kb):
    assert kb.search("qwerty zxcvb") == []


def test_workflow_detection():
    assert detect_workflow("I forgot my password")[0] == "password_reset"
    assert detect_workflow("my account is locked")[0] == "account_unlock"
    kind, target = detect_workflow("Mujhe finance drive ka access chahiye")
    assert kind == "access_request" and "finance drive" in target
    assert detect_workflow("Outlook keeps asking for password after I changed it") is None


def test_triage():
    assert triage("VPN is down for the whole team") == ("Network", "High")
    assert triage("printer is a bit slow") == ("End-user Devices", "Low")


def test_answer_then_ticket_needs_confirmation(bot):
    r = bot.handle("s1", "Outlook stuck on loading profile", requester="Asha")
    assert r["steps"] and r["sources"][0]["id"] == "KB-1001" and r["ask_feedback"]

    r = bot.feedback("s1", fixed=False)
    assert r["pending"]["kind"] == "ticket"
    assert bot.store.list_tickets() == []  # nothing created before confirmation

    r = bot.handle("s1", "haan kar do")
    t = r["ticket"]
    assert t["id"].startswith("TKT-") and t["category"] == "Messaging"
    assert t["details"]["steps_already_tried"]  # L2 sees what was tried


def test_cancel_does_nothing(bot):
    bot.handle("s2", "I forgot my password")
    r = bot.handle("s2", "no")
    assert r["workflow"] is None and r["ticket"] is None


def test_workflow_runs_after_yes(bot):
    r = bot.handle("s3", "I need access to the finance drive")
    assert r["pending"]["kind"] == "workflow"
    r = bot.handle("s3", "yes")
    assert r["workflow"]["kind"] == "access_request"


def test_status_lookup(bot):
    bot.handle("s4", "Outlook stuck on loading profile", requester="Ravi")
    bot.feedback("s4", fixed=False)
    tid = bot.handle("s4", "yes")["ticket"]["id"]
    r = bot.handle("s5", f"what's the status of {tid}?", requester="Ravi")
    assert tid in r["reply"] and "New" in r["reply"]


def test_api_flow_and_learning_loop(client):
    r = client.post("/api/chat", json={"text": "Wi-Fi connected but no internet", "requester": "Meera"}).json()
    sid = r["session_id"]
    assert r["sources"][0]["id"] == "KB-1010"
    client.post("/api/feedback", json={"session_id": sid, "fixed": False})
    tid = client.post("/api/chat", json={"session_id": sid, "text": "yes"}).json()["ticket"]["id"]

    out = client.post(f"/api/admin/tickets/{tid}/resolve",
                      json={"resolution": "DHCP lease was stuck. Restarted the floor switch"}).json()
    draft = out["draft"]
    assert draft["status"] == "Draft"
    client.post(f"/api/admin/drafts/{draft['id']}/approve")
    doc = client.get(f"/api/kb/{draft['id']}").json()
    assert doc["kind"] == "draft" and doc["steps"]

    m = client.get("/api/admin/metrics").json()
    assert m["tickets_created"] == 1 and m["conversations"] == 1


def test_rejects_empty_message(client):
    assert client.post("/api/chat", json={"text": ""}).status_code == 422


def test_ticket_outcome_not_overwritten(client):
    sid = client.post("/api/chat", json={"text": "Outlook stuck on loading profile"}).json()["session_id"]
    client.post("/api/feedback", json={"session_id": sid, "fixed": False})
    client.post("/api/chat", json={"session_id": sid, "text": "yes"})
    client.post("/api/chat", json={"session_id": sid, "text": "I forgot my password"})
    client.post("/api/chat", json={"session_id": sid, "text": "yes"})
    assert client.get("/api/admin/metrics").json()["self_resolution_rate"] == 0
