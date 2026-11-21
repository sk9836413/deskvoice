const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

async function api(url, body) {
  const res = await fetch(url, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || "Request failed");
  return data;
}

function kpi(label, value, hint) {
  const k = el("div", "kpi");
  k.appendChild(el("div", "kpi-value", value));
  k.appendChild(el("div", "kpi-label", label));
  if (hint) k.appendChild(el("div", "muted small", hint));
  return k;
}

async function loadMetrics() {
  const m = await api("/api/admin/metrics");
  const box = $("kpis");
  box.replaceChildren(
    kpi("Conversations", String(m.conversations)),
    kpi("Solved without a human", m.self_resolution_rate == null ? "–" : `${m.self_resolution_rate}%`,
        "fixed by the agent or a self-service workflow"),
    kpi("Time to first answer", m.avg_first_answer_ms == null ? "–" : `${m.avg_first_answer_ms} ms`, "server time"),
    kpi("Tickets created", String(m.tickets_created), `${m.open_tickets} open`),
  );
  if (m.top_categories.length) {
    const top = el("div", "kpi");
    top.appendChild(el("div", "kpi-label", "Top ticket categories"));
    const ul = el("ul", "plain");
    m.top_categories.forEach((c) => ul.appendChild(el("li", null, `${c.category}: ${c.c}`)));
    top.appendChild(ul);
    box.appendChild(top);
  }
}

async function loadTickets() {
  const list = await api("/api/admin/tickets");
  const box = $("tickets");
  if (!list.length) { box.replaceChildren(el("p", "muted", "No tickets yet.")); return; }
  box.replaceChildren(...list.map((t) => {
    const card = el("div", "card ticket");
    const head = el("div", "row");
    head.appendChild(el("strong", null, `${t.id} · ${t.summary}`));
    head.appendChild(el("span", `badge ${t.status === "Resolved" ? "ok" : t.priority === "High" ? "high" : ""}`,
      t.status === "Resolved" ? "Resolved" : `${t.priority} · ${t.status}`));
    card.appendChild(head);
    card.appendChild(el("div", "muted", `${t.requester} · ${t.category} → ${t.team} · ${new Date(t.created_at * 1000).toLocaleString()}`));

    const d = t.details || {};
    const det = el("details");
    det.appendChild(el("summary", null, "What the agent collected"));
    if (d.issue) det.appendChild(el("div", null, `Issue: ${d.issue}`));
    if (d.steps_already_tried?.length) {
      det.appendChild(el("div", null, "Steps already shown to the employee:"));
      const ol = el("ol", "steps");
      d.steps_already_tried.forEach((s) => ol.appendChild(el("li", null, s)));
      det.appendChild(ol);
    }
    if (d.kb_shown?.length) det.appendChild(el("div", "muted", `Articles shown: ${d.kb_shown.join(", ")}`));
    if (d.language) det.appendChild(el("div", "muted", `Language: ${d.language}`));
    card.appendChild(det);

    if (t.status === "Resolved") {
      card.appendChild(el("div", null, `Resolution: ${t.resolution}`));
    } else {
      const form = el("form", "resolve");
      const input = el("input");
      input.placeholder = "How was it fixed? (becomes a draft article)";
      input.maxLength = 1000;
      const btn = el("button", "primary", "Resolve");
      form.append(input, btn);
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (input.value.trim().length < 3) return;
        btn.disabled = true;
        await api(`/api/admin/tickets/${t.id}/resolve`, { resolution: input.value.trim() });
        refresh();
      });
      card.appendChild(form);
    }
    return card;
  }));
}

async function loadDrafts() {
  const list = await api("/api/admin/drafts");
  const box = $("drafts");
  if (!list.length) { box.replaceChildren(el("p", "muted", "No drafts yet. Resolve a ticket to create one.")); return; }
  box.replaceChildren(...list.map((d) => {
    const card = el("div", "card");
    const head = el("div", "row");
    head.appendChild(el("strong", null, `${d.id} · ${d.title}`));
    head.appendChild(el("span", `badge ${d.status === "Approved" ? "ok" : ""}`, d.status));
    card.appendChild(head);
    card.appendChild(el("pre", "article", d.body));
    if (d.status !== "Approved") {
      const btn = el("button", "primary", "Approve and publish");
      btn.addEventListener("click", async () => { btn.disabled = true; await api(`/api/admin/drafts/${d.id}/approve`, {}); refresh(); });
      card.appendChild(btn);
    }
    return card;
  }));
}

function refresh() { loadMetrics(); loadTickets(); loadDrafts(); }
$("refresh").addEventListener("click", refresh);
refresh();
