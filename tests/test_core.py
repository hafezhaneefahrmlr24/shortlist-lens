"""Tests for core logic. They don't need Ollama: model calls are replaced with stubs."""
import json
from pathlib import Path

import pytest

import core

SAMPLES = Path(__file__).resolve().parent.parent / "sample_resumes"


@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(core, "DB_FILE", tmp_path / "talent_pool.json")
    monkeypatch.setattr(core, "RESUME_DIR", tmp_path / "resumes")


def fake_model(name="Ananya Rao", exp=6, skills=("SQL", "Reporting")):
    def _generate(model, prompt, fmt=None):
        return json.dumps({"candidateName": name, "totalExperienceYrs": exp, "category": "Non-Tech",
                           "currentRole": "Senior Correspondent", "skills": list(skills),
                           "education": "BA Journalism", "fitScore": 81, "summary": "Business journalist."})
    return _generate


def test_normalize_maps_alias_keys_and_coerces_types():
    rec = core.normalize_candidate({"name": " Diya Nair ", "experience": "4.5 years", "type": "technical",
                                    "skills": "SQL, Python, sql", "ht_score": 130})
    assert rec["candidateName"] == "Diya Nair"
    assert rec["totalExperienceYrs"] == 4.5
    assert rec["category"] == "Tech"
    assert rec["skills"] == ["SQL", "Python"]
    assert rec["fitScore"] == 100


def test_normalize_rejects_missing_or_placeholder_names():
    for bad in ({}, {"candidateName": ""}, {"candidateName": "Unknown"}, ["not", "a", "dict"]):
        with pytest.raises(ValueError):
            core.normalize_candidate(bad)


def test_load_pool_skips_empty_records():
    core.save_pool([{"candidateName": "A"}, {}, {"candidateName": ""}])
    assert [r["candidateName"] for r in core.load_pool()] == ["A"]


def test_extract_text_from_sample_pdf_and_docx():
    pdf = next(SAMPLES.glob("*.pdf")); docx_file = next(SAMPLES.glob("*.docx"))
    assert len(core.extract_text(pdf.name, pdf.read_bytes())) > 100
    assert len(core.extract_text(docx_file.name, docx_file.read_bytes())) > 100


def test_add_resume_stores_file_and_skips_exact_duplicates(monkeypatch):
    monkeypatch.setattr(core, "_generate", fake_model())
    pool, sample = [], SAMPLES / "ananya_rao.pdf"
    status, _, record = core.add_resume(sample.name, sample.read_bytes(), "test-model", pool)
    assert status == "added" and (core.RESUME_DIR / record["storedFilename"]).exists()
    status, _, _ = core.add_resume(sample.name, sample.read_bytes(), "test-model", pool)
    assert status == "duplicate" and len(pool) == 1


def test_add_resume_skips_same_person_from_a_different_file(monkeypatch):
    monkeypatch.setattr(core, "_generate", fake_model())
    pool = []
    core.add_resume("ananya_rao.pdf", (SAMPLES / "ananya_rao.pdf").read_bytes(), "m", pool)
    status, _, _ = core.add_resume("ananya_v2.docx", (SAMPLES / "neha_pillai.docx").read_bytes(), "m", pool)
    assert status == "duplicate"


def test_add_resume_reports_unusable_model_output(monkeypatch):
    monkeypatch.setattr(core, "_generate", lambda *a, **k: "Sorry, I can't help with that.")
    status, message, _ = core.add_resume("ananya_rao.pdf", (SAMPLES / "ananya_rao.pdf").read_bytes(), "m", [])
    assert status == "error" and "couldn't be used" in message


def test_search_ranks_skill_matches_and_applies_experience_floor():
    pool = [
        {"candidateName": "A", "skills": ["SQL", "Python"], "summary": "BFSI analyst", "totalExperienceYrs": 4, "fitScore": 70},
        {"candidateName": "B", "skills": ["Premiere Pro"], "summary": "Video producer", "totalExperienceYrs": 3, "fitScore": 90},
        {"candidateName": "C", "skills": ["SQL"], "summary": "Content strategist", "totalExperienceYrs": 6, "fitScore": 60},
    ]
    shortlist, matched = core.search(pool, "BFSI candidates with SQL")
    assert matched and [r["candidateName"] for r in shortlist] == ["A", "C"]
    shortlist, _ = core.search(pool, "SQL with 5+ years")
    assert [r["candidateName"] for r in shortlist] == ["C"]


def test_search_falls_back_to_top_scores_when_nothing_matches():
    pool = [{"candidateName": "A", "skills": [], "fitScore": 50}, {"candidateName": "B", "skills": [], "fitScore": 90}]
    shortlist, matched = core.search(pool, "astronaut")
    assert not matched and shortlist[0]["candidateName"] == "B"


def test_pick_default_model_prefers_known_models():
    assert core.pick_default_model(["mistral:latest", "llama3:latest"]) == "llama3:latest"
    assert core.pick_default_model(["qwen3:4b", "llama3:latest"]) == "qwen3:4b"
    assert core.pick_default_model([]) is None


def test_fit_score_is_the_sum_of_clamped_sub_scores():
    rec = core.normalize_candidate({"candidateName": "A", "clarityScore": 15, "experienceScore": 40,
                                    "skillsScore": "22", "impactScore": -3})
    assert (rec["clarityScore"], rec["experienceScore"], rec["skillsScore"], rec["impactScore"]) == (15, 30, 22, 0)
    assert rec["fitScore"] == 67


def test_strip_think_handles_a_missing_opening_tag():
    assert core._strip_think("long reasoning... </think>\nFinal answer") == "Final answer"
    assert core._strip_think("<think>a</think>Answer") == "Answer"
    assert core._strip_think("Plain answer") == "Plain answer"


# ---------- Job matching ----------

REQS = {"roleTitle": "Data Analyst", "mustHave": ["SQL", "Python", "Dashboards"],
        "niceToHave": ["Credit risk"], "minExperienceYrs": 3}


def test_score_match_is_computed_from_requirement_checks():
    checks = [{"status": "met"}, {"status": "partial"}, {"status": "missing"}, {"status": "met"}]
    r = core.score_match(REQS, checks, experience_yrs=4)
    # must-haves (1 + 0.5 + 0) / 3 * 65 = 32.5, nice 20, experience 15
    assert r["matchScore"] == 68
    assert (r["mustMet"], r["mustTotal"], r["gaps"], r["experienceOk"]) == (1, 3, ["Dashboards"], True)


def test_score_match_treats_missing_or_bad_checks_as_missing_and_penalises_low_experience():
    r = core.score_match(REQS, [{"status": "met"}, {"status": "nonsense"}], experience_yrs=2.5)
    assert r["gaps"] == ["Python", "Dashboards", "Credit risk"]
    assert r["matchScore"] == round(65 / 3 + 0 + 7.5)


def test_score_match_reweights_when_there_are_no_nice_to_haves():
    reqs = {**REQS, "niceToHave": [], "minExperienceYrs": 0}
    assert core.score_match(reqs, [{"status": "met"}] * 3, experience_yrs=0)["matchScore"] == 100


def test_extract_requirements_cleans_and_dedupes(monkeypatch):
    monkeypatch.setattr(core, "_generate", lambda *a, **k: json.dumps({
        "roleTitle": "HRBP", "mustHave": ["Employee relations", " employee relations ", "Excel"],
        "niceToHave": ["Excel", "Workday"], "minExperienceYrs": "4+ years"}))
    reqs = core.extract_requirements("We need an HRBP with employee relations and Excel skills. " * 2, "m")
    assert reqs["mustHave"] == ["Employee relations", "Excel"]
    assert reqs["niceToHave"] == ["Workday"] and reqs["minExperienceYrs"] == 4


def test_match_candidates_ranks_best_fit_first(monkeypatch):
    def fake(model, prompt, fmt=None):
        statuses = ["met", "met", "met", "met"] if "Rohan" in prompt else ["missing"] * 4
        return json.dumps({"assessments": [{"requirement": "x", "status": s, "evidence": ""} for s in statuses],
                           "summary": "ok"})
    monkeypatch.setattr(core, "_generate", fake)
    pool = [{"id": "a", "candidateName": "Isha", "totalExperienceYrs": 3, "skills": []},
            {"id": "b", "candidateName": "Rohan", "totalExperienceYrs": 4, "skills": ["SQL"]}]
    results = core.match_candidates(REQS, pool, "m", k=10)
    assert [r["candidateName"] for r in results] == ["Rohan", "Isha"]
    assert results[0]["matchScore"] == 100
