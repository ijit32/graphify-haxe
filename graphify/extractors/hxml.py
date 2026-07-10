"""HXML build-config extractor (line-based, no tree-sitter grammar)."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from graphify.extractors.base import _file_stem, _make_id


def extract_hxml(path: Path) -> dict:
    """Extract build directives from a .hxml file.

    HXML is Haxe's build-config format: one compiler flag per line, or
    multiple flags separated by spaces. Lines starting with ``#`` are
    comments. ``--next`` separates multi-target build configurations.

    This extractor captures ``-lib``, ``-cp``, ``--main`` / ``-main``,
    ``-D``, ``--interp`` directives as nodes, grouped by ``--next``
    target sections.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    stem = _file_stem(path)
    str_path = str(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    def add_node(nid: str, label: str, line: int) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({"id": nid, "label": label, "file_type": "code",
                          "source_file": str_path, "source_location": f"L{line}"})

    def add_edge(src: str, tgt: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", weight: float = 1.0,
                 context: str | None = None) -> None:
        edge = {"source": src, "target": tgt, "relation": relation,
                "confidence": confidence, "source_file": str_path,
                "source_location": f"L{line}", "weight": weight}
        if context:
            edge["context"] = context
        edges.append(edge)

    file_nid = _make_id(str(path))
    add_node(file_nid, path.name, 1)

    # Split into sections separated by --next
    lines = source.splitlines()
    sections: list[list[tuple[int, str]]] = [[]]  # list of (line_no, stripped_text)
    for i, raw_line in enumerate(lines):
        line = i + 1
        stripped = raw_line.strip()
        if stripped == "--next":
            sections.append([])
        elif stripped and not stripped.startswith("#"):
            sections[-1].append((line, stripped))

    # Create a target node for each section beyond the first
    for section_idx, section_lines in enumerate(sections):
        if section_idx == 0:
            target_nid = file_nid
        else:
            target_nid = _make_id(stem, f"target_{section_idx}")
            add_node(target_nid, f"target_{section_idx + 1}", 0)
            add_edge(file_nid, target_nid, "contains", 0, context="build_target")

        for raw_line_no, stripped in section_lines:
            try:
                tokens = shlex.split(stripped)
            except ValueError:
                tokens = stripped.split()

            if not tokens:
                continue

            key = tokens[0]
            val = tokens[1] if len(tokens) > 1 else ""

            if key in ("-lib", "--library"):
                lib_nid = _make_id("lib", val)
                add_node(lib_nid, val, raw_line_no)
                add_edge(target_nid, lib_nid, "depends_on", raw_line_no, context="library")
            elif key in ("-cp", "--class-path"):
                cp_nid = _make_id("classpath", val)
                add_node(cp_nid, val, raw_line_no)
                add_edge(target_nid, cp_nid, "references", raw_line_no, context="classpath")
            elif key in ("-main", "--main"):
                main_nid = _make_id("main", val)
                add_node(main_nid, val, raw_line_no)
                add_edge(target_nid, main_nid, "references", raw_line_no, context="main")
            elif key == "-D" and val:
                define_nid = _make_id("define", val)
                add_node(define_nid, val, raw_line_no)
                add_edge(target_nid, define_nid, "configures", raw_line_no, context="define")
            elif key == "--interp":
                interp_nid = _make_id("mod", "interp")
                add_node(interp_nid, "--interp", raw_line_no)
                add_edge(target_nid, interp_nid, "configures", raw_line_no, context="interp")

    return {"nodes": nodes, "edges": edges}
