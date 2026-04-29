"""Render the LangGraph pipeline as a Mermaid diagram and PNG image.

Compiles the pipeline state graph, exports it as a Mermaid definition
file (``.mmd``), and fetches a PNG rendering via the mermaid.ink service.
Both files are saved to ``backend/app/data/``.

Usage::

    python backend/scripts/render_graph.py
"""

import base64
import re
import sys
from pathlib import Path

import requests

# Add backend to path so we can import the graph builder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.pipeline.graph import _build_graph

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data"


def render_mermaid(graph, save_as: str = None):
    """Export a LangGraph drawable as a Mermaid file and PNG image.

    Strips any YAML front-matter from the generated Mermaid string,
    writes the ``.mmd`` text file, then fetches a PNG from the
    ``mermaid.ink`` rendering service.

    Args:
        graph: A LangGraph drawable object (from ``get_graph()``).
        save_as: Optional filename for the PNG output. Defaults to
            ``"pipeline_graph.png"``.
    """
    mermaid_str = graph.draw_mermaid()
    mermaid_str = re.sub(r"^---.*?---\s*", "", mermaid_str, flags=re.DOTALL).strip()

    # Save .mmd file
    mmd_path = OUT_DIR / "pipeline_graph.mmd"
    mmd_path.write_text(mermaid_str, encoding="utf-8")
    print(f"Saved {mmd_path}")

    # Fetch PNG from mermaid.ink
    encoded = base64.urlsafe_b64encode(mermaid_str.encode("utf-8")).decode("utf-8")
    url = f"https://mermaid.ink/img/{encoded}"

    png_path = OUT_DIR / (save_as or "pipeline_graph.png")
    png_bytes = requests.get(url, timeout=15).content
    png_path.write_bytes(png_bytes)
    print(f"Saved {png_path}")


if __name__ == "__main__":
    drawable = _build_graph().compile().get_graph()
    render_mermaid(drawable, "pipeline_graph.png")
