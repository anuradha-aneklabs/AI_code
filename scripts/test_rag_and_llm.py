"""
Task 2 Verification: Test actual RAG retrieval and inspect LLM provider configuration.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harvey.rag.store import VectorStore
from harvey.rag.retriever import RAGRetriever
from harvey.config import load_config

async def test_rag():
    print("=" * 65)
    print("TASK 2: RAG RETRIEVAL & AI PROVIDER AUDIT")
    print("=" * 65)

    # 1. Inspect Vector Store
    store = VectorStore()
    total_chunks = store.collection.count()
    print(f"\n[1. CHROMA VECTOR STORE]")
    print(f"  Collection Name: {store.collection.name}")
    print(f"  Total Indexed Chunks: {total_chunks}")
    assert total_chunks > 0, "No chunks found in collection!"

    # 2. Test Retrieval
    retriever = RAGRetriever(store=store)
    queries = [
        "SaaS outbound sales pipeline automation",
        "Dental practice appointment booking",
        "Legal firm contract review",
    ]

    print(f"\n[2. DOCUMENT CHUNK RETRIEVAL]")
    for q in queries:
        chunks = await store.search(query=q, top_k=2)
        print(f"  Query: '{q}' -> Retrieved {len(chunks)} chunk(s):")
        for i, c in enumerate(chunks, 1):
            src = c.get("source", "unknown")
            text_snippet = c.get("text", "").replace("\n", " ").strip()[:90]
            dist = c.get("distance", "N/A")
            print(f"    [{i}] Source: {src} | Dist/Score: {dist}")
            print(f"        Snippet: {text_snippet}...")

    # 3. Check Formatted Prompt Grounding
    from types import SimpleNamespace
    mock_prospect = SimpleNamespace(industry="SaaS", title="VP Sales", personalization_notes="Scaling pipeline")
    mock_company = SimpleNamespace(name="CloudCorp", description="Enterprise B2B SaaS platform")
    prospect_chunks = await retriever.retrieve_for_prospect(mock_prospect, company=mock_company, top_k=2)
    context_str = retriever.format_prompt_context(prospect_chunks)
    print(f"\n[3. GROUNDED PROMPT CONTEXT]")
    print(f"  Context String Length: {len(context_str)} characters")
    print(f"  Context Preview:\n{context_str[:250]}...\n")

    # 4. Check Configured AI Providers
    print(f"[4. AI PROVIDER CONFIGURATION CHECK]")
    config = load_config()
    configured_provider = getattr(config, "ai_provider", None) or os.environ.get("HARVEY_AI_PROVIDER", "None")
    print(f"  Configured Provider: {configured_provider}")

    # Check API keys presence (masked)
    keys_to_check = [
        ("Anthropic", "ANTHROPIC_API_KEY"),
        ("OpenAI", "OPENAI_API_KEY"),
        ("Google Gemini", "GEMINI_API_KEY"),
        ("Ollama", "OLLAMA_HOST"),
    ]
    available_providers = []
    for name, env_var in keys_to_check:
        val = os.environ.get(env_var, "").strip()
        has_key = bool(val)
        status_str = f"Set ({len(val)} chars, masked)" if has_key else "NOT SET"
        print(f"  - {name} ({env_var}): {status_str}")
        if has_key:
            available_providers.append((name, env_var))

    # Also check config file for credentials
    claude_cfg = getattr(config, "claude", None)
    if claude_cfg and getattr(claude_cfg, "api_key", None):
        print(f"  - Claude in config: Set (masked)")
        available_providers.append(("Claude (Config)", "config.claude.api_key"))

    print(f"\n  Available Active LLM Providers: {[p[0] for p in available_providers] if available_providers else 'NONE'}")
    return available_providers

if __name__ == "__main__":
    providers = asyncio.run(test_rag())
