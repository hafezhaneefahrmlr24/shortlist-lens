"""Tests for the search stage and bulk import. No Ollama needed."""
import json
import shutil
import sys
from pathlib import Path

import pytest

import core
import pool_search

SAMPLES = Path(__file__).resolve().parent.parent / "sample_resumes"
HR_REQS = {"roleTitle": "HRBP", "mustHave": ["HRBP experience", "employee relations and performance management"],
           "niceToHave": ["Workday or another HRIS"], "minExperienceYrs": 4}


@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(core, "DB_FILE", tmp_path / "talent_pool.json")
    monkeypatch.setattr(core, "RESUME_DIR", tmp_path / "resumes")
    core._INDEX_CACHE.clear()
    core._TEXT_CACHE.clear()


def rec(id_, text, exp=5, **kw):
    return {"id": id_, "candidateName": id_, "resumeText": text + " " * 60, "totalExperienceYrs": exp, "skills": [], **kw}


def test_query_groups_expand_synonyms_and_drop_filler():
    groups = pool_search.query_groups("4+ years of experience as an HRBP, with strong employee relations")
    flat = [t for g in groups for t in g]
    assert "people partner" in flat and "grievance" in flat
    assert "4+" not in flat and "strong" not in flat and "years" not in flat


def test_rank_pool_finds_synonym_wording_and_explains_why():
    pool = [rec("eng", "Backend engineer building APIs in Go on AWS."),
            rec("hr", "People Partner for a plant. Handles grievances and runs the appraisal cycle. Uses SuccessFactors."),
            rec("sales", "Key account manager for enterprise clients.")]
    ranked = core.rank_pool(pool, HR_REQS)
    assert ranked[0]["record"]["id"] == "hr"
    assert ranked[0]["mustCovered"] == 2 and ranked[0]["niceCovered"] == 1
    assert [r["searchRank"] for r in ranked] == [1, 2, 3]


def test_rank_pool_ranks_low_experience_lower_but_keeps_it():
    text = "HR Business Partner handling employee relations and performance management on Workday."
    pool = [rec("junior", text, exp=1), rec("senior", text, exp=6)]
    ranked = core.rank_pool(pool, HR_REQS)
    assert [r["record"]["id"] for r in ranked] == ["senior", "junior"]
    assert ranked[1]["belowExperience"] and len(ranked) == 2


def test_match_candidates_only_sends_the_top_k_to_the_model(monkeypatch):
    calls = []

    def fake(model, prompt, fmt=None):
        calls.append(prompt)
        return json.dumps({"assessments": [{"requirement": "x", "status": "met", "evidence": "e"}] * 3, "summary": "ok"})
    monkeypatch.setattr(core, "_generate", fake)
    pool = [rec("eng", "Backend engineer building APIs."),
            rec("hr", "HR Business Partner: employee relations, performance management, Workday.")]
    results = core.match_candidates(HR_REQS, pool, "m", k=1)
    assert len(calls) == 1 and "HR Business Partner" in calls[0]
    assert results[0]["candidateName"] == "hr" and results[0]["searchRank"] == 1


def test_search_question_uses_synonyms():
    pool = [rec("a", "Analyst at an NBFC writing PostgreSQL queries.", fitScore=60),
            rec("b", "Video producer for YouTube.", fitScore=90)]
    shortlist, matched = core.search(pool, "BFSI candidates with SQL")
    assert matched and [r["id"] for r in shortlist] == ["a"]


def test_bulk_import_adds_then_skips_on_rerun(tmp_path, monkeypatch, capsys):
    import bulk_import
    folder = tmp_path / "in"
    folder.mkdir()
    for p in list(SAMPLES.glob("*"))[:3]:
        shutil.copy(p, folder / p.name)
    names = iter(["Asha One", "Bina Two", "Chetan Three"])

    def fake(model, prompt, fmt=None):
        return json.dumps({"candidateName": next(names), "totalExperienceYrs": 3, "category": "Non-Tech",
                           "currentRole": "x", "skills": ["a"], "education": "y", "fitScore": 50, "summary": "s"})
    monkeypatch.setattr(core, "_generate", fake)
    monkeypatch.setattr(core, "list_models", lambda: ["qwen3:4b"])
    monkeypatch.setattr(sys, "argv", ["bulk_import.py", str(folder)])
    assert bulk_import.main() == 0
    assert len(core.load_pool()) == 3 and all(r.get("resumeText") for r in core.load_pool())
    assert bulk_import.main() == 0
    assert len(core.load_pool()) == 3
    assert "3 already in the pool" in capsys.readouterr().out
