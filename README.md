# DeskVoice

**A voice IT helpdesk agent that answers, acts and learns.**
Prototype by team **SKA** (Suraj Kumar, Prathmesh Purushottam Deore), built for the *Build with Google Gemini*
hackathon (problem statement: *Voice-Powered Enterprise Knowledge & Decision Agent*) and entered in **BUILD 2036**
(frontier: *Applied AI & Agentic Intelligence*).

Employees speak their IT problem in English, Hindi or Hinglish. DeskVoice searches the company's IT
knowledge and past tickets, replies by voice with step-by-step fixes and shows the source. If the fix
doesn't work, it raises a complete, prioritised ticket. It can also start self-service workflows
(password reset, account unlock, access requests). Nothing is created until the employee says yes.
Every resolved ticket becomes a draft knowledge article that IT can approve, so the agent keeps learning.

## What's in the prototype

| Part | Where | Status |
|---|---|---|
| Voice in / out (push-to-talk, live transcript, spoken replies) | `static/app.js` (Web Speech API) | Works in Chrome / Edge |
| Agent with Gemini function calling | `app/agent.py` (`_gemini_turn`) | Needs `GEMINI_API_KEY` or Vertex AI |
| Offline demo brain (same tools and rules, no key needed) | `app/agent.py` (`_demo_turn`) | Works |
| Knowledge search (BM25 over articles + past tickets, Hinglish phrase map) | `app/knowledge.py` | Works |
| Tickets, triage, routing, status | `app/store.py` (SQLite ITSM stand-in) | Works |
| Self-service workflows | `app/store.py` | Simulated (no real reset or access change) |
| Confirmation before every action | `DeskVoice.handle()` | Enforced in code, not by the model |
| IT admin dashboard, resolve ticket → draft article → approve | `static/admin.*`, `/api/admin/*` | Works |
| ADK agent for Agent Runtime / Gemini Enterprise | `deskvoice_adk/agent.py` | Loads; run with `adk web` |
| Tests | `tests/` | 12 passing |

### How it maps to Gemini Enterprise

| In this prototype | In production |
|---|---|
| `LocalKnowledge` (Markdown articles + past tickets) | Gemini Enterprise connectors: SharePoint / OneDrive, Google Drive, Confluence, ServiceNow, Jira |
| `ItsmStore` (SQLite) | ServiceNow / Jira connector actions, or our own ITSM through an MCP server |
| Simulated workflows | Workflow Builder flows (password reset, access request with manager approval) |
| Browser speech recognition and speech synthesis | Gemini Live API for real-time voice |
| FastAPI on Cloud Run | Same, plus the ADK agent on Agent Runtime registered in the Gemini Enterprise app |

## Run it locally

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8090
```

Open http://localhost:8090 for the employee app and http://localhost:8090/admin for IT.
Without a key it runs in **demo mode**. To use Gemini, set a key first:

```powershell
$env:GEMINI_API_KEY="your-key"; .venv\Scripts\python -m uvicorn app.main:app --port 8090
```

See `.env.example` for Vertex AI settings and the model name.

### Try these

1. "Outlook is stuck on loading profile" → steps from KB-1001 → **No, still broken** → confirm → ticket with the steps already tried.
2. "Mujhe finance drive ka access chahiye" → access request → confirm.
3. "What's the status of my ticket?"
4. In `/admin`, resolve the ticket → a draft article appears → approve it → ask the same question again.

## Run the ADK agent

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
$env:GOOGLE_API_KEY="your-key"; .venv\Scripts\adk web
```

Pick `deskvoice_adk` in the ADK web UI.

## Deploy to Cloud Run

```powershell
gcloud run deploy deskvoice --source . --region asia-south1 --allow-unauthenticated --set-env-vars GEMINI_API_KEY=your-key
```

For production use Secret Manager instead of a plain env var. The SQLite file lives in `/tmp` on Cloud
Run, so data resets when the instance restarts; use Cloud SQL for anything beyond a demo.

## Tests

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

## Known limits

- Workflows are simulated; no real password reset or access change happens.
- The knowledge base is 10 sample articles and 6 past tickets, not a real company's data.
- Voice uses the browser's speech engine; Hindi recognition quality depends on the browser.
- The Gemini path is written against the `google-genai` SDK but has only been run in demo mode so far.
