"""Haxe extractor (tree-sitter)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from graphify.extractors.base import _LANGUAGE_BUILTIN_GLOBALS, _file_stem, _make_id, _read_text


_HAXE_BUILTIN_TYPES = frozenset({
    "String", "Int", "Float", "Bool", "Void", "Dynamic",
    "Array", "Map", "Null", "Class", "Enum", "EReg",
    "Date", "Math", "Type", "Reflect", "Std", "Lambda",
    "Xml", "Json", "Http",
})


_HAXE_ACCESS_MODIFIERS = frozenset({
    "public", "private", "static", "inline", "override", "dynamic", "extern",
})


def _collect_modifiers(node) -> list[str]:
    """Return a list of access modifier keywords appearing as direct children."""
    mods = []
    for c in node.children:
        if c.type in _HAXE_ACCESS_MODIFIERS:
            mods.append(c.type)
    return mods


def _has_extern_keyword(node) -> bool:
    return any(c.type == "extern" for c in node.children)


def _package_prefix(root, source: bytes) -> str:
    """Extract package prefix from root node, e.g. 'com.example'."""
    parts = []
    for child in root.children:
        if child.type == "package":
            for sub in child.children:
                if sub.type == "package_name":
                    name = _read_text(sub, source)
                    if name:
                        parts.append(name)
            if parts:
                return "_".join(parts)
    return ""


def extract_haxe(path: Path) -> dict:
    """Extract types, methods, imports, and calls from a .hx file."""
    try:
        import tree_sitter_haxe as tshx
        from tree_sitter import Language, Parser
    except ImportError:
        return {"nodes": [], "edges": [], "error": "tree_sitter_haxe not installed"}

    try:
        language = Language(tshx.language())
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    stem = _file_stem(path)
    str_path = str(path)
    pkg = _package_prefix(root, source)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    function_bodies: list[tuple[str, Any]] = []

    def pkg_id(*parts: str) -> str:
        if pkg:
            return _make_id(pkg, *parts)
        return _make_id(*parts)

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

    def _collect_type_refs(node, out: list[tuple[str, int]]) -> None:
        if node is None:
            return
        t = node.type
        if t == "TypePath":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _read_text(name_node, source)
                if name and name not in _HAXE_BUILTIN_TYPES and name not in _LANGUAGE_BUILTIN_GLOBALS:
                    out.append((name, node.start_point[0] + 1))
            else:
                for c in node.children:
                    if c.type == "type_name":
                        name = _read_text(c, source)
                        if name and name not in _HAXE_BUILTIN_TYPES:
                            out.append((name, c.start_point[0] + 1))
        elif t in ("ComplexType", "TAnonymous"):
            for c in node.children:
                _collect_type_refs(c, out)
        else:
            for c in node.children:
                _collect_type_refs(c, out)

    def _emit_type_refs(owner_nid: str, node, line: int) -> None:
        refs: list[tuple[str, int]] = []
        _collect_type_refs(node, refs)
        for ref_name, ref_line in refs:
            tgt_nid = _make_id(ref_name)
            if tgt_nid not in seen_ids:
                add_node(tgt_nid, ref_name, ref_line)
            if tgt_nid != owner_nid:
                add_edge(owner_nid, tgt_nid, "references", ref_line, context="type")

    def _emit_metadata(owner_nid: str, node, line: int) -> None:
        """Emit 'configures' edges for metadata annotations on a declaration."""
        for child in node.children:
            if child.type == "MetaDataEntry":
                name_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                if name_node:
                    meta_name = _read_text(name_node, source)
                    meta_nid = _make_id("meta", meta_name)
                    if meta_nid not in seen_ids:
                        add_node(meta_nid, f"@:{meta_name}", line)
                    add_edge(owner_nid, meta_nid, "configures", line, context="metadata")

    def _get_method_details(node):
        """Extract name, return type, params, body, modifiers, type params from a ClassMethod."""
        name = None
        ret = None
        params = None
        body = None
        line = node.start_point[0] + 1
        seen_func_keyword = False
        type_params: list[Any] = []

        for child in node.children:
            ct = child.type
            if ct == "function":
                seen_func_keyword = True
            elif ct == "identifier" and seen_func_keyword and name is None:
                name = _read_text(child, source)
            elif ct == "FunctionArg":
                if params is None:
                    params = child
            elif ct == "ComplexType" and not seen_func_keyword:
                continue
            elif ct == "ComplexType" and seen_func_keyword and name is not None:
                ret = child
            elif ct == "TypeParameter":
                type_params.append(child)
            elif ct == "EBlock":
                body = child
        return name, ret, params, body, line, type_params

    def walk(node, parent_type_nid: str | None = None) -> None:
        t = node.type

        if t == "ClassType":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            type_name = _read_text(name_node, source)
            line = node.start_point[0] + 1
            type_nid = pkg_id(stem, type_name)
            add_node(type_nid, type_name, line)
            add_edge(file_nid, type_nid, "contains", line)
            _emit_metadata(type_nid, node, line)

            mods = _collect_modifiers(node)
            if "extern" in mods:
                ext_nid = _make_id("mod", "extern")
                if ext_nid not in seen_ids:
                    add_node(ext_nid, "extern", line)
                add_edge(type_nid, ext_nid, "configures", line, context="modifier")

            # Generic type parameters
            for tp in node.children:
                if tp.type == "TypeParameter":
                    tp_name = _read_text(tp, source)
                    tp_line = tp.start_point[0] + 1
                    tp_nid = _make_id(type_nid, "T", tp_name)
                    add_node(tp_nid, tp_name, tp_line)
                    add_edge(type_nid, tp_nid, "contains", tp_line, context="type_param")

            ext_node = node.child_by_field_name("extends")
            if ext_node:
                ext_name_node = ext_node.child_by_field_name("name")
                if ext_name_node:
                    parent_name = _read_text(ext_name_node, source)
                    parent_nid = _make_id(parent_name)
                    if parent_nid not in seen_ids:
                        add_node(parent_nid, parent_name, line)
                    add_edge(type_nid, parent_nid, "inherits", line)

            for impl_node in node.children_by_field_name("implements"):
                impl_name_node = impl_node.child_by_field_name("name")
                if impl_name_node:
                    impl_name = _read_text(impl_name_node, source)
                    impl_nid = _make_id(impl_name)
                    if impl_nid not in seen_ids:
                        add_node(impl_nid, impl_name, line)
                    add_edge(type_nid, impl_nid, "implements", line)

            for child in node.children:
                if child.type in ("ClassVar", "ClassMethod"):
                    walk(child, parent_type_nid=type_nid)
            return

        if t == "AbstractType":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            type_name = _read_text(name_node, source)
            line = node.start_point[0] + 1
            type_nid = pkg_id(stem, type_name)
            add_node(type_nid, type_name, line)
            add_edge(file_nid, type_nid, "contains", line)
            _emit_metadata(type_nid, node, line)

            # Generic type parameters
            for tp in node.children:
                if tp.type == "TypeParameter":
                    tp_name = _read_text(tp, source)
                    tp_line = tp.start_point[0] + 1
                    tp_nid = _make_id(type_nid, "T", tp_name)
                    add_node(tp_nid, tp_name, tp_line)
                    add_edge(type_nid, tp_nid, "contains", tp_line, context="type_param")

            type_node = node.child_by_field_name("type")
            if type_node:
                _emit_type_refs(type_nid, type_node, line)

            for child in node.children:
                if child.type in ("ClassVar", "ClassMethod"):
                    walk(child, parent_type_nid=type_nid)
            return

        if t == "EnumType":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            enum_name = _read_text(name_node, source)
            line = node.start_point[0] + 1
            enum_nid = pkg_id(stem, enum_name)
            add_node(enum_nid, enum_name, line)
            add_edge(file_nid, enum_nid, "contains", line)
            _emit_metadata(enum_nid, node, line)

            # Generic type parameters
            for tp in node.children:
                if tp.type == "TypeParameter":
                    tp_name = _read_text(tp, source)
                    tp_line = tp.start_point[0] + 1
                    tp_nid = _make_id(enum_nid, "T", tp_name)
                    add_node(tp_nid, tp_name, tp_line)
                    add_edge(enum_nid, tp_nid, "contains", tp_line, context="type_param")

            for child in node.children:
                if child.type == "EnumConstructor":
                    const_name_node = child.child_by_field_name("name")
                    if const_name_node:
                        const_name = _read_text(const_name_node, source)
                        const_line = child.start_point[0] + 1
                        const_nid = _make_id(enum_nid, const_name)
                        add_node(const_nid, const_name, const_line)
                        add_edge(enum_nid, const_nid, "contains", const_line)

                    # Enum constructor parameters
                    for arg in child.children:
                        if arg.type != "FunctionArg":
                            continue
                        arg_name_node = arg.child_by_field_name("name")
                        if arg_name_node:
                            arg_name = _read_text(arg_name_node, source)
                            arg_line = arg.start_point[0] + 1
                            arg_nid = _make_id(const_nid, arg_name)
                            add_node(arg_nid, arg_name, arg_line)
                            add_edge(const_nid, arg_nid, "contains", arg_line, context="param")
                        arg_type = arg.child_by_field_name("type")
                        if arg_type:
                            _emit_type_refs(const_nid, arg_type, arg_line)
            return

        if t == "DefType":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            typedef_name = _read_text(name_node, source)
            line = node.start_point[0] + 1
            typedef_nid = pkg_id(stem, typedef_name)
            add_node(typedef_nid, typedef_name, line)
            add_edge(file_nid, typedef_nid, "contains", line)
            _emit_metadata(typedef_nid, node, line)

            # Generic type parameters
            for tp in node.children:
                if tp.type == "TypeParameter":
                    tp_name = _read_text(tp, source)
                    tp_line = tp.start_point[0] + 1
                    tp_nid = _make_id(typedef_nid, "T", tp_name)
                    add_node(tp_nid, tp_name, tp_line)
                    add_edge(typedef_nid, tp_nid, "contains", tp_line, context="type_param")

            type_node = node.child_by_field_name("type")
            if type_node:
                _emit_type_refs(typedef_nid, type_node, line)
            return

        if t == "ClassVar":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            var_name = _read_text(name_node, source)
            line = node.start_point[0] + 1
            if parent_type_nid:
                var_nid = _make_id(parent_type_nid, var_name)
                add_node(var_nid, f".{var_name}", line)
                add_edge(parent_type_nid, var_nid, "contains", line)
            else:
                var_nid = pkg_id(stem, var_name)
                add_node(var_nid, var_name, line)
                add_edge(file_nid, var_nid, "contains", line)
            _emit_metadata(var_nid, node, line)

            mods = _collect_modifiers(node)
            for m in ("static", "inline", "override", "dynamic", "final"):
                if m in mods:
                    mod_nid = _make_id("mod", m)
                    if mod_nid not in seen_ids:
                        add_node(mod_nid, m, line)
                    add_edge(var_nid, mod_nid, "configures", line, context="modifier")

            # Property getter/setter links
            for child in node.children:
                if child.type == "property_accessor":
                    for pa in child.children:
                        if pa.type in ("get", "set") or (
                            pa.type == "property_access"
                            and pa.children
                            and pa.children[0].type in ("get", "set")
                        ):
                            acc_type = pa.children[0].type if pa.type == "property_access" else pa.type
                            acc_nid = _make_id("mod", var_name, acc_type)
                            if acc_nid not in seen_ids:
                                add_node(acc_nid, acc_type, line)
                            add_edge(var_nid, acc_nid, "configures", line, context=f"property_{acc_type}")

            type_node = node.child_by_field_name("type")
            if type_node:
                _emit_type_refs(var_nid, type_node, line)
            return

        if t == "ClassMethod":
            func_name, ret, params, body, line, type_params = _get_method_details(node)
            if func_name is None:
                return

            mods = _collect_modifiers(node)

            if parent_type_nid:
                func_nid = _make_id(parent_type_nid, func_name)
                add_node(func_nid, f".{func_name}()", line)
                add_edge(parent_type_nid, func_nid, "method", line)
                _emit_metadata(func_nid, node, line)
            else:
                func_nid = pkg_id(stem, func_name)
                add_node(func_nid, f"{func_name}()", line)
                add_edge(file_nid, func_nid, "contains", line)

            # Modifier edges
            for m in ("public", "private", "static", "inline", "override", "dynamic"):
                if m in mods:
                    mod_nid = _make_id("mod", m)
                    if mod_nid not in seen_ids:
                        add_node(mod_nid, m, line)
                    add_edge(func_nid, mod_nid, "configures", line, context="modifier")
            if "override" in mods:
                add_edge(func_nid, func_nid, "overrides", line, confidence="INFERRED", weight=0.5)

            # Method type parameters
            for tp in type_params:
                tp_name = _read_text(tp, source)
                tp_line = tp.start_point[0] + 1
                tp_nid = _make_id(func_nid, "T", tp_name)
                add_node(tp_nid, tp_name, tp_line)
                add_edge(func_nid, tp_nid, "contains", tp_line, context="type_param")

            if ret:
                _emit_type_refs(func_nid, ret, line)
            if params:
                for arg in params.children:
                    if arg.type == "FunctionArg":
                        arg_type = arg.child_by_field_name("type")
                        if arg_type:
                            _emit_type_refs(func_nid, arg_type, line)

            if body:
                function_bodies.append((func_nid, body))
            return

        if t == "EFunction":
            for child in node.children:
                walk(child, parent_type_nid)

            for child in node.children:
                if child.type == "EBlock":
                    function_bodies.append((parent_type_nid or file_nid, child))
                elif child.type in ("EReturn", "ECall", "EBinop"):
                    dummy = {"type": "dummy_block", "children": [child]}
                    function_bodies.append((parent_type_nid or file_nid, dummy))
            return

        if t == "EArrowFunction":
            owner = parent_type_nid if parent_type_nid else file_nid
            for child in node.children:
                if child.type not in ("identifier", "->"):
                    function_bodies.append((owner, child))
            return

        if t == "EVars":
            name_node = node.child_by_field_name("name")
            if name_node:
                var_name = _read_text(name_node, source)
                line = node.start_point[0] + 1
                var_nid = pkg_id(stem, var_name)
                add_node(var_nid, var_name, line)
                add_edge(file_nid, var_nid, "contains", line)

                type_node = node.child_by_field_name("type")
                if type_node:
                    _emit_type_refs(var_nid, type_node, line)
            return

        if t == "ENew":
            for child in node.children:
                if child.type == "TypePath":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        type_name = _read_text(name_node, source)
                        line = child.start_point[0] + 1
                        tgt_nid = _make_id(type_name)
                        if tgt_nid not in seen_ids:
                            add_node(tgt_nid, type_name, line)
                        owner = parent_type_nid if parent_type_nid else file_nid
                        add_edge(owner, tgt_nid, "references", line, context="type")
            return

        if t == "conditional":
            for child in node.children:
                walk(child, parent_type_nid)
            return

        if t in ("conditional_elseif", "conditional_else"):
            for child in node.children:
                walk(child, parent_type_nid)
            return

        for child in node.children:
            walk(child, parent_type_nid)

    def _walk_imports(node) -> None:
        t = node.type
        if t == "import":
            module_node = node.child_by_field_name("module")
            line = node.start_point[0] + 1
            if module_node:
                module_name = _read_text(module_node, source)
                tgt_nid = _make_id(module_name)
                add_edge(file_nid, tgt_nid, "imports_from",
                         line, context="import")

            # Import aliasing: `import haxe.ds.StringMap as SM`
            alias = None
            for i, child in enumerate(node.children):
                if child.type == "as" and i + 1 < len(node.children):
                    next_c = node.children[i + 1]
                    if next_c.type == "identifier":
                        alias = _read_text(next_c, source)

            if alias:
                alias_nid = _make_id("alias", alias)
                if alias_nid not in seen_ids:
                    add_node(alias_nid, alias, line)
                add_edge(file_nid, alias_nid, "imports_from",
                         line, context="import_alias")

            for child in node.children:
                if child.type == "wildcard":
                    path_nodes = [c for c in node.children if c.type == "package_name"]
                    if path_nodes:
                        pkg_name = ".".join(_read_text(p, source) for p in path_nodes)
                        tgt_nid = _make_id(pkg_name)
                        add_edge(file_nid, tgt_nid, "imports_from",
                                 line, context="import")
            return

        if t == "using":
            type_node = node.child_by_field_name("type")
            if type_node:
                name_node = type_node
                if type_node.type == "TypePath":
                    name_node = type_node.child_by_field_name("name")
                if name_node is not None:
                    type_name = _read_text(name_node, source)
                    tgt_nid = _make_id(type_name)
                    add_edge(file_nid, tgt_nid, "imports_from",
                             node.start_point[0] + 1, context="using")
            return

        for child in node.children:
            _walk_imports(child)

    _walk_imports(root)
    walk(root)

    seen_call_pairs: set[tuple[str, str]] = set()
    raw_calls: list[dict] = []

    def walk_calls(node, caller_nid: str) -> None:
        if node.type in ("ClassMethod", "EFunction"):
            return

        if node.type == "EArrowFunction":
            return

        if node.type == "ECall":
            callee_node = node.child_by_field_name("callee")
            if callee_node:
                callee_name = None
                is_member_call = False
                if callee_node.type == "identifier":
                    callee_name = _read_text(callee_node, source)
                elif callee_node.type == "EField":
                    name_node = callee_node.child_by_field_name("name")
                    if name_node:
                        callee_name = _read_text(name_node, source)
                        is_member_call = True
                elif callee_node.type == "super":
                    callee_name = "super"
                    is_member_call = True

                if callee_name and callee_name not in _LANGUAGE_BUILTIN_GLOBALS:
                    tgt_nid = next(
                        (n["id"] for n in nodes
                         if n["label"] in (f"{callee_name}()", f".{callee_name}()")),
                        None
                    )
                    if tgt_nid and tgt_nid != caller_nid:
                        pair = (caller_nid, tgt_nid)
                        if pair not in seen_call_pairs:
                            seen_call_pairs.add(pair)
                            add_edge(caller_nid, tgt_nid, "calls",
                                     node.start_point[0] + 1,
                                     confidence="EXTRACTED", weight=1.0)
                    elif callee_name:
                        raw_calls.append({
                            "caller_nid": caller_nid,
                            "callee": callee_name,
                            "is_member_call": is_member_call,
                            "source_file": str_path,
                            "source_location": f"L{node.start_point[0] + 1}",
                        })
            return

        if node.type == "EMeta":
            line = node.start_point[0] + 1
            for child in node.children:
                if child.type == "MetaDataEntry":
                    name_node = next(
                        (c for c in child.children if c.type == "identifier"), None
                    )
                    if name_node:
                        meta_name = _read_text(name_node, source)
                        meta_nid = _make_id("meta", meta_name)
                        if meta_nid not in seen_ids:
                            add_node(meta_nid, f"@:{meta_name}", line)
                        add_edge(caller_nid, meta_nid, "configures", line, context="metadata")
            for child in node.children:
                walk_calls(child, caller_nid)
            return

        if node.type == "type_trace":
            for child in node.children:
                walk_calls(child, caller_nid)
            return

        if node.type == "macro":
            for child in node.children:
                walk_calls(child, caller_nid)
            return

        if node.type in ("ECast", "ECheckType"):
            line = node.start_point[0] + 1
            for child in node.children:
                if child.type == "ComplexType":
                    _emit_type_refs(caller_nid, child, line)
            for child in node.children:
                walk_calls(child, caller_nid)
            return

        if node.type == "switch_case":
            line = node.start_point[0] + 1
            for child in node.children:
                if child.type == "identifier":
                    label = _read_text(child, source)
                    if label and label[0].isupper():
                        ref_nid = _make_id(label)
                        if ref_nid not in seen_ids:
                            add_node(ref_nid, label, line)
                        add_edge(caller_nid, ref_nid, "references", line, context="case")
                    break
            for child in node.children:
                walk_calls(child, caller_nid)
            return

        if node.type in ("switch_default", "default"):
            for child in node.children:
                walk_calls(child, caller_nid)
            return

        if node.type == "EObjectDecl":
            line = node.start_point[0] + 1
            i = 1  # skip `{`
            while i < len(node.children) - 1:
                child = node.children[i]
                if child.type == "identifier":
                    field_name = _read_text(child, source)
                    if field_name:
                        field_nid = _make_id(caller_nid, field_name)
                        if field_nid not in seen_ids:
                            add_node(field_nid, field_name, line)
                        add_edge(caller_nid, field_nid, "contains", line, context="field")
                i += 1
                # skip : and value
                if i < len(node.children) and node.children[i].type == ":":
                    i += 1
                if i < len(node.children):
                    walk_calls(node.children[i], caller_nid)
                    i += 1
                # skip `,`
                if i < len(node.children) and node.children[i].type == ",":
                    i += 1
            return

        for child in node.children:
            walk_calls(child, caller_nid)

    for caller_nid, body_node in function_bodies:
        walk_calls(body_node, caller_nid)

    clean_edges = [e for e in edges if e["source"] in seen_ids and
                   (e["target"] in seen_ids or e["relation"] in ("imports_from",))]
    return {"nodes": nodes, "edges": clean_edges, "raw_calls": raw_calls}


def _resolve_cross_file_haxe_imports(
    per_file: list[dict],
    paths: list[Path],
) -> list[dict]:
    """Two-pass Haxe import resolution.

    Pass 1: build a global index {ClassName: [node_id, ...]} across all Haxe nodes.
    Pass 2: re-parse each Haxe file; for every `import a.b.C;`, resolve C against
    the index. Wildcard, stdlib, and package-only imports produce no edge.
    """
    try:
        import tree_sitter_haxe as tshx
        from tree_sitter import Language, Parser
    except ImportError:
        return []

    language = Language(tshx.language())
    parser = Parser(language)

    # Pass 1: class/type-name → node_id index (only internal, uppercase-starting names)
    name_to_ids: dict[str, list[str]] = {}
    for file_result in per_file:
        for node in file_result.get("nodes", []):
            label = node.get("label", "")
            nid = node.get("id", "")
            src = node.get("source_file", "")
            if not label or not nid or not src:
                continue
            if label.endswith(")") or label.endswith(".hx"):
                continue
            if not label[0].isalpha() or not label[0].isupper():
                continue
            name_to_ids.setdefault(label, []).append(nid)

    # Pass 2: resolve imports to real node IDs
    new_edges: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for path in paths:
        file_nid = _make_id(str(path))
        try:
            source = path.read_bytes()
            tree = parser.parse(source)
        except Exception:
            continue

        def walk(n) -> None:
            if n.type == "import":
                raw = _read_text(n, source).strip()
                body = raw[len("import"):].strip().rstrip(";").strip()
                if body.endswith(".*"):
                    return

                # Check for alias: `import haxe.ds.StringMap as SM`
                alias = None
                as_idx = body.find(" as ")
                if as_idx != -1:
                    alias = body[as_idx + 4:].strip()
                    body = body[:as_idx]

                parts = body.split(".")
                if not parts:
                    return
                last = parts[-1]
                if last and last[0].islower() and len(parts) >= 2:
                    last = parts[-2]
                at_line = n.start_point[0] + 1

                resolved_ids: set[str] = set()
                for tgt_nid in name_to_ids.get(last, []):
                    if tgt_nid == file_nid:
                        continue
                    key = (file_nid, tgt_nid)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    resolved_ids.add(tgt_nid)
                    new_edges.append({
                        "source": file_nid,
                        "target": tgt_nid,
                        "relation": "imports",
                        "confidence": "EXTRACTED",
                        "confidence_score": 1.0,
                        "source_file": str(path),
                        "source_location": f"L{at_line}",
                        "weight": 1.0,
                    })

                # Also resolve alias name against the index
                if alias:
                    for tgt_nid in name_to_ids.get(alias, []):
                        if tgt_nid == file_nid or tgt_nid in resolved_ids:
                            continue
                        key = (file_nid, tgt_nid)
                        if key in seen_pairs:
                            continue
                        seen_pairs.add(key)
                        new_edges.append({
                            "source": file_nid,
                            "target": tgt_nid,
                            "relation": "imports",
                            "confidence": "EXTRACTED",
                            "confidence_score": 1.0,
                            "source_file": str(path),
                            "source_location": f"L{at_line}",
                            "weight": 1.0,
                        })
            for child in n.children:
                walk(child)

        walk(tree.root_node)

    return new_edges
