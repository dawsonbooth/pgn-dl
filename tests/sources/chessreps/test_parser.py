"""Devalue hydration and payload extraction, including the real trial payload."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessreps import parser

from .conftest import (
    course_page,
    data_json,
    flatten,
    signed_in_layout,
    signed_out_layout,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "chessreps"


def test_hydrate_follows_indices() -> None:
    values = flatten({"a": [1, "x"], "b": {"c": True}})
    assert parser.hydrate(0, values) == {"a": [1, "x"], "b": {"c": True}}


def test_hydrate_resolves_scalar_sentinels() -> None:
    # Sentinels appear as negative "indices" in reference position.
    values = [{"und": -1, "hole": -2, "nan": -3, "inf": -4, "ninf": -5, "nzero": -6}]
    got = parser.hydrate(0, values)
    assert got["und"] is None and got["hole"] is None
    assert math.isnan(got["nan"])
    assert got["inf"] == math.inf and got["ninf"] == -math.inf
    assert got["nzero"] == 0.0


def test_hydrate_refuses_a_tagged_array() -> None:
    values = [{"when": 1}, ["Date", 2], "2026-01-01"]
    with pytest.raises(FetchError) as exc:
        parser.hydrate(0, values)
    assert "Date" in str(exc.value)


def test_hydrate_refuses_a_cycle() -> None:
    values = [{"self": 0}]
    with pytest.raises(FetchError):
        parser.hydrate(0, values)


def test_hydrate_refuses_an_out_of_range_index() -> None:
    with pytest.raises(FetchError):
        parser.hydrate(0, [{"a": 99}])


def test_hydrate_shares_repeated_references_instead_of_exploding() -> None:
    # devalue deduplicates repeated values into shared references, so a
    # diamond chain is the format's normal case. Forty levels is 2**40 naive
    # resolutions; completing at all proves resolution is linear.
    values: list = [[i + 1, i + 1] for i in range(40)]
    values.append("leaf")
    got = parser.hydrate(0, values)
    assert got[0] is got[1], "a shared reference must hydrate to one object"
    node = got
    for _ in range(40):
        node = node[0]
    assert node == "leaf"


def test_hydrate_turns_a_too_deep_payload_into_a_fetch_error() -> None:
    # The flat wire format sidesteps json.loads's depth guard, so this must
    # not escape as a raw RecursionError traceback.
    values: list = [[i + 1] for i in range(5000)]
    values.append("leaf")
    with pytest.raises(FetchError) as exc:
        parser.hydrate(0, values)
    assert "deep" in str(exc.value)


def test_parse_payload_reads_a_signed_in_course() -> None:
    page = parser.parse_payload(data_json(signed_in_layout(), course_page()))
    assert page.signed_in is True
    course = page.course
    assert course.id == "abc1234"
    assert course.name == "Test Repertoire"
    assert course.player_side == "w"
    assert course.trial is False
    assert course.lines == ("1. e4 c5 2. c3 Nc6", "1. e4 c5 2. c3 d5")
    assert course.line_names == {"1. e4 c5 2. c3 Nc6": "Old Main Line"}


def test_parse_payload_reads_a_signed_out_page() -> None:
    text = data_json(signed_out_layout(), course_page(isUserCourseTrial=True))
    page = parser.parse_payload(text)
    assert page.signed_in is False
    assert page.course.trial is True


def test_a_missing_layout_node_reads_as_signed_out() -> None:
    page = parser.parse_payload(data_json(None, course_page()))
    assert page.signed_in is False


def test_nodes_are_found_by_content_not_position() -> None:
    # Same page data, but with extra null and unrelated nodes ahead of it.
    body = json.loads(data_json(signed_in_layout(), course_page()))
    body["nodes"] = [None, {"type": "data", "data": flatten({"unrelated": 1})}] + body[
        "nodes"
    ]
    page = parser.parse_payload(json.dumps(body))
    assert page.course.id == "abc1234"


def test_a_payload_without_course_data_is_refused() -> None:
    with pytest.raises(FetchError) as exc:
        parser.parse_payload(json.dumps({"type": "data", "nodes": [None]}))
    assert "userCourseData" in str(exc.value)


def test_empty_lines_are_refused() -> None:
    with pytest.raises(FetchError):
        parser.parse_payload(data_json(signed_in_layout(), course_page(lines=[])))


def test_an_unknown_player_side_is_refused() -> None:
    with pytest.raises(FetchError):
        parser.parse_payload(
            data_json(signed_in_layout(), course_page(playerSide="white"))
        )


def test_unread_fields_may_carry_encodings_the_parser_does_not_know() -> None:
    # A tagged type (a Date, say) in a course field this source never reads
    # must not fail the run; hydration is scoped to the consumed fields.
    page = course_page()
    page["userCourseData"]["data"]["computedAt"] = ["Date", "2026-08-09"]
    parsed = parser.parse_payload(data_json(signed_in_layout(), page))
    assert parsed.course.id == "abc1234"


def test_session_presence_is_read_without_resolving_the_session() -> None:
    # Only presence matters, so an encoding inside the session (or its user
    # object) that the hydrator would refuse must not fail the run.
    layout = {"session": {"user": ["Date", "2026-08-09"], "access_token": "D"}}
    parsed = parser.parse_payload(data_json(layout, course_page()))
    assert parsed.signed_in is True


def test_non_string_map_entries_are_dropped_not_fatal() -> None:
    page = parser.parse_payload(
        data_json(
            signed_in_layout(),
            course_page(
                descriptions={"fen1": "keep", "fen2": None},
                lineNames={"1. e4 c5 2. c3 Nc6": 7},
            ),
        )
    )
    assert page.course.descriptions == {"fen1": "keep"}
    assert page.course.line_names == {}


def test_the_real_trial_payload_parses() -> None:
    text = (FIXTURES / "opening_trial_data.json").read_text(encoding="utf-8")
    page = parser.parse_payload(text)
    assert page.signed_in is False
    course = page.course
    assert course.id == "ilg8hms"
    assert course.name == "Alapin Sicilian Repertoire"
    assert course.player_side == "w"
    assert course.trial is True
    assert len(course.lines) == 5  # the anonymous subset of 33
    assert all(line.startswith("1. e4 c5 2. c3") for line in course.lines)
    assert len(course.descriptions) >= 15
    assert len(course.evals) >= 30  # 36 in the capture
    # The two FEN vocabularies: descriptions carry move counters, evals do not.
    assert all(len(fen.split()) == 6 for fen in course.descriptions)
    assert all(len(fen.split()) == 4 for fen in course.evals)
