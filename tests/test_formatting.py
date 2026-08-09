"""Native Messages attributed-text extraction tests."""

import pytest


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("plain text reply", {"text": "plain text reply", "ranges": []}),
        (
            "😀 **bold _and italic_** ~~gone~~",
            {
                "text": "😀 bold and italic gone",
                "ranges": [
                    {"start": 3, "length": 15, "styles": ["bold"]},
                    {"start": 8, "length": 10, "styles": ["italic"]},
                    {"start": 19, "length": 4, "styles": ["strikethrough"]},
                ],
            },
        ),
        (
            "<u>x</u> and <ins>y</ins>",
            {
                "text": "x and y",
                "ranges": [
                    {"start": 0, "length": 1, "styles": ["underline"]},
                    {"start": 6, "length": 1, "styles": ["underline"]},
                ],
            },
        ),
        (
            "`*literal*` def __init__(self):",
            {"text": "`*literal*` def __init__(self):", "ranges": []},
        ),
        (
            "snake_case and obj.__class__ and __dict__['key']",
            {"text": "snake_case and obj.__class__ and __dict__['key']", "ranges": []},
        ),
        (
            "(__warning__)",
            {
                "text": "(warning)",
                "ranges": [{"start": 1, "length": 7, "styles": ["bold"]}],
            },
        ),
    ],
)
def test_extract_markdown_format_runs(source, expected):
    from hermes_imsg_cli.formatting import extract_markdown_format_runs

    assert extract_markdown_format_runs(source) == expected


def test_utf16_offsets_count_emoji_as_two_code_units():
    from hermes_imsg_cli.formatting import extract_markdown_format_runs

    assert extract_markdown_format_runs("😀 **x**") == {
        "text": "😀 x",
        "ranges": [{"start": 3, "length": 1, "styles": ["bold"]}],
    }


def test_unmatched_markers_remain_literal():
    from hermes_imsg_cli.formatting import extract_markdown_format_runs

    assert extract_markdown_format_runs("price * quantity and **  **") == {
        "text": "price * quantity and **  **",
        "ranges": [],
    }
