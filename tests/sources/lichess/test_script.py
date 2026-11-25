"""Reading literals out of minified JavaScript, and skipping what is not needed."""

from __future__ import annotations

from typing import Any

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.lichess.script import (
    Arrow,
    Call,
    Mapped,
    Name,
    Obj,
    Reader,
    ScriptError,
    Skipped,
    Spread,
)


def read(text: str, keep: frozenset[str] | None = None) -> Any:
    """The value ``text`` is, read whole."""
    reader = Reader(text)
    value = reader.value(keep)
    assert reader.pos == len(text), f"stopped at {reader.pos}"
    return value


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ('"Grab all the stars!"', "Grab all the stars!"),
        ("'single'", "single"),
        ('""', ""),
        (r'"say \"hi\""', 'say "hi"'),
        (r"'don\'t'", "don't"),
        (r'"back\\slash"', "back\\slash"),
        (r'"two\nlines\ttab"', "two\nlines\ttab"),
        (r'"café"', "café"),
        (r'"\x41\x42"', "AB"),
        (r'"\u{1F600}"', "\U0001f600"),
        (r'"nul\0"', "nul\0"),
        (r'"\q"', "q"),  # an escape that means nothing is the letter itself
    ],
)
def test_strings(literal: str, expected: str) -> None:
    assert read(literal) == expected


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("42", 42),
        ("-3", -3),
        ("0", 0),
        ("1.5", 1.5),
        ("2e3", 2000.0),
        ("1e-3", 0.001),
        ("!0", True),
        ("!1", False),
    ],
)
def test_numbers_and_booleans(literal: str, expected: object) -> None:
    value = read(literal)
    assert value == expected
    assert type(value) is type(expected)


def test_arrays() -> None:
    assert read("[]") == []
    assert read('[1,"a",!0,[2,[]]]') == [1, "a", True, [2, []]]


def test_names_and_member_chains() -> None:
    assert read("he") == Name(("he",))
    assert read("$e_1") == Name(("$e_1",))
    assert read("i18n.learn.theBishop") == Name(("i18n", "learn", "theBishop"))


def test_calls() -> None:
    assert read('n("f3d5")') == Call(Name(("n",)), ("f3d5",))
    assert read('n("c5d6","red")') == Call(Name(("n",)), ("c5d6", "red"))
    assert read("Ie()") == Call(Name(("Ie",)), ())
    assert read("a.b(x,1)") == Call(Name(("a", "b")), (Name(("x",)), 1))


def test_arrow_functions() -> None:
    assert read("e=>e") == Arrow(("e",), Name(("e",)))
    assert read("(e,a)=>t(e,a)") == Arrow(
        ("e", "a"), Call(Name(("t",)), (Name(("e",)), Name(("a",))))
    )
    assert read("()=>({nbMoves:2})") == Arrow((), Obj((("nbMoves", 2),)))


def test_a_parenthesized_value_is_the_value() -> None:
    assert read("(1)") == 1
    assert read("(e)") == Name(("e",))


def test_objects_keep_their_entries_and_spreads_in_order() -> None:
    obj = read('{key:"rook","quoted key":1,\'single\':2,...Ne,...Ie(),...e,k:!0}')
    assert obj == Obj(
        (
            ("key", "rook"),
            ("quoted key", 1),
            ("single", 2),
            Spread(Name(("Ne",))),
            Spread(Call(Name(("Ie",)), ())),
            Spread(Name(("e",))),
            ("k", True),
        )
    )
    assert obj.spreads == [
        Spread(Name(("Ne",))),
        Spread(Call(Name(("Ie",)), ())),
        Spread(Name(("e",))),
    ]
    assert read("{}") == Obj(())


def test_an_object_s_fields_let_a_later_key_win_as_in_javascript() -> None:
    obj = read('{goal:"first",...e,goal:"second",fen:"8/8"}')
    assert obj.fields() == {"goal": "second", "fen": "8/8"}


def test_an_array_mapped_through_a_function() -> None:
    assert read("[{a:1}].map(t)") == Mapped([Obj((("a", 1),))], Name(("t",)))
    mapped = read("[{a:1}].map((e,a)=>t({...Ne,...e,b:!0},a))")
    assert mapped == Mapped(
        [Obj((("a", 1),))],
        Arrow(
            ("e", "a"),
            Call(
                Name(("t",)),
                (
                    Obj((Spread(Name(("Ne",))), Spread(Name(("e",))), ("b", True))),
                    Name(("a",)),
                ),
            ),
        ),
    )


# Skipping what the caller does not need.

KEEP = frozenset({"goal", "fen", "levels"})


def test_a_value_under_a_key_not_kept_is_skipped_by_its_brackets() -> None:
    obj = read('{goal:"g",success:u(i,x(2),{a:[1,2]}),fen:"8/8"}', KEEP)
    assert obj == Obj(
        (
            ("goal", "g"),
            ("success", Skipped("u(i,x(2),{a:[1,2]})")),
            ("fen", "8/8"),
        )
    )


def test_a_skipped_value_may_be_the_last_entry() -> None:
    obj = read('{goal:"g",failure:e=>!e.chess.instance.isCheck()}', KEEP)
    assert obj.fields()["failure"] == Skipped("e=>!e.chess.instance.isCheck()")


def test_brackets_and_commas_inside_strings_do_not_end_a_skip() -> None:
    obj = read('{css:"a]})(,b",img:o+\'x",}\'+"\\"]",tpl:`${f(1,2)}}{`,goal:"g"}', KEEP)
    assert obj.fields() == {
        "css": Skipped('"a]})(,b"'),
        "img": Skipped('o+\'x",}\'+"\\"]"'),
        "tpl": Skipped("`${f(1,2)}}{`"),
        "goal": "g",
    }


def test_a_template_literal_nested_in_a_substitution_is_skipped_whole() -> None:
    obj = read('{t:`a${b(`c${d}`)}e`,goal:"g"}', KEEP)
    assert obj.fields() == {"t": Skipped("`a${b(`c${d}`)}e`"), "goal": "g"}


def test_keys_are_kept_or_skipped_at_every_depth() -> None:
    obj = read('{levels:[{goal:"g",success:i},{fen:"8",nbMoves:2}],image:o+"x"}', KEEP)
    assert obj.fields() == {
        "levels": [
            Obj((("goal", "g"), ("success", Skipped("i")))),
            Obj((("fen", "8"), ("nbMoves", Skipped("2")))),
        ],
        "image": Skipped('o+"x"'),
    }


def test_without_keep_every_value_is_read() -> None:
    assert read("{a:{b:x(1)}}").fields() == {
        "a": Obj((("b", Call(Name(("x",)), (1,))),))
    }


def test_a_reader_stops_after_the_value_it_was_asked_for() -> None:
    reader = Reader('he={key:"bishop"},q=he;', 3)
    assert reader.value() == Obj((("key", "bishop"),))
    assert reader.text[reader.pos :] == ",q=he;"


# What it cannot read fails, naming where.


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "unexpected end of script at offset 0"),
        ("[1,", "unexpected end of script at offset 3"),
        ('"never closed', "unterminated string at offset 0"),
        ("@", "unreadable value at offset 0"),
        ("{1:2}", "unreadable key at offset 1"),
        ("{a:1;b:2}", r"expected '\}' at offset 4"),
        ("[1 2]", r"expected '\]' at offset 2"),
    ],
)
def test_what_cannot_be_read_fails_naming_the_offset(text: str, message: str) -> None:
    with pytest.raises(ScriptError, match=message):
        Reader(text).value()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{a:(]}", "unbalanced brackets"),
        ("{a:f(1,[2}", "unbalanced brackets"),
        ("{a:f(1", "unexpected end of script"),
        ("{a:`${b", "unclosed template substitution"),
        ("{a:`${`b}", "unterminated string"),  # a template nested inside
        ("{a:'open}", "unterminated string"),
    ],
)
def test_a_value_that_cannot_be_skipped_fails(text: str, message: str) -> None:
    with pytest.raises(ScriptError, match=message):
        Reader(text).value(frozenset())


def test_a_script_error_is_a_fetch_error_the_runner_reports() -> None:
    assert issubclass(ScriptError, FetchError)
