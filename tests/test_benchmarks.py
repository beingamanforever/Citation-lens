import asyncio
import importlib.util
import io
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

ROOT = Path(__file__).resolve().parents[1]


def test_live_benchmark_retains_unreadable_pdf_failure(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "live_benchmark", ROOT / "scripts/live_benchmark.py"
    )
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    content = DecodedStreamObject()
    content.set_data(b"1 0 0 rg 20 30 150 100 re f")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)

    async def search(papers, *args):
        paper = papers.remember(
            {
                "id": "OA:W1",
                "provider": "openalex",
                "title": "FlashAttention scanned paper",
                "pdf": "https://public.example/paper.pdf",
                "arxiv": "",
                "citations": 0,
            }
        )
        return [paper], 1

    async def neighbors(papers, *args):
        return [], {"sampled": False, "indexed": 0}

    async def fetch(web, *args, **kwargs):
        return output.getvalue()

    monkeypatch.setattr(benchmark.Papers, "search", search)
    monkeypatch.setattr(benchmark.Papers, "neighbors", neighbors)
    monkeypatch.setattr(benchmark.Web, "fetch", fetch)
    result = asyncio.run(benchmark.measure(tmp_path))
    assert result["paired_payload"]["readable_papers"] == 0
    assert result["paired_payload"]["reduction_percent"] is None
    assert result["full_text_attempts"][0]["included_in_payload"] is False
    assert "No extractable full text" in result["errors"][0]["error"]


def test_efficiency_benchmark_preserves_coverage_and_text(tmp_path):
    spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts/benchmark.py")
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    result = asyncio.run(benchmark.measure(tmp_path))
    for paired in result["batch_search"]:
        scalar, batch = paired["scalar"], paired["batch"]
        assert scalar["canonical_dois"] == batch["canonical_dois"]
        assert scalar["http_requests"] == batch["http_requests"] == 4
        assert scalar["initial_tool_calls"] == 4 and batch["initial_tool_calls"] == 1
        assert scalar["failed_searches"] == batch["failed_searches"]
        assert batch["failed_searches"] == (1 if paired["scenario"] == "partial-outage" else 0)
        assert batch["returned_cards"] == len(batch["canonical_dois"])
    read = result["paged_read"]
    assert read["pages"] > 1 and read["identical_complete_text"]
    assert read["overview_once_bytes"] < read["repeated_overview_bytes"]
