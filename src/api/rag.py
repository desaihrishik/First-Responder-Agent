"""
RAG retrieval module — ChromaDB semantic search + DuckDB structured queries.
Provides similar incident retrieval and context assembly for the triage pipeline.
"""

import time
import logging
from pathlib import Path
from typing import Optional

import duckdb
import chromadb
from sentence_transformers import SentenceTransformer

log = logging.getLogger("rag")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "responder.duckdb"
CHROMA_DIR = DATA_DIR / "chromadb"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_embedder: Optional[SentenceTransformer] = None
_chroma_collection = None
_duckdb_con = None


def init_rag():
    global _embedder, _chroma_collection, _duckdb_con

    log.info("Initializing RAG components...")

    _embedder = SentenceTransformer(EMBED_MODEL)
    log.info(f"Embedding model loaded: {EMBED_MODEL}")

    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _chroma_collection = chroma_client.get_collection("nyc_incidents")
    count = _chroma_collection.count()
    log.info(f"ChromaDB collection loaded: {count:,} documents")

    _duckdb_con = duckdb.connect(str(DB_PATH), read_only=True)
    row_count = _duckdb_con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    log.info(f"DuckDB connected: {row_count:,} rows")


def retrieve_similar(
    query_text: str,
    borough: Optional[str] = None,
    n: int = 5,
) -> tuple[list[dict], float]:
    """
    Semantic search over ChromaDB for similar past incidents.
    Returns (list_of_incidents, search_time_ms).
    """
    global _chroma_collection, _embedder

    if _chroma_collection is None or _embedder is None:
        init_rag()

    t0 = time.perf_counter()

    where_filter = None
    if borough and borough.upper() not in ("ALL", "UNKNOWN", ""):
        where_filter = {"borough": borough.upper()}

    try:
        results = _chroma_collection.query(
            query_texts=[query_text],
            n_results=n,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        log.warning(f"ChromaDB query with borough filter failed: {e}. Retrying without filter.")
        results = _chroma_collection.query(
            query_texts=[query_text],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )

    incidents = []
    if results and results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else 1.0

            incidents.append({
                "id": doc_id,
                "complaint_type": meta.get("complaint_type", "Unknown"),
                "borough": meta.get("borough", "Unknown"),
                "date": meta.get("date", ""),
                "resolution_days": float(meta.get("resolution_days", -1)),
                "source": meta.get("source", ""),
                "similarity": round(1.0 - distance, 3),
            })

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return incidents, elapsed_ms


def query_structured(
    borough: Optional[str] = None,
    complaint_type: Optional[str] = None,
    limit: int = 5,
) -> tuple[list[dict], dict, float]:
    """
    DuckDB structured query for similar incidents + resolution time stats.
    Returns (incidents, stats_dict, query_time_ms).
    """
    global _duckdb_con

    if _duckdb_con is None:
        init_rag()

    t0 = time.perf_counter()

    conditions = []
    params = []

    if borough and borough.upper() not in ("ALL", "UNKNOWN", ""):
        conditions.append("borough = ?")
        params.append(borough.upper())

    if complaint_type:
        conditions.append("complaint_type ILIKE ?")
        params.append(f"%{complaint_type}%")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    query = f"""
        SELECT id, complaint_type, descriptor, borough, agency,
               created_date, resolution_days, source
        FROM incidents
        {where_clause}
        ORDER BY created_date DESC
        LIMIT ?
    """
    params.append(limit)

    rows = _duckdb_con.execute(query, params).fetchall()
    columns = ["id", "complaint_type", "descriptor", "borough", "agency",
                "created_date", "resolution_days", "source"]

    incidents = [dict(zip(columns, r)) for r in rows]

    stats_query = f"""
        SELECT
            COUNT(*) as total_count,
            AVG(CASE WHEN resolution_days > 0 THEN resolution_days END) as avg_resolution,
            MEDIAN(CASE WHEN resolution_days > 0 THEN resolution_days END) as median_resolution,
            MIN(CASE WHEN resolution_days > 0 THEN resolution_days END) as min_resolution,
            MAX(CASE WHEN resolution_days > 0 THEN resolution_days END) as max_resolution
        FROM incidents
        {where_clause}
    """
    stats_params = params[:-1]
    stats_row = _duckdb_con.execute(stats_query, stats_params).fetchone()

    stats = {
        "total_similar": stats_row[0] if stats_row else 0,
        "avg_resolution_days": round(stats_row[1], 2) if stats_row and stats_row[1] else None,
        "median_resolution_days": round(stats_row[2], 2) if stats_row and stats_row[2] else None,
        "min_resolution_days": round(stats_row[3], 2) if stats_row and stats_row[3] else None,
        "max_resolution_days": round(stats_row[4], 2) if stats_row and stats_row[4] else None,
    }

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return incidents, stats, elapsed_ms


def assemble_context(
    user_text: str,
    similar_incidents: list[dict],
    vision_desc: Optional[str] = None,
    stats: Optional[dict] = None,
) -> str:
    """
    Build the context string for Nemotron, kept under ~800 tokens.
    """
    parts = []

    if vision_desc:
        parts.append(f"SCENE ANALYSIS: {vision_desc[:300]}")

    parts.append(f"DISPATCH REPORT: {user_text[:400]}")

    if similar_incidents:
        parts.append("\nSIMILAR PAST INCIDENTS IN NYC:")
        for i, inc in enumerate(similar_incidents[:3], 1):
            line = f"{i}. {inc.get('complaint_type', 'Unknown')} in {inc.get('borough', 'Unknown')}"
            date = inc.get("date", "")
            if date:
                line += f" ({date})"
            res = inc.get("resolution_days", -1)
            if res and res > 0:
                line += f" — resolved in {res:.1f} days"
            source = inc.get("source", "")
            if source:
                line += f" [source: {source}]"
            parts.append(line)

    if stats and stats.get("total_similar"):
        avg = stats.get("avg_resolution_days")
        if avg:
            parts.append(f"\nHISTORICAL CONTEXT: {stats['total_similar']:,} similar incidents on record. Average resolution: {avg:.1f} days.")

    context = "\n".join(parts)
    if len(context) > 2000:
        context = context[:2000] + "..."

    return context


def close_rag():
    global _duckdb_con
    if _duckdb_con:
        _duckdb_con.close()
        _duckdb_con = None
