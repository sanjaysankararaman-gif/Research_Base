import pytest

from backend.integrations.supabase_db import FacultyMember, match_faculty

MEMBERS = [
    FacultyMember("sree", "Sreehari Veeramachaneni", ("sreehari",), "veeramachaneni", frozenset()),
    FacultyMember("suga", "Suganya E", ("suganya",), "e", frozenset()),
    FacultyMember("gaya", "K.S. Gayathri", ("k", "s"), "gayathri", frozenset()),
    FacultyMember("sripr", "N. Sripriya", ("n",), "sripriya", frozenset()),
    FacultyMember("swath", "R. Swathika", ("r",), "swathika", frozenset()),
    FacultyMember("velu", "Durgadevi Velusamy", ("durgadevi",), "velusamy", frozenset()),
    FacultyMember("sarav", "Saravanan Alagarsamy", ("saravanan",), "alagarsamy", frozenset()),
]


def _hit(author: str) -> str | None:
    found = match_faculty([author], MEMBERS)
    return found[0][0] if found else None


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
    ("Gayathri", None),             # surname alone is not enough
    ("Sreehari", None),
    ("Rajesh Kumar", None),
])
def test_author_spellings(author, expected):
    assert _hit(author) == expected


def test_same_professor_linked_once():
    found = match_faculty(["D. Velusamy", "Durgadevi Velusamy"], MEMBERS)
    assert len(found) == 1
    assert found[0][2] == 1   # first position in the author list


def test_position_is_recorded():
    found = match_faculty(["Rajesh Kumar", "Gayathri K.S."], MEMBERS)
    assert found == [("gaya", "Gayathri K.S.", 2)]