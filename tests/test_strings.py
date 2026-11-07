"""Checks on strings.json that hassfest would otherwise only catch in CI."""

import json
import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "nsw_trains"


def _strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


@pytest.mark.unit
@pytest.mark.parametrize("path", ["strings.json", "translations/en.json"])
def test_no_urls_in_strings(path):
    """hassfest rejects URLs in strings; pass them as description placeholders instead."""
    strings = list(_strings(json.loads((COMPONENT / path).read_text())))
    assert [s for s in strings if re.search(r"https?://", s)] == []


@pytest.mark.unit
def test_translation_matches_strings():
    assert json.loads((COMPONENT / "strings.json").read_text()) == json.loads((COMPONENT / "translations/en.json").read_text())
