import asyncio
import sqlite3
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harvey.state import StateManager
from harvey.rag.store import VectorStore

def check_sqlite_direct():
    db_path = Path("data/harvey.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    tables = [
        "companies", "prospects", "outbox", "campaigns"
    ]
    counts = {}
    for t in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cursor.fetchone()[0]
        except Exception as e:
            counts[t] = str(e)
    conn.close()
    return counts

async def check_async_state():
    state = StateManager("data/harvey.db")
    await state.init_db()
    status = await state.verify_integrity()
    return status

async def check_rag():
    store = VectorStore()
    total_chunks = store.collection.count()
    results = await store.search(query="SaaS outbound sales pipeline automation", top_k=2)
    return total_chunks, len(results)

if __name__ == "__main__":
    print("=== SQLITE DIRECT RECORD COUNTS ===")
    counts = check_sqlite_direct()
    for k, v in counts.items():
        print(f"  {k}: {v}")

    print("\n=== STATE INTEGRITY CHECK ===")
    status = asyncio.run(check_async_state())
    print(f"  Integrity: {status}")

    print("\n=== RAG CHROMA QUERY CHECK ===")
    chunks, hits = asyncio.run(check_rag())
    print(f"  Total Indexed Chunks: {chunks}")
    print(f"  Query Hits: {hits}")
