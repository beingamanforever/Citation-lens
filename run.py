"""Launch from a plugin folder without installing the project itself."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from citation_lens.server import main  # noqa: E402

if __name__ == "__main__":
    main()
