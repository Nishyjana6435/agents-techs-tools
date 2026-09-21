"""Render docs/architecture.png with matplotlib (no graphviz dependency)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mp
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parents[1] / "docs" / "architecture.png"
C = {
    "ui": "#DCEBFF",
    "api": "#E6F4EA",
    "graph": "#FFF8E1",
    "data": "#F3E8FF",
    "tools": "#FFE4E1",
    "obs": "#EEEEEE",
}
W, H = 1.5, 0.7  # node size


def box(ax, x, y, w, h, text, color, fontsize=9, bold=False):
    ax.add_patch(
        mp.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc=color, ec="#333", lw=1
        )
    )
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontweight="bold" if bold else "normal",
    )


def arrow(ax, p1, p2, text="", style="-|>", ls="-", dx=0.0, dy=0.12):
    ax.annotate(
        "",
        xy=p2,
        xytext=p1,
        arrowprops={
            "arrowstyle": style,
            "color": "#333",
            "lw": 1.1,
            "linestyle": ls,
            "shrinkA": 0,
            "shrinkB": 0,
        },
    )
    if text:
        ax.text(
            (p1[0] + p2[0]) / 2 + dx, (p1[1] + p2[1]) / 2 + dy, text, fontsize=7.5, ha="center", color="#444"
        )


def main() -> None:
    fig, ax = plt.subplots(figsize=(16, 10))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")
    fig.suptitle("Meridian Knowledge Assistant - architecture", fontsize=15, fontweight="bold", y=0.97)

    # ---- top row: client, api, observability, security
    box(ax, 0.3, 7.5, 2.6, 1.8, "Streamlit UI\nchat window +\nAgent Activity Panel", C["ui"], bold=True)
    box(
        ax,
        3.5,
        7.5,
        3.2,
        1.8,
        "FastAPI (async)\nJWT auth + RBAC\ntoken-bucket rate limit\nSSE streaming",
        C["api"],
        bold=True,
    )
    box(
        ax,
        7.3,
        7.5,
        3.9,
        1.8,
        "LangSmith\ntraces: every turn, node transition,\nLLM call, retrieval, tool call\n+ user feedback",
        C["obs"],
        bold=True,
    )
    box(
        ax,
        11.6,
        7.5,
        4.1,
        1.8,
        "Security controls\ninjection screening (input + documents)\ninput / tool / content validation\noutput guard: citations, secrets, brand",
        C["obs"],
        fontsize=8.5,
        bold=True,
    )
    arrow(ax, (2.9, 8.4), (3.5, 8.4), "SSE", "<|-|>", dy=0.15)
    arrow(ax, (6.7, 8.4), (7.3, 8.4), "", "-|>", ls="--")
    arrow(ax, (5.1, 7.5), (5.1, 7.0), "graph.astream(custom, messages, updates)", dx=1.9, dy=-0.1)

    # ---- graph container
    ax.add_patch(
        mp.FancyBboxPatch(
            (0.3, 0.5), 10.8, 6.5, boxstyle="round,pad=0.02", fc=C["graph"], ec="#b8860b", lw=1.4
        )
    )
    ax.text(
        0.5,
        6.7,
        "LangGraph orchestration  (checkpointed per thread; every node is a LangSmith span)",
        fontsize=10,
        fontweight="bold",
    )

    N = {  # name: (x, y)
        "guard": (0.6, 3.9),
        "memory_load": (2.35, 3.9),
        "supervisor": (4.1, 3.9),
        "retrieval": (6.2, 5.5),
        "research (RLM)": (6.2, 3.9),
        "tools": (6.2, 2.4),
        "approval\n(interrupt)": (6.2, 0.8),
        "response": (8.0, 3.9),
        "validator": (9.3, 2.4),
        "rewrite": (9.3, 0.8),
        "memory_update": (9.3, 5.5),
    }
    for name, (x, y) in N.items():
        box(ax, x, y, W, H, name, "#FFFFFF", fontsize=8.5)

    def right(n):
        return (N[n][0] + W, N[n][1] + H / 2)

    def left(n):
        return (N[n][0], N[n][1] + H / 2)

    def top(n, f=0.5):
        return (N[n][0] + W * f, N[n][1] + H)

    def bottom(n, f=0.5):
        return (N[n][0] + W * f, N[n][1])

    arrow(ax, right("guard"), left("memory_load"))
    arrow(ax, right("memory_load"), left("supervisor"))
    arrow(ax, right("supervisor"), left("retrieval"), "route", dx=-0.25)
    arrow(ax, right("supervisor"), left("research (RLM)"))
    arrow(ax, right("supervisor"), left("tools"))
    arrow(ax, right("retrieval"), top("response", 0.5))
    arrow(ax, right("research (RLM)"), left("response"))
    arrow(ax, right("tools"), bottom("response", 0.5))
    arrow(ax, bottom("tools", 0.35), top("approval\n(interrupt)", 0.35), "admin tool", dx=-0.55)
    arrow(ax, top("approval\n(interrupt)", 0.7), bottom("tools", 0.7), "approve / reject", dx=0.75)
    arrow(ax, bottom("response", 0.6), (N["validator"][0] + 0.3, N["validator"][1] + H), "draft", dx=-0.3)
    arrow(ax, bottom("validator", 0.35), top("rewrite", 0.35), "fail", dx=-0.35)
    arrow(ax, top("rewrite", 0.7), bottom("validator", 0.7), "", ls="--")
    arrow(ax, top("validator", 0.9), bottom("memory_update", 0.9), "pass", dx=0.3)

    # ---- right column: data + tools
    box(
        ax,
        11.6,
        5.8,
        4.1,
        1.1,
        "Pinecone (dense vectors)\nnamespace per department, metadata filters",
        C["data"],
    )
    box(ax, 11.6, 4.5, 4.1, 1.1, "BM25 sparse index\nidentifier-aware tokens", C["data"])
    box(
        ax,
        11.6,
        3.2,
        4.1,
        1.1,
        "Memory: checkpointer (thread state)\n+ long-term user profile (JSON)",
        C["data"],
    )
    box(
        ax,
        11.6,
        0.8,
        4.1,
        2.0,
        "Tool registry\nRBAC -> approval -> validation -> timeout\nknowledge_search | python_analysis (sandbox)\nmcp.* (directory, services, incidents)\nescalate_incident / reindex (admin + HITL)",
        C["tools"],
        fontsize=8,
    )
    arrow(
        ax,
        (7.7, 6.2),
        (11.6, 6.45),
        "hybrid search: dense (Pinecone) + sparse (BM25) -> RRF -> rerank",
        dy=0.15,
    )
    arrow(ax, (11.6, 5.05), (11.6, 5.8), "", "<|-|>", ls="--")
    arrow(ax, (10.8, 5.7), (11.6, 3.9), "", ls=":")
    arrow(ax, (7.7, 2.45), (11.6, 1.9), "tool calls (also from retrieval & research)", dy=-0.35, dx=0.6)
    fig.savefig(OUT, dpi=150, bbox_inches="tight")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
