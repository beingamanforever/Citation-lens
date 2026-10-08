import asyncio
import io
from pathlib import Path

import pytest
from conftest import run
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from citation_lens.documents import Documents, html_document, pdf_document, render_pdf
from citation_lens.network import Web

ROOT = Path(__file__).resolve().parents[1]


class CachedPaper:
    def __init__(self, store):
        self.store, self.web = store, Web(store)

    async def resolve(self, paper_id):
        return {"id": "OA:W1", "arxiv": "", "pdf": None}


def test_html_keeps_math_tables_captions_and_pages_losslessly(store):
    doc = html_document(
        (ROOT / "tests/fixtures/paper.html").read_bytes(), "https://arxiv.org/html/1706.03762"
    )
    assert "$QK^T / \\sqrt{d}$" in doc["text"] and "12 ms" in doc["text"]
    assert doc["figures"][0]["url"] == "https://arxiv.org/html/figures/architecture.png"
    assert "$K$" in doc["figures"][0]["caption"] and "Ignore navigation" not in doc["text"]
    doc.update(paper_id="OA:W1", source_url="https://public.example/paper", source_sha256="x")
    store.save("document:OA:W1", doc)
    papers = CachedPaper(store)
    documents = Documents(papers)

    async def exercise():
        outline = await documents.read("OA:W1", outline_only=True)
        assert not outline["text"] and outline["outline"] and outline["untrusted_content"]
        parts, offset = [], 0
        while offset is not None:
            page = await documents.read("OA:W1", offset=offset, max_chars=90)
            assert bool(page["figures"]) is (offset == 0)
            parts.append(page["text"])
            offset = page["next_offset"]
        assert "".join(parts) == doc["text"]
        await papers.web.close()

    run(exercise())


def example_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(b"1 0 0 rg 20 30 150 100 re f BT /F1 14 Tf 20 250 Td (Evidence diagram) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_text_and_rendered_figure():
    data = example_pdf()
    assert "Evidence diagram" in pdf_document(data)["text"]
    image = Image.open(io.BytesIO(render_pdf(data, 1, 600)))
    red = image.getpixel((80, image.height - 100))
    assert max(image.size) <= 600 and red[0] > 240 and red[1] < 10
    with pytest.raises(ValueError):
        render_pdf(data, 2, 600)


def test_concurrent_rendering_is_stable():
    data = example_pdf()
    expected = render_pdf(data, 1, 300)

    async def exercise():
        images = await asyncio.gather(
            *(asyncio.to_thread(render_pdf, data, 1, 300) for _ in range(12))
        )
        assert all(image == expected for image in images)

    run(exercise())
