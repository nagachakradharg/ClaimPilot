"""Policy-form ingestion (clause-level chunks) and filtered retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from claimpilot.embeddings import get_embedder
from claimpilot.models import PolicyClause

_FORM_RE = re.compile(r"^form_id:\s*(\S+)", re.M)
_CLAUSE_RE = re.compile(r"^###\s+\[([A-Z0-9-]+)\]\s+(.+)$")
_SECTION_RE = re.compile(r"^#\s+(.+)$")


@dataclass(frozen=True)
class Clause:
    form_id: str
    clause_id: str
    section: str
    title: str
    text: str
    score: float | None = None  # cosine similarity, filled at retrieval

    def render(self) -> str:
        return f"[{self.clause_id}] {self.title} ({self.section})\n{self.text}"


def parse_policy_form(markdown: str) -> list[Clause]:
    """Split a policy form into clauses. Each `### [ID] Title` starts a clause."""
    form = _FORM_RE.search(markdown)
    if not form:
        raise ValueError("policy form is missing `form_id:` front matter")
    form_id = form.group(1)
    body = markdown.split("---", 2)[-1]

    clauses: list[Clause] = []
    section, current, lines = "", None, []

    def flush() -> None:
        if current:
            text = " ".join(line.strip() for line in lines if line.strip())
            clauses.append(Clause(form_id, current[0], section, current[1], text))

    for line in body.splitlines():
        if m := _CLAUSE_RE.match(line):
            flush()
            current, lines = (m.group(1), m.group(2).strip()), []
        elif m := _SECTION_RE.match(line):
            flush()
            current, lines = None, []
            section = m.group(1).strip()
        elif current:
            lines.append(line)
    flush()

    ids = [c.clause_id for c in clauses]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate clause ids in {form_id}")
    return clauses


async def ingest_policy_form(session: AsyncSession, path: Path) -> list[Clause]:
    """Replace all clauses of the form found in `path` (idempotent)."""
    clauses = parse_policy_form(path.read_text())
    form_id = clauses[0].form_id
    vectors = get_embedder().embed_documents([f"{c.title}. {c.section}. {c.text}" for c in clauses])
    await session.execute(delete(PolicyClause).where(PolicyClause.form_id == form_id))
    session.add_all(
        PolicyClause(
            form_id=c.form_id,
            clause_id=c.clause_id,
            section=c.section,
            title=c.title,
            text=c.text,
            embedding=vec,
        )
        for c, vec in zip(clauses, vectors, strict=True)
    )
    await session.flush()
    return clauses


async def retrieve_clauses(session: AsyncSession, form_id: str, query: str, k: int) -> list[Clause]:
    """Top-k clauses of ONE policy form by cosine similarity."""
    qvec = get_embedder().embed_query(query)
    distance = PolicyClause.embedding.cosine_distance(qvec)
    rows = await session.execute(
        select(PolicyClause, distance.label("distance"))
        .where(PolicyClause.form_id == form_id)
        .order_by(distance)
        .limit(k)
    )
    return [
        Clause(r.form_id, r.clause_id, r.section, r.title, r.text, round(1 - float(d), 4))
        for r, d in rows.all()
    ]


async def get_clauses(session: AsyncSession, form_id: str, clause_ids: list[str]) -> list[Clause]:
    rows = await session.scalars(
        select(PolicyClause).where(
            PolicyClause.form_id == form_id, PolicyClause.clause_id.in_(clause_ids)
        )
    )
    return [Clause(r.form_id, r.clause_id, r.section, r.title, r.text) for r in rows]


async def retrieve_for_coverage(
    session: AsyncSession, form_id: str, query: str, k: int
) -> list[Clause]:
    """Semantic top-k plus an exclusion sweep: exclusions decide coverage, so they are always
    in context even when they are not lexically similar to the claim description."""
    semantic = await retrieve_clauses(session, form_id, query, k)
    rows = await session.scalars(
        select(PolicyClause).where(
            PolicyClause.form_id == form_id,
            (PolicyClause.section.ilike("%exclusion%")) | (PolicyClause.title.ilike("%exclusion%")),
        )
    )
    seen = {c.clause_id for c in semantic}
    extra = [
        Clause(r.form_id, r.clause_id, r.section, r.title, r.text)
        for r in rows
        if r.clause_id not in seen
    ]
    return semantic + sorted(extra, key=lambda c: c.clause_id)
