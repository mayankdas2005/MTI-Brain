"""Render the neo4j_analytics LangGraph pipeline as a Mermaid diagram and PNG.

Compiles the analytics graph, exports it as a Mermaid definition file
(``.mmd``), and fetches a PNG rendering via the mermaid.ink service.
Both files are saved to ``assets/``.

Usage::

    python backend/scripts/render_graph.py
"""

import base64
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.neo4j_analytics.graph import compile_graph

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def render_mermaid(graph, save_as: str = "pipeline_graph.png"):
    mermaid_str = graph.draw_mermaid()
    mermaid_str = re.sub(r"^---.*?---\s*", "", mermaid_str, flags=re.DOTALL).strip()

    stem = save_as.replace(".png", "")
    mmd_path = OUT_DIR / f"{stem}.mmd"
    mmd_path.write_text(mermaid_str, encoding="utf-8")
    print(f"Saved {mmd_path}")

    encoded = base64.urlsafe_b64encode(mermaid_str.encode("utf-8")).decode("utf-8")
    png_path = OUT_DIR / save_as
    render_urls = [
        f"https://mermaid.ink/img/{encoded}",
        f"https://kroki.io/mermaid/png/{encoded}",
    ]
    for url in render_urls:
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            png_path.write_bytes(resp.content)
            print(f"Saved {png_path}")
            break
        except Exception as exc:
            print(f"  [warn] {url} failed: {exc}")
    else:
        print(f"  [skip] PNG render unavailable — open {mmd_path} in https://mermaid.live")


if __name__ == "__main__":
    graph = compile_graph().compile().get_graph()
    render_mermaid(graph, "analytics_graph.png")
