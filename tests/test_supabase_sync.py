"""Mapping a record to the Supabase tables (no database needed)."""
from backend.integrations.database.supabase_sync import _issn_parts, publication_from_record
from backend.processing.data_merger import merge_sources
from backend.services import OK, SourceResult


def test_supabase_issn_split_never_duplicates():
    def rec(*values):
        return {"metrics": {"issn": {"values": [{"available": True, "value": v} for v in values]}}}

    assert _issn_parts(rec("2691-4581 (E-ISSN)", "2691-4581")) == (None, "2691-4581")
    assert _issn_parts(rec("0028-0836 (ISSN), 1476-4687 (E-ISSN)", "0028-0836; 1476-4687")) == ("0028-0836", "1476-4687")
    assert _issn_parts(rec("1234-5678 (ISSN)", "1234-5678; 8765-4321")) == ("1234-5678", "8765-4321")
    assert _issn_parts(rec("1581-5374; 1855-363X")) == ("1581-5374", "1855-363X")


def test_publication_carries_home_institution_and_faculty_ids():
    crossref = {"title": "T", "publication_type": "Journal", "journal": "J", "authors": ["Alan Turing"],
                "publication_date": "2020-05-17", "publication_year": 2020,
                "authorships": [{"name": "Alan Turing", "institutions": ["SSN College of Engineering, Chennai"],
                                 "ror": []}]}
    pub = publication_from_record(merge_sources("10.1/X", {"crossref": SourceResult("crossref", OK, data=crossref)}))
    assert pub.doi == "10.1/x" and pub.publication_date == "2020-05-17"
    assert pub.home_institution == "Shiv Nadar University, Chennai"
    assert pub.faculty == [{"faculty_id": "SNUC-F0002", "name": "Alan Turing", "author": "Alan Turing",
                            "position": 1, "evidence": "name and affiliation"}]
