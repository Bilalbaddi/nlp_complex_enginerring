"""RAG over policy documents: pgvector retrieval (embeddings from the Hugging Face API, nothing local) + Groq answer with citations.

If embeddings are unavailable (no token, API down) retrieval falls back to keyword overlap, so answers still work.
Chunks without an embedding are embedded lazily on first use (a few dozen chunks, one API call).
"""

import logging
import re
from typing import Any

import numpy as np
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import AutocommitSession
from app.llm import service
from app.ml.embed_store import vector_literal
from app.nlp.embeddings import get_embedder
from app.nlp.hf_client import HFError

log = logging.getLogger(__name__)
TOP_K = 4
_STOP = set("the a an of and or to in on for with is are was were what which who how does do be by as at from that this it its".split())

_PENDING = text("SELECT id, content FROM document_chunks WHERE embedding IS NULL ORDER BY id LIMIT :n")
_STORE = text(
    "UPDATE document_chunks c SET embedding = CAST(v.emb AS halfvec) "
    "FROM unnest(CAST(:ids AS int[]), CAST(:embs AS text[])) AS v(id, emb) WHERE c.id = v.id"
)
_VECTOR = text(
    "SELECT dc.id, d.id, d.title, dc.page, dc.content, 1 - (dc.embedding <=> CAST(:q AS halfvec)) AS score "
    "FROM document_chunks dc JOIN documents d ON d.id = dc.document_id WHERE dc.embedding IS NOT NULL "
    "ORDER BY dc.embedding <=> CAST(:q AS halfvec) LIMIT :k"
)
_ALL = text(
    "SELECT dc.id, d.id, d.title, dc.page, dc.content FROM document_chunks dc JOIN documents d ON d.id = dc.document_id LIMIT 3000"
)


def embeddings_available() -> bool:
    s = get_settings()
    return s.hf_configured and s.embedding_provider != "hashing"


def embed_pending(limit: int = 200) -> dict[str, int]:
    """Embed document chunks that have no vector yet. Returns {embedded, remaining}."""
    with AutocommitSession() as db:
        rows = db.execute(_PENDING, {"n": limit}).all()
        if not rows or not embeddings_available():
            remaining = db.scalar(text("SELECT count(*) FROM document_chunks WHERE embedding IS NULL")) or 0
            return {"embedded": 0, "remaining": int(remaining)}
        vecs = get_embedder("hf").embed([r[1] for r in rows])
        db.execute(_STORE, {"ids": [r[0] for r in rows], "embs": [vector_literal(v) for v in vecs]})
        db.commit()
        remaining = db.scalar(text("SELECT count(*) FROM document_chunks WHERE embedding IS NULL")) or 0
    return {"embedded": len(rows), "remaining": int(remaining)}


def _terms(q: str) -> list[str]:
    return [t for t in re.findall(r"[\wऀ-ॿ]+", q.lower()) if t not in _STOP and len(t) > 2]


def _keyword(db: Any, query: str, k: int) -> list[dict[str, Any]]:
    terms = _terms(query)
    scored = []
    for cid, did, title, page, content in db.execute(_ALL).all():
        low = content.lower()
        hits = sum(low.count(t) for t in terms)
        if hits:
            scored.append((hits / (len(low) ** 0.3), cid, did, title, page, content))
    scored.sort(reverse=True)
    return [{"chunk_id": c, "document_id": d, "title": t, "page": p, "content": ct, "score": round(float(s), 3), "method": "keyword"}
            for s, c, d, t, p, ct in scored[:k]]


def retrieve(query: str, k: int = TOP_K) -> list[dict[str, Any]]:
    """Top-k chunks for a question: vector search when possible, keyword fallback otherwise."""
    if embeddings_available():
        try:
            embed_pending()
            q = get_embedder("hf").embed([query[:500]])[0]
            with AutocommitSession() as db:
                rows = db.execute(_VECTOR, {"q": vector_literal(q), "k": k}).all()
            if rows:
                return [{"chunk_id": r[0], "document_id": r[1], "title": r[2], "page": r[3], "content": r[4],
                         "score": round(float(r[5]), 3), "method": "vector"} for r in rows]
        except HFError as exc:
            log.warning("vector retrieval unavailable, using keywords: %s", exc)
    with AutocommitSession() as db:
        return _keyword(db, query, k)


def answer(question: str, *, user_id: int | None = None) -> dict[str, Any]:
    """Grounded answer with numbered citations. Only the retrieved chunks are sent to the LLM."""
    chunks = retrieve(question)
    if not chunks:
        return {"answer": "I could not find anything relevant in the uploaded policy documents.", "sources": [],
                "label": {"ai_generated": False}}
    context = "\n\n".join(f"[{i + 1}] ({c['title']}, p.{c['page'] or '-'}) {c['content'][:900]}" for i, c in enumerate(chunks))
    prompt = service.load_prompt("rag_answer").format(context=context, question=question)
    r = service.generate("rag_answer", prompt, max_tokens=700, user_id=user_id)
    return {
        "answer": r.text.strip(),
        "sources": [{"n": i + 1, "chunk_id": c["chunk_id"], "document_id": c["document_id"], "title": c["title"], "page": c["page"],
                     "score": c["score"], "retrieval": c["method"], "snippet": c["content"][:240]} for i, c in enumerate(chunks)],
        "label": service.label(r),
    }
