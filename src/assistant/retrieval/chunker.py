"""Markdown loading and chunking.

Strategy: split on Markdown headings first (a section is the natural unit of meaning in
runbooks/incident reports), then split any oversized section into overlapping windows.
Every chunk keeps its document title and section heading in metadata so citations can point at
"INC-2025-0419 > Root cause" rather than a bare chunk id.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import frontmatter

from assistant.retrieval.dates import date_to_int
from assistant.retrieval.models import Chunk

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def split_sections(markdown: str) -> list[tuple[str, str]]:
    """Return ``[(section_heading, section_text), ...]`` preserving document order."""
    matches = list(HEADING_RE.finditer(markdown))
    if not matches:
        return [("", markdown.strip())]
    sections: list[tuple[str, str]] = []
    preamble = markdown[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[m.end() : end].strip()
        sections.append((m.group(2).strip(), body))
    return sections


def window(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        # Prefer to cut at a paragraph/sentence boundary.
        cut = text.rfind("\n", start, end)
        if cut == -1 or cut <= start + size // 2:
            cut = text.rfind(". ", start, end)
        if cut == -1 or cut <= start + size // 2 or end == len(text):
            cut = end
        out.append(text[start:cut].strip())
        if cut >= len(text):
            break
        start = max(cut - overlap, start + 1)
    return [o for o in out if o]


def load_document(path: Path, chunk_size: int, overlap: int) -> list[Chunk]:
    post = frontmatter.load(path)
    meta = {k: (str(v) if v is not None else "") for k, v in post.metadata.items()}
    doc_id = path.stem
    title = meta.get("title", doc_id)
    department = meta.get("department", "general")
    base_meta = {
        "doc_id": doc_id,
        "title": title,
        "department": department,
        "document_type": meta.get("document_type", "unknown"),
        "access_level": meta.get("access_level", "internal"),
        "created_date": meta.get("created_date", ""),
        # Pinecone range operators ($gte/$lte) only accept numbers, so dates are also stored as YYYYMMDD ints.
        "created_ts": date_to_int(meta.get("created_date", "")),
        "source_path": str(path.name),
    }
    for extra in ("tags", "severity", "system"):
        if extra in meta:
            base_meta[extra] = meta[extra]

    chunks: list[Chunk] = []
    for section, body in split_sections(post.content):
        # Drop the H1 title-only section (it duplicates the title metadata).
        if not body and section == title:
            continue
        section_text = f"{section}\n{body}".strip() if section else body
        for i, piece in enumerate(window(section_text, chunk_size, overlap)):
            digest = hashlib.sha1(f"{doc_id}:{section}:{i}:{piece[:40]}".encode()).hexdigest()[:10]
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}--{_slug(section) or 'body'}--{i}-{digest}",
                    doc_id=doc_id,
                    title=title,
                    section=section,
                    text=piece,
                    namespace=department,
                    metadata={**base_meta, "section": section, "text": piece},
                )
            )
    return chunks


def load_corpus(docs_dir: Path, chunk_size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        chunks.extend(load_document(path, chunk_size, overlap))
    return chunks
