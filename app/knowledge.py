"""Knowledge search over IT articles and past tickets.

`LocalKnowledge` reads Markdown articles from data/kb and past tickets from
data/past_tickets.json, and ranks them with BM25. In production this is swapped
for Gemini Enterprise connectors (SharePoint, Drive, Confluence, ServiceNow,
Jira) behind the same `search()` interface.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

# Small Hinglish / Hindi-in-Latin phrase map so spoken Hinglish still finds English articles.
PHRASES = {
    "nahi khul raha": "not opening",
    "nahi khul rahi": "not opening",
    "khul nahi raha": "not opening",
    "nahi chal raha": "not working",
    "chal nahi raha": "not working",
    "kaam nahi kar raha": "not working",
    "bhool gaya": "forgot",
    "bhool gayi": "forgot",
    "atak gaya": "stuck",
    "atka hua": "stuck",
    "band ho gaya": "locked",
    "baar baar": "keeps",
    "kat raha": "disconnect",
    "kat jata": "disconnect",
    "naya phone": "new phone",
    "chahiye": "need",
    "awaaz": "audio microphone",
    "net nahi": "no internet",
}
STOP = set("a an the is are am i my me to of in on for and or it its this that with be can not no do does did "
           "please hai ho raha rahi mera meri mujhe ko ka ki ke se par".split())


def normalise(text: str) -> str:
    t = text.lower()
    for k, v in PHRASES.items():
        t = t.replace(k, f"{k} {v}")
    return t


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9\-]+", normalise(text)) if w not in STOP and len(w) > 1]


@dataclass
class Doc:
    id: str
    title: str
    category: str
    kind: str  # "article" | "ticket" | "draft"
    body: str
    steps: list[str] = field(default_factory=list)
    escalate_if: str = ""
    keywords: str = ""

    def search_text(self) -> str:
        # Title and keywords count double.
        return f"{self.title} {self.title} {self.keywords} {self.keywords} {self.body}"


def parse_article(path: Path) -> Doc:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = raw
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    sections = dict(re.findall(r"^## (.+?)\n(.*?)(?=^## |\Z)", body, re.S | re.M))
    steps = [re.sub(r"^\d+\.\s*", "", s).strip() for s in sections.get("Steps", "").strip().splitlines() if s.strip()]
    return Doc(
        id=meta.get("id", path.stem),
        title=meta.get("title", path.stem),
        category=meta.get("category", "General"),
        kind="article",
        body=body,
        steps=steps,
        escalate_if=sections.get("Raise a ticket if", "").strip(),
        keywords=meta.get("keywords", ""),
    )


class LocalKnowledge:
    def __init__(self, data_dir: Path):
        self.docs: list[Doc] = []
        for p in sorted((data_dir / "kb").glob("*.md")):
            self.docs.append(parse_article(p))
        tickets = data_dir / "past_tickets.json"
        if tickets.exists():
            for t in json.loads(tickets.read_text(encoding="utf-8")):
                self.docs.append(Doc(id=t["id"], title=t["summary"], category=t["category"], kind="ticket",
                                     body=f"{t['summary']}. Resolution: {t['resolution']}"))
        self._index()

    def add(self, doc: Doc) -> None:
        self.docs = [d for d in self.docs if d.id != doc.id] + [doc]
        self._index()

    def get(self, doc_id: str) -> Doc | None:
        return next((d for d in self.docs if d.id == doc_id), None)

    def _index(self) -> None:
        self._toks = [tokens(d.search_text()) for d in self.docs]
        self._avg = sum(map(len, self._toks)) / max(1, len(self._toks))
        df: dict[str, int] = {}
        for ts in self._toks:
            for w in set(ts):
                df[w] = df.get(w, 0) + 1
        n = len(self._toks)
        self._idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query: str, k: int = 3, min_score: float = 1.0) -> list[tuple[Doc, float]]:
        q = tokens(query)
        k1, b = 1.5, 0.75
        scored = []
        for doc, ts in zip(self.docs, self._toks):
            tf: dict[str, int] = {}
            for w in ts:
                tf[w] = tf.get(w, 0) + 1
            s = 0.0
            for w in set(q):
                if w in tf:
                    f = tf[w]
                    s += self._idf.get(w, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * len(ts) / self._avg))
            if doc.kind == "ticket":
                s *= 0.75  # full articles first; past tickets back them up
            if s >= min_score:
                scored.append((doc, round(s, 2)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
