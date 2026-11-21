"""The flight stream: rows, text rows, references, and finding data in a tree."""

from __future__ import annotations

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessendings import flight


def _text_row(row_id: str, text: str) -> str:
    """A ``T`` row: its length counts UTF-8 bytes, and no line break follows."""
    return f"{row_id}:T{len(text.encode('utf-8')):x},{text}"


def _lines(*rows: str) -> str:
    """Rows one per line, as the server sends all but ``T`` rows."""
    return "\n".join(rows) + "\n"


def test_rows_resolve_into_one_tree() -> None:
    tree = flight.parse(
        _lines(
            '1:"$Sreact.fragment"',
            '2:I[22471,["/_next/static/chunks/a.js"],"DrillPlayer"]',
            ':HL["/_next/static/chunks/a.css","style"]',
            "5:X",
            '3:["$","p",null,{"children":"hello"}]',
            '0:["$","$1","c",{"children":["$L3",["$","$L2",null,{"title":"$4"}]]}]',
            "4:null",
            "5:C",
        )
    )
    assert tree[0:3] == ["$", "$Sreact.fragment", "c"]
    paragraph, drill = tree[3]["children"]
    assert paragraph == ["$", "p", None, {"children": "hello"}]
    # A client module row is not data.
    assert drill == ["$", None, None, {"title": None}]


def test_a_text_row_is_measured_in_utf8_bytes() -> None:
    # An en dash and an accent are more bytes than characters; a parser
    # counting characters would cut the text and misread the next row.
    long = "Kling and Horwitz – 1851, le pion en é. " * 40
    stream = (
        _text_row("7", long) + '0:{"intro":"$7","goal":"$8"}\n' + _text_row("8", "win")
    )
    assert flight.parse(stream) == {"intro": long, "goal": "win"}


def test_reference_kinds_and_escapes() -> None:
    tree = flight.parse(
        _lines(
            '3:["$","li","k",{"children":"deep"}]',
            '4:[["a",1],["b",2]]',
            '6:{"value":7}',
            (
                '0:{"lazy":"$L3","promise":"$@6","map":"$Q4",'
                '"undefined":"$undefined","dollar":"$$5","date":"$D2026"}'
            ),
        )
    )
    assert tree["lazy"] == ["$", "li", "k", {"children": "deep"}]
    assert tree["promise"] == {"value": 7}
    assert tree["map"] == [["a", 1], ["b", 2]]
    assert tree["undefined"] is None
    assert tree["dollar"] == "$5"  # escaped, not a reference to row 5
    assert tree["date"] == "$D2026"  # not a reference, so left as it is


@pytest.mark.parametrize(
    ("stream", "message"),
    [
        ('0:{"me":"$0"}', "refers to itself"),
        ('0:{"gone":"$9"}', "no row 9"),
        ("0:{not json}", "not JSON"),
        ("zz:[]", "with id"),
        ("0:T20,short", "cut short"),
        ("no colon at all", "without an id"),
    ],
)
def test_a_stream_that_cannot_be_read_is_refused(stream: str, message: str) -> None:
    with pytest.raises(FetchError, match=message):
        flight.parse(stream)


def test_a_repeated_row_id_keeps_its_first_payload() -> None:
    assert flight.parse('5:"first"\n5:"second"\n0:"$5"') == "first"


def test_walk_is_depth_first_in_document_order() -> None:
    tree = {"a": [{"n": 1}, {"n": 2}], "b": {"n": 3}}
    found = [
        node["n"]
        for node in flight.walk(tree)
        if isinstance(node, dict) and "n" in node
    ]
    assert found == [1, 2, 3]


def test_props_with_finds_the_first_object_carrying_every_key() -> None:
    tree = [
        "$",
        "main",
        None,
        {"children": [{"a": 1}, {"a": 2, "b": 3}, {"a": 4, "b": 5}]},
    ]
    assert flight.props_with(tree, "a", "b") == {"a": 2, "b": 3}
    assert flight.props_with(tree, "c") is None


def test_is_element() -> None:
    assert flight.is_element(["$", "li", None, {}])
    assert not flight.is_element(["$", "li", None])
    assert not flight.is_element(["x", "li", None, {}])
