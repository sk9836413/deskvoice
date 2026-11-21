// DeskVoice employee app: voice in (Web Speech API), voice out (speechSynthesis), chat with the agent API.
const $ = (id) => document.getElementById(id);
const log = $("log");
let sessionId = sessionStorage.getItem("dv_session") || null;

$("name").value = localStorage.getItem("dv_name") || "";
$("name").addEventListener("change", () => localStorage.setItem("dv_name", $("name").value.trim()));

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function scroll() { log.scrollTop = log.scrollHeight; }

function addUser(text) {
  const m = el("div", "msg user");
  m.appendChild(el("div", "bubble", text));
  log.appendChild(m);
  scroll();
}

function speechLang() { return $("lang").value.split("|")[0]; }
function langCode() { const v = $("lang").value; return v.includes("hinglish") ? "hinglish" : v.slice(0, 2); }

function speak(text) {
  if (!$("speak").checked || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = speechLang();
  speechSynthesis.speak(u);
}

async function api(url, body) {
  const res = await fetch(url, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || "Something went wrong");
  return data;
}

function addBot(r) {
  const m = el("div", "msg bot");
  const b = el("div", "bubble");
  b.appendChild(el("div", null, r.reply));

  if (r.steps?.length) {
    const ol = el("ol", "steps");
    r.steps.forEach((s) => ol.appendChild(el("li", null, s)));
    b.appendChild(ol);
  }

  if (r.sources?.length) {
    const row = el("div", "sources");
    row.appendChild(el("span", "muted", "Source: "));
    r.sources.forEach((s) => {
      const chip = el("button", "chip", `${s.id} · ${s.title}`);
      chip.type = "button";
      chip.addEventListener("click", () => showDoc(s.id, b));
      row.appendChild(chip);
    });
    b.appendChild(row);
  }

  if (r.pending) {
    const card = el("div", "card pending");
    card.appendChild(el("strong", null, r.pending.title));
    r.pending.lines.forEach((l) => card.appendChild(el("div", null, l)));
    const actions = el("div", "actions");
    const yes = el("button", "primary", "Yes, go ahead");
    const no = el("button", null, "No");
    [yes, no].forEach((btn) => (btn.type = "button"));
    yes.addEventListener("click", () => { disable(actions); send("yes"); });
    no.addEventListener("click", () => { disable(actions); send("no"); });
    actions.append(yes, no);
    card.appendChild(actions);
    b.appendChild(card);
  }

  if (r.ticket) {
    const t = r.ticket;
    const card = el("div", "card done");
    card.appendChild(el("strong", null, `${t.id} created`));
    card.appendChild(el("div", null, t.summary));
    card.appendChild(el("div", "muted", `${t.team} · ${t.priority} priority · ${t.status}`));
    b.appendChild(card);
  }

  if (r.workflow) {
    const w = r.workflow;
    const card = el("div", "card done");
    card.appendChild(el("strong", null, `${w.id} · ${w.status}`));
    card.appendChild(el("div", "muted", "Simulated in this prototype: no real reset or access change is made."));
    b.appendChild(card);
  }

  if (r.ask_feedback) {
    const actions = el("div", "actions");
    actions.appendChild(el("span", "muted", "Did this fix it?"));
    const yes = el("button", null, "Yes, fixed");
    const no = el("button", null, "No, still broken");
    [yes, no].forEach((btn) => (btn.type = "button"));
    yes.addEventListener("click", () => { disable(actions); feedback(true); });
    no.addEventListener("click", () => { disable(actions); feedback(false); });
    actions.append(yes, no);
    b.appendChild(actions);
  }

  if (r.latency_ms != null) b.appendChild(el("div", "meta", `${r.latency_ms} ms · ${r.mode}`));
  m.appendChild(b);
  log.appendChild(m);
  scroll();

  const spoken = [r.reply, ...(r.steps || []).slice(0, 3).map((s, i) => `${i + 1}. ${s}`)].join(" ");
  speak(spoken.replace(/`/g, ""));
}

function disable(container) { container.querySelectorAll("button").forEach((b) => (b.disabled = true)); }

async function showDoc(id, bubble) {
  try {
    const d = await api(`/api/kb/${encodeURIComponent(id)}`);
    const card = el("div", "card doc");
    card.appendChild(el("strong", null, `${d.id} · ${d.title}`));
    card.appendChild(el("div", "muted", `${d.category} · ${d.kind}`));
    if (d.steps.length) {
      const ol = el("ol", "steps");
      d.steps.forEach((s) => ol.appendChild(el("li", null, s)));
      card.appendChild(ol);
    } else {
      card.appendChild(el("div", null, d.body));
    }
    bubble.appendChild(card);
    scroll();
  } catch (e) { /* ignore */ }
}

function typing(on) {
  let t = $("typing");
  if (on && !t) {
    t = el("div", "msg bot");
    t.id = "typing";
    t.appendChild(el("div", "bubble muted", "DeskVoice is thinking…"));
    log.appendChild(t);
    scroll();
  } else if (!on && t) t.remove();
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  addUser(text);
  $("text").value = "";
  typing(true);
  try {
    const r = await api("/api/chat", {
      session_id: sessionId, text, requester: $("name").value.trim() || "Employee", lang: langCode(),
    });
    sessionId = r.session_id;
    sessionStorage.setItem("dv_session", sessionId);
    typing(false);
    addBot(r);
  } catch (e) {
    typing(false);
    addBot({ reply: `Error: ${e.message}` });
  }
}

async function feedback(fixed) {
  addUser(fixed ? "Yes, that fixed it" : "No, it's still not working");
  typing(true);
  try {
    const r = await api("/api/feedback", { session_id: sessionId, fixed });
    typing(false);
    addBot(r);
  } catch (e) {
    typing(false);
    addBot({ reply: `Error: ${e.message}` });
  }
}

$("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("text").value); });
$("chips").addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") send(e.target.textContent); });

// ---------- voice input ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
if (!SR) {
  $("voiceNote").textContent = "Voice input needs Chrome or Edge. You can still type.";
  $("mic").disabled = true;
} else {
  $("mic").addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    speechSynthesis?.cancel();
    rec = new SR();
    rec.lang = speechLang();
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = "";
    rec.onresult = (ev) => {
      let interim = "";
      for (const r of ev.results) (r.isFinal ? (finalText = r[0].transcript) : (interim += r[0].transcript));
      $("text").value = finalText || interim;
    };
    rec.onerror = (ev) => { $("voiceNote").textContent = `Mic error: ${ev.error}`; };
    rec.onend = () => {
      $("mic").classList.remove("live");
      rec = null;
      if ($("text").value.trim()) send($("text").value);
    };
    $("mic").classList.add("live");
    $("voiceNote").textContent = "Listening… tap the mic again to stop.";
    rec.start();
  });
}

api("/api/health").then((h) => {
  $("mode").textContent = h.mode === "gemini" ? `Gemini · ${h.model}` : "Demo mode (no API key)";
  $("mode").classList.add(h.mode);
}).catch(() => {});
