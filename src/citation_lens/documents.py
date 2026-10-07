"""Faithful HTML first; PDF page text and actual visual evidence on demand."""

import asyncio
import hashlib
import io
import os
import re
import threading
import uuid
import warnings
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify
from PIL import Image
from pypdf import PdfReader

from .graph import bounded, payload
from .papers import Papers, identifier

PDFIUM_LOCK = threading.Lock()


def html_document(data: bytes, url: str):
    soup = BeautifulSoup(data, "html.parser")
    body = soup.find("article") or soup.find(class_="ltx_document")
    if body is None:
        raise ValueError("No paper article found in HTML; try PDF fallback")
    math_fragments, missing_tex = {}, 0
    prefix = "CITATIONLENSMATH" + uuid.uuid4().hex
    for unwanted in body.select("script,style,nav,footer,header"):
        unwanted.decompose()
    for math in body.find_all("math"):
        annotation = math.find("annotation", attrs={"encoding": "application/x-tex"})
        latex = annotation.get_text() if annotation else math.get("alttext")
        if latex:
            token = prefix + str(len(math_fragments)) + "END"
            delimiter = "$$" if math.get("display") == "block" else "$"
            math_fragments[token] = delimiter + latex + delimiter
            math.replace_with(token)
        else:
            missing_tex += 1

    def restore_math(text):
        return re.sub(prefix + r"\d+END", lambda m: math_fragments[m.group()], text)

    figures = []
    for img in body.find_all("img", src=True):
        src = urljoin(url, img["src"])
        img["src"] = src
        parent = img.find_parent("figure")
        caption = parent.find("figcaption") if parent else None
        figures.append(
            {
                "index": len(figures),
                "url": src,
                "caption": restore_math(
                    caption.get_text(" ", strip=True) if caption else img.get("alt", "")
                )[:2000],
            }
        )
    text = markdownify(str(body), heading_style="ATX")
    text = restore_math(text)
    return {
        "text": text,
        "figures": figures,
        "format": "html",
        "warnings": [
            "HTML extraction: verify complex tables against their visual when consequential."
        ]
        + (
            [f"{missing_tex} MathML equations lacked LaTeX; inspect their PDF pages."]
            if missing_tex
            else []
        ),
    }


def pdf_document(data: bytes):
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF is not supported")
    if len(reader.pages) > 200:
        raise ValueError("PDF exceeds 200-page safety limit")
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    return {
        "text": "\n\n".join(f"## Page {i + 1}\n\n{t}" for i, t in enumerate(pages)),
        "figures": [],
        "format": "pdf",
        "page_count": len(pages),
        "warnings": [
            "PDF text may lose equations, reading order and table structure; inspect page images."
        ]
        + (
            ["No extractable text: use page images or configured Jina fallback."]
            if not any(p.strip() for p in pages)
            else []
        ),
    }


def render_pdf(data: bytes, page: int, max_edge: int):
    import pypdfium2 as pdfium

    # PDFium is not thread-safe, even when each request opens a separate document.
    with PDFIUM_LOCK, pdfium.PdfDocument(data) as document:
        bounded(page, 1, len(document), "page")
        pdf_page = document[page - 1]
        try:
            scale = min(2.5, max_edge / max(pdf_page.get_size()))
            bitmap = pdf_page.render(scale=scale)
            try:
                image = bitmap.to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()
            finally:
                bitmap.close()
        finally:
            pdf_page.close()


def normalize_image(data: bytes, max_edge: int):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as img:
            img.thumbnail((max_edge, max_edge))
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            return buffer.getvalue()


class Documents:
    def __init__(self, papers: Papers):
        self.papers = papers
        self.web = papers.web
        self.store = papers.store
        self.locks: dict[str, asyncio.Lock] = {}

    async def selected_paper(self, paper_id: str):
        paper = await self.papers.resolve(paper_id)
        requested = identifier(paper_id)
        if requested.startswith("ARXIV:") and re.search(r"v\d+$", requested):
            arxiv = requested[6:]
            paper = {**paper, "arxiv": arxiv, "pdf": "https://arxiv.org/pdf/" + arxiv}
        return paper

    async def load(self, paper_id: str):
        paper = await self.selected_paper(paper_id)
        key = "document:" + paper["id"]
        if re.search(r"v\d+$", paper.get("arxiv", "")):
            key += "@" + paper["arxiv"]
        async with self.locks.setdefault(key, asyncio.Lock()):
            if cached := self.store.load(key):
                return cached
            failures, document = [], None
            if paper.get("arxiv"):
                url = "https://arxiv.org/html/" + paper["arxiv"]
                try:
                    data = await self.web.fetch(url, ttl=86400 * 30, max_bytes=5_000_000)
                    document = await asyncio.to_thread(html_document, data, url)
                    if len(document["text"].strip()) < 200:
                        raise ValueError("HTML has too little paper content")
                except (RuntimeError, ValueError, TimeoutError) as exc:
                    failures.append(str(exc)[:200])
                    document = None
            if document is None:
                url = paper.get("pdf") or (
                    "https://arxiv.org/pdf/" + paper["arxiv"] if paper.get("arxiv") else None
                )
                if not url:
                    raise ValueError("No open full-text URL indexed. Try another identifier.")
                data = await self.web.fetch(url, ttl=86400 * 30)
                document = await asyncio.to_thread(pdf_document, data)
                if (
                    "No extractable text" in " ".join(document["warnings"])
                    and os.getenv("CITATION_LENS_USE_JINA") == "1"
                ):
                    text = await self.web.fetch("https://r.jina.ai/" + url, max_bytes=5_000_000)
                    document["text"] = text.decode()
                    document["warnings"].append(
                        "Jina Reader fallback; generated captions require visual verification."
                    )
                    document["format"] = "jina"
            if len(document["text"]) > 2_000_000:
                raise ValueError(
                    "Paper text exceeds two million characters; use a smaller document"
                )
            document.update(
                {
                    "paper_id": paper["id"],
                    "source_url": url,
                    "arxiv_version": paper.get("arxiv"),
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                    "text_sha256": hashlib.sha256(document["text"].encode()).hexdigest(),
                    "fallback_errors": failures,
                }
            )
            self.store.save(key, document, ttl=86400 * 30)
            return document

    async def read(self, paper_id: str, offset=0, max_chars=6000, outline_only=False):
        bounded(offset, 0, 2_000_000, "offset")
        bounded(max_chars, 1, 12000, "max_chars")
        document = await self.load(paper_id)
        text = document["text"]
        outline = [
            {"heading": m.group(2)[:120], "offset": m.start()}
            for m in re.finditer(r"(?m)^(#{1,6})\s+(.+)$", text)
        ]
        end = min(offset + max_chars, len(text))
        include_overview = outline_only or offset == 0
        return payload(
            {k: v for k, v in document.items() if k not in ("text", "figures")}
            | {
                "outline": outline[:40] if include_overview else [],
                "overview_included": include_overview,
                "outline_truncated": len(outline) > 40,
                "total_chars": len(text),
                "offset": offset,
                "text": "" if outline_only else text[offset:end],
                "next_offset": (end if end < len(text) else None) if not outline_only else 0,
                "figures": document["figures"][:12] if include_overview else [],
                "figure_count": len(document["figures"]),
                "figure_list_truncated": len(document["figures"]) > 12,
                "untrusted_content": True,
            }
        )

    async def visual(self, paper_id: str, page=1, figure_index: int | None = None, max_edge=1400):
        bounded(page, 1, 200, "page")
        bounded(max_edge, 256, 1800, "max_edge")
        paper = await self.selected_paper(paper_id)
        if figure_index is not None:
            document = await self.load(paper_id)
            if not document["figures"]:
                raise ValueError("No HTML figures indexed. Request a PDF page instead.")
            bounded(figure_index, 0, len(document["figures"]) - 1, "figure_index")
            figure = document["figures"][figure_index]
            data = await self.web.fetch(figure["url"], ttl=86400 * 30, max_bytes=10_000_000)
            try:
                png = await asyncio.to_thread(normalize_image, data, max_edge)
            except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise ValueError(
                    "Unsupported or unsafe figure image; inspect its PDF page"
                ) from None
            return {"paper_id": paper["id"], **figure}, png
        url = paper.get("pdf") or (
            "https://arxiv.org/pdf/" + paper["arxiv"] if paper.get("arxiv") else None
        )
        if not url:
            raise ValueError("No open PDF indexed for page rendering")
        data = await self.web.fetch(url, ttl=86400 * 30)
        png = await asyncio.to_thread(render_pdf, data, page, max_edge)
        return {
            "paper_id": paper["id"],
            "page": page,
            "source_url": url,
            "source_sha256": hashlib.sha256(data).hexdigest(),
        }, png
