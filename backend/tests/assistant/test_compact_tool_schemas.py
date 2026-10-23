from dawam.modules.assistant.internal.tools import TOOLS


def _keywords(node):
    """Every ``title`` schema keyword in the schema (a field named ``title`` is not one)."""
    found = []
    if isinstance(node, dict):
        if isinstance(node.get("title"), str):
            found.append(node["title"])
        for key in ("properties", "$defs"):
            for sub in node.get(key, {}).values():
                found += _keywords(sub)
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []):
                found += _keywords(sub)
        for key in ("items", "additionalProperties"):
            found += _keywords(node.get(key))
    return found


def test_compact_schemas_drop_generated_titles_but_keep_a_title_field():
    spec = next(t for t in TOOLS if t.name == "propose_changes").spec
    assert "title" in spec.parameters["properties"]
    assert _keywords(spec.parameters) == []
    assert "title" in spec.parameters["required"]


def test_other_tools_keep_their_schema_unchanged():
    assert any(_keywords(t.spec.parameters) for t in TOOLS if not t.compact)
