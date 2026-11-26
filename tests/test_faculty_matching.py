"""Home institution and faculty matching (processing/home_institution.py)."""
import csv
from pathlib import Path

import pytest

from backend.config import PROJECT_ROOT
from backend.processing.home_institution import (FacultyMember, home_institution, is_home_affiliation,
                                                 name_matches, parse_faculty)

MEMBERS = [
    FacultyMember("sree", "Sreehari Veeramachaneni", ("sreehari",), "veeramachaneni", frozenset()),
    FacultyMember("suga", "Suganya E", ("suganya",), "e", frozenset()),
    FacultyMember("gaya", "K.S. Gayathri", ("k", "s"), "gayathri", frozenset()),
    FacultyMember("sripr", "N. Sripriya", ("n",), "sripriya", frozenset()),
    FacultyMember("swath", "R. Swathika", ("r",), "swathika", frozenset()),
    FacultyMember("velu", "Durgadevi Velusamy", ("durgadevi",), "velusamy", frozenset()),
    FacultyMember("sarav", "Saravanan Alagarsamy", ("saravanan",), "alagarsamy", frozenset({"saravanan a s"})),
]
HOME = "Shiv Nadar University, Chennai"


def _hit(author: str) -> str | None:
    hits = [m.faculty_id for m in MEMBERS if name_matches(author, m)]
    return hits[0] if len(hits) == 1 else None


@pytest.mark.parametrize("author, expected", [
    ("Sreehari Veeramachaneni", "sree"),
    ("S. Veeramachaneni", "sree"),
    ("Veeramachaneni S.", "sree"),
    ("Veeramachaneni Sreehari", "sree"),
    ("Suganya E", "suga"),
    ("E. Suganya", "suga"),
    ("K. S. Gayathri", "gaya"),
    ("K.S. Gayathri", "gaya"),
    ("Gayathri K.S.", "gaya"),
    ("Gayathri KS", "gaya"),
    ("KS Gayathri", "gaya"),
    ("K Gayathri", None),           # only one initial: not enough to identify her
    ("N. Sripriya", "sripr"),
    ("Sripriya N.", "sripr"),
    ("R. Swathika", "swath"),
    ("Durgadevi Velusamy", "velu"),
    ("D. Velusamy", "velu"),
    ("Durgadevi V.", "velu"),
    ("Saravanan Alagarsamy", "sarav"),
    ("S. Alagarsamy", "sarav"),
    ("Saravanan A.", "sarav"),
    ("Saravanan A S", "sarav"),     # listed in name_variants
    ("Gayathri", None),             # surname alone is not enough
    ("Sreehari", None),
    ("Rajesh Kumar", None),
])
def test_author_spellings(author, expected):
    assert _hit(author) == expected


def _oa(name, institutions=(), ror=(), raw=()):
    return {"name": name, "institutions": list(institutions), "ror": list(ror), "raw_affiliations": list(raw)}


@pytest.mark.parametrize("names, rors, expected", [
    (["Shiv Nadar University, Chennai"], [], True),
    (["Department of Mathematics, Shiv Nadar University Chennai, Kalavakkam, India"], [], True),
    (["SSN School of Engineering, Shiv Nadar University, Chennai, India"], [], True),
    (["Sri Sivasubramaniya Nadar College of Engineering"], [], True),
    (["SSN College of Engineering, Chennai"], [], True),
    (["Some Lab"], ["https://ror.org/039rg7508"], True),            # SNU Chennai ROR
    (["Shiv Nadar University"], ["https://ror.org/05aqahr97"], False),   # the Delhi-NCR campus
    (["Shiv Nadar Institution of Eminence, Greater Noida"], [], False),
    (["Anna University, Chennai"], [], False),
    ([], [], False),
])
def test_home_affiliation(names, rors, expected):
    assert is_home_affiliation(names, rors) is expected


def test_home_institution_by_affiliation_and_faculty():
    authorships = {"openalex": [
        _oa("Rajesh Kumar", ["Anna University, Chennai"]),
        _oa("Durgadevi Velusamy", ["Shiv Nadar University, Chennai"], ["https://ror.org/039rg7508"]),
        _oa("Priya S", raw=["Department of CSE, Shiv Nadar University Chennai, India"]),
    ]}
    authors = ["Rajesh Kumar", "D. Velusamy", "Priya S"]
    h = home_institution(authorships, authors, MEMBERS)
    assert h["home_institution"] == HOME
    assert h["home_authors"] == ["Durgadevi Velusamy", "Priya S"]
    assert h["faculty"] == [{"faculty_id": "velu", "name": "Durgadevi Velusamy", "author": "D. Velusamy",
                             "position": 2, "evidence": "name and affiliation"}]
    assert [a["is_home"] for a in h["affiliations"]] == [False, True, True]
    # Priya has no normalized institution, so the affiliation text is shown.
    assert h["affiliations"][2]["institutions"] == ["Department of CSE, Shiv Nadar University Chennai, India"]
    assert h["affiliation_source"] == "openalex"


def test_same_name_elsewhere_is_not_linked():
    """'E. Suganya' is several different researchers: one at another institution is not ours."""
    authorships = {"openalex": [_oa("E. Suganya", ["Indian Institute of Technology Guwahati"])]}
    h = home_institution(authorships, ["E. Suganya"], MEMBERS)
    assert h["faculty"] == [] and h["home_institution"] is None


def test_name_only_when_no_source_lists_affiliations():
    h = home_institution({}, ["Rajesh Kumar", "Gayathri K.S."], MEMBERS)
    assert h["faculty"] == [{"faculty_id": "gaya", "name": "K.S. Gayathri", "author": "Gayathri K.S.",
                             "position": 2, "evidence": "name only (no affiliation in the sources)"}]
    assert h["home_institution"] == HOME and h["affiliations"] == []


def test_teacher_linked_once_at_first_position():
    h = home_institution({}, ["D. Velusamy", "Durgadevi Velusamy"], MEMBERS)
    assert [(f["faculty_id"], f["position"]) for f in h["faculty"]] == [("velu", 1)]


def test_one_source_at_home_wins_over_missing_affiliation_in_another():
    authorships = {"openalex": [_oa("N. Sripriya")],
                   "crossref": [_oa("Sripriya N", ["SSN College of Engineering, Chennai"])]}
    h = home_institution(authorships, ["Sripriya N"], MEMBERS)
    assert [f["faculty_id"] for f in h["faculty"]] == ["sripr"]
    assert h["faculty"][0]["evidence"] == "name and affiliation"


def test_no_home_author():
    authorships = {"crossref": [_oa("Yann LeCun", ["New York University"])]}
    h = home_institution(authorships, ["Yann LeCun"], MEMBERS)
    assert h["home_institution"] is None and h["faculty"] == [] and h["home_authors"] == []
    assert h["affiliations"] == [{"author": "Yann LeCun", "institutions": ["New York University"], "is_home": False}]


# --------------------------------------------------------------------------- faculty.csv

def _rows(*lines):
    return list(csv.DictReader(["faculty_id,full_name,given_names,family_name,name_variants", *lines]))


def test_faculty_file_rules():
    members = parse_faculty(_rows("SNUC-F0001,K.S. Gayathri,K S,Gayathri,Gayathri Kanchi; G. K. S."))
    assert members[0].given == ("k", "s") and members[0].family == "gayathri"
    assert members[0].variants == {"gayathri kanchi", "g k s"}
    for bad in (["SNUC-F0001,A B,A,B,", "SNUC-F0001,C D,C,D,"],    # same ID twice
                ["SNUC-F0001,A B,A,B,", "SNUC-F0002,A B,A,B,"],    # same name twice
                [",A B,A,B,"],                                   # no ID
                ["SNUC-F0001,A B,A,Van Der B,"]):                # surname must be one word
        with pytest.raises(ValueError):
            parse_faculty(_rows(*bad))


def test_real_faculty_file_is_valid():
    path = Path(PROJECT_ROOT) / "reference" / "faculty.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        members = parse_faculty(list(csv.DictReader(fh)))
    assert members and len({m.faculty_id for m in members}) == len(members)
    assert all(m.faculty_id.startswith("SNUC-F") for m in members)


def test_home_author_not_on_faculty_list_is_explained():
    from backend.processing.data_merger import flatten, merge_sources
    from backend.services import OK, SourceResult

    oa = {"title": "T", "authors": ["K. Deepa"],
          "authorships": [_oa("K. Deepa", ["Shiv Nadar University, Chennai"])]}
    p = flatten(merge_sources("10.1/x", {"openalex": SourceResult("openalex", OK, data=oa)}))
    assert p["fields"]["home_institution"] == HOME and p["fields"]["home_faculty"] == []
    assert p["fields"]["home_faculty_text"] == "None on the faculty list (home authors: K. Deepa)"
    assert "not on the faculty list" in p["provenance"]["home_faculty"]["reason"]

