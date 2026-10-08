import asyncio

import pytest

from citation_lens.papers import from_s2
from citation_lens.storage import Store


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "cache.sqlite")
    yield result
    result.close()


def run(coroutine):
    return asyncio.run(coroutine)


def s2(key, title, year=2020, cites=10, abstract="", refs=(), arxiv="", date=None):
    """A Semantic Scholar record; key doubles as the 40-hex paper ID suffix."""
    return from_s2(
        {
            "paperId": key.ljust(40, "0"),
            "title": title,
            "year": year,
            "publicationDate": date,
            "citationCount": cites,
            "abstract": abstract,
            "externalIds": {"ArXiv": arxiv} if arxiv else {},
            "references": [{"paperId": ref.ljust(40, "0")} for ref in refs],
        }
    )
