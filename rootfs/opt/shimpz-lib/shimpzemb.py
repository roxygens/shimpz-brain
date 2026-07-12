"""shimpzemb — LOCAL semantic-recall layer over the markdown memory store (files stay the truth).

Embeddings only RANK; the store remains plain markdown (git-diffable, model-editable with
Read/Edit/grep). The model is a small multilingual static-embedding one (model2vec
potion-multilingual-128M, baked into the image at /opt/shimpz-emb) — the store is PT/EN mixed, so an
English-centric encoder would miss half the queries. Measured on this machine (R121 spike): loads
in ~3s, embeds ONE query in ~13ms and the WHOLE store (~300 sections) in ~50ms — so the index is
simply rebuilt in full whenever any file changes; incremental bookkeeping would be pure complexity.

numpy/model2vec are imported lazily inside functions so this module stays importable (and its pure
helpers testable) on hosts without them. shimpz-recall treats ANY failure here as "no semantic signal
this turn" (keyword-only): the recall hook must never block a prompt over ranking.
"""

import json
import os
import re
from pathlib import Path

MODEL_DIR = os.environ.get("SHIMPZ_EMB_MODEL", "/opt/shimpz-emb/potion-multilingual-128M")
INDEX = ".embindex.npz"  # cached store vectors, next to the memory files (derived, git-ignored)
CHUNK_CAP = 2000  # chars per embedded section — static embeddings dilute on long text
CHUNK_MIN = 40  # skip heading stubs shorter than this
QUERY_CAP = 1000  # chars of the prompt worth embedding

_HEADING = re.compile(r"(?m)^(?=#{1,3} )")


def chunk_sections(body: str) -> list[str]:
    """Split a markdown body into heading-anchored sections (section 0 = the file head).

    Pure and deterministic on purpose: the index stores (file, section-idx) and the injection side
    re-derives the winning section's TEXT by re-running this on the same body. A body with no
    headings is one single section.
    """
    parts = [p.strip()[:CHUNK_CAP] for p in _HEADING.split(body or "")]
    return [p for p in parts if len(p) >= CHUNK_MIN]


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal-rank fusion of ranked key lists → {key: fused score}.

    The standard scale-free way to combine rankers whose scores aren't comparable (keyword counts
    vs cosine): each list contributes 1/(k+rank), so a key present in BOTH lists beats a key that
    tops only one. A key missing from a list simply contributes nothing — no weight tuning.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for i, key in enumerate(ranking):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + i + 1)
    return fused


def _load_model():
    from model2vec import StaticModel

    return StaticModel.from_pretrained(MODEL_DIR)


def _read_index(idx, current):
    """The cached (vectors, owners, sections) — or None when absent/stale/corrupt (→ rebuild).

    Staleness = the exact {relpath: mtime} map or the model dir differ from what was indexed.
    A corrupt cache is treated the same as a stale one: rebuilt and overwritten, never fatal.
    """
    import numpy as np

    if not idx.is_file():
        return None
    try:
        with np.load(idx, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            if meta.get("files") != current or meta.get("model") != MODEL_DIR:
                return None
            return z["vectors"], meta["owners"], meta["sections"]
    except OSError, ValueError, KeyError, json.JSONDecodeError:
        return None


def _build_index(mem, rels, current, model, idx):
    """Embed every section of every store file and persist the cache atomically.

    Full rebuild by design (~50ms for the whole store — see module docstring). The tmp+replace
    keeps a concurrent recall from ever reading a half-written .npz.
    """
    import numpy as np

    chunks, owners, sections = [], [], []
    for rel in rels:
        body = (mem / rel).read_text(encoding="utf-8", errors="ignore")
        for i, chunk in enumerate(chunk_sections(body)):
            chunks.append(chunk)
            owners.append(rel)
            sections.append(i)
    if not chunks:
        return None
    vectors = model.encode(chunks).astype("float32")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
    meta = {"model": MODEL_DIR, "files": current, "owners": owners, "sections": sections}
    tmp = idx.with_name(INDEX + ".tmp.npz")
    with tmp.open("wb") as fh:
        np.savez(fh, vectors=vectors, meta=json.dumps(meta))
    tmp.replace(idx)
    return vectors, owners, sections


def semantic_scores(query: str, mem_dir, rels: list[str]) -> dict[str, tuple[float, int]]:
    """Cosine of the query against every store file → {relpath: (best score, best section idx)}.

    Refreshes the on-disk index first (mtime map comparison; full re-embed when stale). Raises on
    any failure (model missing, unreadable store) — the CALLER decides what a failure means:
    shimpz-recall degrades to keyword-only and logs, because the hook must never die over ranking.
    """
    import numpy as np

    if not rels:  # empty store — don't pay the model load just to rank nothing
        return {}
    mem = Path(mem_dir)
    current = {rel: (mem / rel).stat().st_mtime for rel in rels}
    idx = mem / INDEX
    cached = _read_index(idx, current)
    model = None
    if cached is None:
        model = _load_model()
        cached = _build_index(mem, rels, current, model, idx)
    if cached is None:  # empty store — nothing to rank
        return {}
    vectors, owners, sections = cached
    if model is None:
        model = _load_model()
    qv = model.encode([(query or "")[:QUERY_CAP]]).astype("float32")
    qv /= np.linalg.norm(qv, axis=1, keepdims=True) + 1e-9
    sims = vectors @ qv[0]
    best: dict[str, tuple[float, int]] = {}
    for sim, rel, sec in zip(sims.tolist(), owners, sections, strict=True):
        if rel not in best or sim > best[rel][0]:
            best[rel] = (sim, sec)
    return best
