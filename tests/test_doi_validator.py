import pytest

from backend.processing.doi_validator import InvalidDOI, normalize_doi, parse_dois, split_bulk


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.1038/nature14539", "10.1038/nature14539"),
        ("10.1038/NATURE14539", "10.1038/nature14539"),
        ("https://doi.org/10.1038/nature14539", "10.1038/nature14539"),
        ("http://dx.doi.org/10.1038/nature14539", "10.1038/nature14539"),
        ("doi.org/10.1038/nature14539", "10.1038/nature14539"),
        ("doi:10.1126/science.1157784", "10.1126/science.1157784"),
        ("DOI: 10.1126/science.1157784.", "10.1126/science.1157784"),
        ("  10.1145/3065386 ", "10.1145/3065386"),
        ("https://doi.org/10.1002%2F%28SICI%291097-4571", "10.1002/(sici)1097-4571"),
        ("<10.1016/j.cell.2020.01.001>", "10.1016/j.cell.2020.01.001"),
        ("urn:doi:10.1000/182", "10.1000/182"),
    ],
)
def test_normalize_valid(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not-a-doi", "11.1234/abc", "10.12/abc", "10.1234", "10.1234/", "10.1234/has space",
     "https://example.com/10.1234/x", "10." + "1" * 400 + "/x"],
)
def test_normalize_invalid(raw):
    with pytest.raises(InvalidDOI):
        normalize_doi(raw)


def test_parse_dois_dedupes_and_reports():
    parsed = parse_dois([
        "10.1038/nature14539",
        "https://doi.org/10.1038/NATURE14539",   # duplicate after normalization
        "garbage",
        "10.1126/science.1157784",
        "",
    ])
    assert [d for _, d in parsed.valid] == ["10.1038/nature14539", "10.1126/science.1157784"]
    assert parsed.duplicates == [{"input": "https://doi.org/10.1038/NATURE14539", "doi": "10.1038/nature14539"}]
    assert parsed.invalid[0]["input"] == "garbage"
    assert "10." in parsed.invalid[0]["reason"]


def test_split_bulk():
    assert split_bulk("a\n\n b \r\nc\n") == ["a", "b", "c"]
