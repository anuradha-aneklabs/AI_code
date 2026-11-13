"""Document ingestion and chunking parser for PDF, TXT, and Markdown."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pypdf

@dataclass
class DocumentChunk:
    chunk_id: str
    text: str
    source: str
    page: int = 1
    chunk_index: int = 1
    file_type: str = "txt"
    ingested_at: str = ""

    def __post_init__(self):
        if not self.ingested_at:
            self.ingested_at = datetime.now(timezone.utc).isoformat()

    def to_metadata(self) -> dict[str, Any]:
        """Convert metadata to flat dictionary for vector store storage."""
        return {
            "source": self.source,
            "page": self.page,
            "chunk_index": self.chunk_index,
            "file_type": self.file_type,
            "ingested_at": self.ingested_at,
        }


def chunk_text(
    text: str,
    source: str = "unknown",
    page: int = 1,
    file_type: str = "txt",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[DocumentChunk]:
    """Split text into overlapping semantic chunks with deterministic IDs."""
    text = text.strip()
    if not text:
        return []

    # Clean redundant whitespace
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    # First split into paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # Break oversized paragraphs into sentence or sub-chunk units
    units: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            units.append(para)
        else:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            curr = ""
            for s in sentences:
                if not curr:
                    curr = s
                elif len(curr) + len(s) + 1 <= chunk_size:
                    curr += " " + s
                else:
                    units.append(curr)
                    curr = s
            if curr:
                while len(curr) > chunk_size:
                    units.append(curr[:chunk_size])
                    curr = curr[chunk_size - chunk_overlap:]
                if curr.strip():
                    units.append(curr.strip())

    raw_chunks: list[str] = []
    current_chunk = ""
    for u in units:
        if not current_chunk:
            current_chunk = u
        elif len(current_chunk) + len(u) + 2 <= chunk_size:
            current_chunk += "\n\n" + u
        else:
            raw_chunks.append(current_chunk)
            overlap = (
                current_chunk[-chunk_overlap:]
                if len(current_chunk) > chunk_overlap
                else ""
            )
            current_chunk = (overlap + " " + u).strip()

    if current_chunk:
        raw_chunks.append(current_chunk)

    safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", source)
    now = datetime.now(timezone.utc).isoformat()

    chunks: list[DocumentChunk] = []
    for idx, chunk_str in enumerate(raw_chunks, 1):
        clean_str = chunk_str.strip()
        if not clean_str:
            continue
        chunk_id = f"{safe_name}_p{page}_c{idx}"
        chunks.append(
            DocumentChunk(
                chunk_id=chunk_id,
                text=clean_str,
                source=source,
                page=page,
                chunk_index=idx,
                file_type=file_type,
                ingested_at=now,
            )
        )

    return chunks


def parse_pdf(file_path: Path | str) -> list[DocumentChunk]:
    """Extract and chunk pages from a PDF document."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")

    reader = pypdf.PdfReader(str(path))
    chunks: list[DocumentChunk] = []
    
    for page_idx, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        page_chunks = chunk_text(
            text=text,
            source=path.name,
            page=page_idx,
            file_type="pdf",
        )
        chunks.extend(page_chunks)
        
    return chunks


def parse_markdown(file_path: Path | str) -> list[DocumentChunk]:
    """Parse and chunk a Markdown document."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    return chunk_text(
        text=text,
        source=path.name,
        page=1,
        file_type="md",
    )


def parse_text(file_path: Path | str) -> list[DocumentChunk]:
    """Parse and chunk a plain text document."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Text file not found: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    return chunk_text(
        text=text,
        source=path.name,
        page=1,
        file_type="txt",
    )


def ingest_document(file_path: Path | str) -> list[DocumentChunk]:
    """Detect file extension and parse into DocumentChunks with metadata."""
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return parse_pdf(path)
    elif ext in (".md", ".markdown"):
        return parse_markdown(path)
    elif ext in (".txt", ".text"):
        return parse_text(path)
    else:
        raise ValueError(f"Unsupported document format: '{ext}'. Supported: .pdf, .md, .txt")
