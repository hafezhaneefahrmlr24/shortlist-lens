"""Core logic for the offline recruitment assistant.

Everything here runs on the local machine: resume parsing, calls to a local
Ollama model, validation, de-duplication and search. No resume data is sent
to an external service.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import docx
import pypdf
import requests

import pool_search

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_FILE = DATA_DIR / "talent_pool.json"
RESUME_DIR = DATA_DIR / "resumes"
SAMPLE_DIR = APP_DIR / "sample_resumes"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
REQUEST_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "180"))
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
PREFERRED_MODELS = ["qwen3:4b", "llama3.1:8b", "llama3:latest"]
MAX_RESUME_CHARS = 12000
SUPPORTED_TYPES = {".pdf", ".docx"}

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidateName": {"type": "string"},
        "totalExperienceYrs": {"type": "number"},
        "category": {"type": "string", "enum": ["Tech", "Non-Tech"]},
        "currentRole": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}},
        "education": {"type": "string"},
        "clarityScore": {"type": "integer"},
        "experienceScore": {"type": "integer"},
        "skillsScore": {"type": "integer"},
        "impactScore": {"type": "integer"},
        "summary": {"type": "string"},
    },
    "required": [
        "candidateName", "totalExperienceYrs", "category", "currentRole", "skills", "education",
        "clarityScore", "experienceScore", "skillsScore", "impactScore", "summary",
    ],
}

EXTRACTION_PROMPT = """You are a recruitment assistant. Read the resume below and return JSON with these fields:
- candidateName: the candidate's full name.
- totalExperienceYrs: total years of professional experience, as a number (0 for a fresher).
- category: "Tech" for software, data, analytics, cloud or infrastructure roles; "Non-Tech" for everything else (sales, marketing, content, editorial, HR, product, creative).
- currentRole: most recent job title and organisation.
- skills: up to 15 concrete skills or tools named in the resume.
- education: highest qualification and institution, as written.
- clarityScore (0-20): is the resume specific, well organised and easy to scan? Vague or cluttered: 0-8. Clear: 9-15. Crisp and specific throughout: 16-20.
- experienceScore (0-30): depth and progression of professional experience. Under 2 years: 0-10. 2 to 5 years: 10-20. Over 5 years with growing responsibility: 20-30.
- skillsScore (0-30): concrete skills backed by evidence of use (projects, tools, results), not just listed. Listed only: 0-10. Some evidence: 11-20. Strong evidence across several skills: 21-30.
- impactScore (0-20): measurable results, ownership and leadership. No results stated: 0-5. Some results: 6-13. Clear numbers and people or projects led: 14-20.
Use the full range of each scale and score each part independently; different resumes should get different scores. The candidate's name, gender, age, location and the prestige of their college must not affect any score.
- summary: two or three sentences on the candidate's profile.
Use only information in the resume. Return JSON only.

Resume:
\"\"\"
{resume}
\"\"\"
"""

CHAT_PROMPT = """You are a recruitment assistant helping a recruiter search a talent pool of {total} candidates.
A search over the pool shortlisted the {shown} candidates below{note}. Use only these candidates.
Name the candidates you recommend and say briefly why, citing their skills or experience.
If none of them fit, say so plainly. Never invent candidates or details.

Candidates (one JSON object per line):
{context}

Recruiter's question: {question}
"""


class OllamaError(RuntimeError):
    """Raised when the local model can't be reached or returns an error."""


# ---------- Storage ----------

def load_pool() -> list[dict]:
    """Load the talent pool, skipping empty or malformed records."""
    if not DB_FILE.exists():
        return []
    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("candidateName")]


def save_pool(pool: list[dict]) -> None:
    """Write the pool atomically, so a crash mid-write can't corrupt it."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(pool, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DB_FILE)


def remove_candidates(pool: list[dict], ids: set[str]) -> list[dict]:
    """Remove candidates by id, along with their stored resume files."""
    kept = []
    for r in pool:
        if r.get("id") in ids:
            stored = r.get("storedFilename")
            if stored and (RESUME_DIR / stored).exists():
                (RESUME_DIR / stored).unlink()
        else:
            kept.append(r)
    return kept


def clear_pool() -> None:
    """Delete the whole pool and every stored resume."""
    if DB_FILE.exists():
        DB_FILE.unlink()
    if RESUME_DIR.exists():
        for p in RESUME_DIR.iterdir():
            if p.is_file():
                p.unlink()


# ---------- Resume parsing ----------

def extract_text(filename: str, data: bytes) -> str:
    """Return the plain text of a PDF or DOCX resume."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if ext == ".docx":
        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(p for p in parts if p.strip()).strip()
    raise ValueError(f"unsupported file type {ext or '(none)'}")


# ---------- Local model (Ollama) ----------

def list_models() -> list[str]:
    """Names of the models installed in the local Ollama server."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
    except requests.RequestException as e:
        raise OllamaError(
            f"Can't reach Ollama at {OLLAMA_URL}. Open the Ollama app, or run `ollama serve` in Terminal."
        ) from e
    return sorted(m["name"] for m in r.json().get("models", []) if m.get("name"))


def pick_default_model(installed: list[str]) -> str | None:
    """Prefer models known to handle structured output well; else the first installed."""
    if not installed:
        return None
    names = set(installed)
    for pref in PREFERRED_MODELS:
        if pref in names:
            return pref
        if f"{pref}:latest" in names:
            return f"{pref}:latest"
    return installed[0]


def _is_thinking_model(model: str) -> bool:
    return model.split(":")[0].lower() in {"qwen3", "deepseek-r1", "magistral"}


def _strip_think(text: str) -> str:
    """Remove a model's visible reasoning, even when the opening <think> tag is missing."""
    text = text or ""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return re.sub(r"<think>.*", "", text, flags=re.S).strip()


def _generate(model: str, prompt: str, fmt=None) -> str:
    """Call Ollama's /api/generate and return the response text."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": NUM_CTX},
    }
    if _is_thinking_model(model):
        payload["think"] = False  # honoured by newer Ollama versions
        payload["prompt"] = prompt + "\n/no_think"  # Qwen3's own switch; works on any Ollama version
    if fmt is not None:
        payload["format"] = fmt
    url = f"{OLLAMA_URL}/api/generate"
    try:
        r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 400 and isinstance(fmt, dict):
            payload["format"] = "json"  # older Ollama versions only accept "json"
            r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except requests.Timeout as e:
        raise OllamaError(
            f"The model took longer than {REQUEST_TIMEOUT} seconds. Try a smaller model."
        ) from e
    except requests.RequestException as e:
        raise OllamaError(f"Ollama request failed: {e}") from e
    return _strip_think(r.json().get("response", ""))


def _first_json_object(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the model output")
    return text[start:end + 1]


def extract_candidate(text: str, model: str) -> dict:
    """Ask the model for structured fields, then validate them."""
    prompt = EXTRACTION_PROMPT.replace("{resume}", text[:MAX_RESUME_CHARS])
    raw = _generate(model, prompt, fmt=CANDIDATE_SCHEMA)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_first_json_object(raw))
    return normalize_candidate(data)


# ---------- Validation ----------

KEY_ALIASES = {
    "candidateName": ["candidateName", "name", "candidate_name", "fullName", "full_name"],
    "totalExperienceYrs": ["totalExperienceYrs", "experience", "experienceYears",
                           "totalExperience", "total_experience", "years_of_experience"],
    "category": ["category", "type"],
    "currentRole": ["currentRole", "current_role", "role", "title"],
    "skills": ["skills", "keySkills", "key_skills"],
    "education": ["education", "highestEducation", "qualification"],
    "fitScore": ["fitScore", "fit_score", "score", "htScore", "ht_score"],
    "summary": ["summary", "profileSummary", "profile_summary"],
}
PLACEHOLDER_NAMES = {"", "unknown", "n/a", "na", "none", "string", "candidate", "name"}


def _pick(raw: dict, keys: list[str]):
    for k in keys:
        if raw.get(k) not in (None, "", []):
            return raw[k]
    return None


def _to_float(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else 0.0


def normalize_candidate(raw) -> dict:
    """Coerce model output into a clean record, or raise ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("model output was not a JSON object")
    name = str(_pick(raw, KEY_ALIASES["candidateName"]) or "").strip()
    if name.lower() in PLACEHOLDER_NAMES:
        raise ValueError("no candidate name in the model output")

    category = str(_pick(raw, KEY_ALIASES["category"]) or "").strip().lower()
    category = "Tech" if category in {"tech", "technical", "technology"} else "Non-Tech"

    skills = _pick(raw, KEY_ALIASES["skills"]) or []
    if isinstance(skills, str):
        skills = re.split(r"[,;\n]", skills)
    seen, clean_skills = set(), []
    for s in skills:
        s = str(s).strip()
        if s and s.casefold() not in seen:
            seen.add(s.casefold())
            clean_skills.append(s)

    return {
        "candidateName": name,
        "totalExperienceYrs": round(min(max(_to_float(_pick(raw, KEY_ALIASES["totalExperienceYrs"])), 0.0), 50.0), 1),
        "category": category,
        "currentRole": str(_pick(raw, KEY_ALIASES["currentRole"]) or "").strip(),
        "skills": clean_skills[:15],
        "education": str(_pick(raw, KEY_ALIASES["education"]) or "").strip(),
        **_scores(raw),
        "summary": str(_pick(raw, KEY_ALIASES["summary"]) or "").strip(),
    }


SUB_SCORES = {"clarityScore": 20, "experienceScore": 30, "skillsScore": 30, "impactScore": 20}


def _scores(raw: dict) -> dict:
    """Clamp each sub-score to its scale; the fit score is their sum."""
    if any(raw.get(k) is not None for k in SUB_SCORES):
        subs = {k: int(min(max(_to_float(raw.get(k)), 0), cap)) for k, cap in SUB_SCORES.items()}
        return {**subs, "fitScore": sum(subs.values())}
    return {"fitScore": int(min(max(_to_float(_pick(raw, KEY_ALIASES["fitScore"])), 0), 100))}


# ---------- Adding resumes ----------

def add_resume(filename: str, data: bytes, model: str, pool: list[dict]):
    """Parse, extract and store one resume.

    Returns (status, message, record) where status is
    "added", "duplicate", "no_text" or "error". The pool is updated in place.
    """
    digest = hashlib.sha256(data).hexdigest()
    if any(r.get("fileHash") == digest for r in pool):
        return "duplicate", f"{filename}: already in the pool, skipped.", None
    try:
        text = extract_text(filename, data)
    except Exception as e:  # corrupt, encrypted or unsupported files
        return "error", f"{filename}: couldn't read the file ({e}).", None
    if len(text) < 50:
        return "no_text", f"{filename}: no readable text. Scanned PDFs need OCR, which isn't supported yet.", None
    try:
        candidate = extract_candidate(text, model)
    except OllamaError as e:
        return "error", f"{filename}: {e}", None
    except (ValueError, json.JSONDecodeError) as e:
        return "error", f"{filename}: the model's output couldn't be used ({e}).", None

    same_person = next(
        (r for r in pool
         if r["candidateName"].casefold() == candidate["candidateName"].casefold()
         and abs(r.get("totalExperienceYrs", 0) - candidate["totalExperienceYrs"]) < 0.5),
        None,
    )
    if same_person:
        return "duplicate", f"{filename}: {candidate['candidateName']} is already in the pool, skipped.", None

    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{digest[:12]}{Path(filename).suffix.lower()}"
    (RESUME_DIR / stored).write_bytes(data)
    record = {
        "id": digest[:12],
        **candidate,
        "originalFilename": Path(filename).name,
        "storedFilename": stored,
        "fileHash": digest,
        "resumeText": text[:MAX_RESUME_CHARS],
        "model": model,
        "addedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    pool.append(record)
    return "added", f"{filename}: added {candidate['candidateName']}.", record


# ---------- Search, then ask ----------

STOPWORDS = {
    "a", "an", "and", "any", "are", "best", "can", "candidate", "candidates", "do", "does",
    "experience", "experienced", "find", "for", "from", "give", "has", "have", "in", "is",
    "list", "me", "of", "on", "or", "people", "show", "skills", "someone", "the", "their",
    "to", "who", "with", "years", "year", "yrs", "background", "worked", "working",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#]+(?:\.[a-z0-9]+)*", (text or "").lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def _min_experience(question: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years|yrs|year)", question.lower())
    return float(match.group(1)) if match else None


def search(pool: list[dict], question: str, k: int = 12):
    """Rank the pool against a plain-English question (BM25 with synonyms, see pool_search).

    Returns (shortlist, matched). A minimum experience in the question ("5+ years") is applied
    as a filter. If nothing matches, falls back to the highest-scoring candidates and sets matched=False.
    """
    min_exp = _min_experience(question)
    eligible = [r for r in pool if min_exp is None or r.get("totalExperienceYrs", 0) >= min_exp]
    if not eligible:
        return [], min_exp is not None
    text = re.sub(r"\d+(?:\.\d+)?\s*\+?\s*(?:years|yrs|year)", " ", question.lower())
    index = _index_for(eligible)
    scores = pool_search.rank_for_question(index, text)
    scored = [(sc, r.get("fitScore", 0), r) for sc, r in zip(scores, eligible) if sc > 0]
    has_terms = bool(pool_search.query_groups(text))
    matched = bool(scored) or (min_exp is not None and not has_terms)
    if not scored:
        scored = [(0, r.get("fitScore", 0), r) for r in eligible]
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [r for _, _, r in scored[:k]], matched


def answer_question(question: str, pool: list[dict], model: str, k: int = 12):
    """Search the pool, then ask the model about the shortlist only."""
    shortlist, matched = search(pool, question, k)
    if not shortlist:
        return "No candidates meet that requirement.", []
    context = "\n".join(
        json.dumps({
            "name": r["candidateName"],
            "experienceYrs": r.get("totalExperienceYrs"),
            "currentRole": r.get("currentRole"),
            "skills": r.get("skills"),
            "summary": r.get("summary"),
        }, ensure_ascii=False)
        for r in shortlist
    )
    note = "" if matched else " (none matched the question's keywords, so these are the highest-scoring candidates)"
    prompt = CHAT_PROMPT.format(total=len(pool), shown=len(shortlist), note=note,
                                context=context, question=question)
    answer = _generate(model, prompt).strip()
    return answer or "The model didn't return an answer. Try again, or pick another model in the sidebar.", shortlist


# ---------- Matching against a job description ----------

SAMPLE_JOBS_DIR = APP_DIR / "sample_jobs"

REQUIREMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "roleTitle": {"type": "string"},
        "mustHave": {"type": "array", "items": {"type": "string"}},
        "niceToHave": {"type": "array", "items": {"type": "string"}},
        "minExperienceYrs": {"type": "number"},
    },
    "required": ["roleTitle", "mustHave", "niceToHave", "minExperienceYrs"],
}

ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "status": {"type": "string", "enum": ["met", "partial", "missing"]},
                    "evidence": {"type": "string"},
                },
                "required": ["requirement", "status", "evidence"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["assessments", "summary"],
}

REQUIREMENTS_PROMPT = """Read the job description below and list what a recruiter would screen for. Return JSON with:
- roleTitle: the job title.
- mustHave: 3 to 6 essential requirements, each a short phrase (for example "SQL" or "employee relations experience").
- niceToHave: 0 to 4 desirable but optional requirements, each a short phrase.
- minExperienceYrs: the minimum years of experience asked for, as a number (0 if not stated).
Don't list years of experience as a mustHave item; use minExperienceYrs for that.
Only include requirements the job description states or clearly implies.

Job description:
\"\"\"
{jd}
\"\"\"
"""

ASSESSMENT_PROMPT = """You are screening one candidate against a job's requirements.
For each requirement, decide what the resume shows:
- "met": clear evidence in the resume
- "partial": related or indirect evidence
- "missing": no evidence
For "met" or "partial", quote a short phrase from the resume as evidence; otherwise leave evidence empty.
Judge only what the resume says. The candidate's name, gender, age, location and college prestige must not affect your judgement.

Requirements, in order:
{requirements}

Resume:
\"\"\"
{resume}
\"\"\"

Return JSON with "assessments" (one item per requirement, in the same order, each with "requirement", "status" and "evidence") and "summary" (one sentence on the candidate's overall fit for this job).
"""

STATUS_VALUE = {"met": 1.0, "partial": 0.5, "missing": 0.0}


def _clean_list(items, limit: int) -> list[str]:
    raw = items if isinstance(items, list) else re.split(r"[,;\n]", str(items or ""))
    out, seen = [], set()
    for item in raw:
        s = str(item).strip().strip("-•*").strip()
        if s and s.casefold() not in seen:
            seen.add(s.casefold())
            out.append(s)
    return out[:limit]


def _parse_json(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_first_json_object(raw))
    if not isinstance(data, dict):
        raise ValueError("model output was not a JSON object")
    return data


def extract_requirements(jd: str, model: str) -> dict:
    """Ask the model what a job description requires."""
    if len(jd.strip()) < 40:
        raise ValueError("paste a fuller job description, at least a few lines")
    data = _parse_json(_generate(model, REQUIREMENTS_PROMPT.replace("{jd}", jd[:8000]), fmt=REQUIREMENTS_SCHEMA))
    must = _clean_list(data.get("mustHave"), 6)
    if not must:
        raise ValueError("no requirements found in that job description")
    must_keys = {m.casefold() for m in must}
    return {
        "roleTitle": str(data.get("roleTitle") or "").strip() or "This role",
        "mustHave": must,
        "niceToHave": [n for n in _clean_list(data.get("niceToHave"), 4) if n.casefold() not in must_keys],
        "minExperienceYrs": round(min(max(_to_float(data.get("minExperienceYrs")), 0.0), 30.0), 1),
    }


def candidate_text(record: dict) -> str:
    """The stored resume's text, or the extracted profile if the file isn't available."""
    if len(record.get("resumeText") or "") >= 50:
        return record["resumeText"]
    stored = record.get("storedFilename")
    if stored and (RESUME_DIR / stored).exists():
        try:
            text = extract_text(stored, (RESUME_DIR / stored).read_bytes())
            if len(text) >= 50:
                return text
        except Exception:
            pass
    return "\n".join([
        record.get("candidateName", ""), record.get("currentRole", ""),
        f"{record.get('totalExperienceYrs', 0)} years of experience",
        "Skills: " + ", ".join(record.get("skills", [])),
        "Education: " + record.get("education", ""), record.get("summary", ""),
    ])


def score_match(reqs: dict, assessments: list, experience_yrs: float) -> dict:
    """Turn per-requirement judgements into a match score. The arithmetic is done here, not by the model."""
    items = reqs["mustHave"] + reqs["niceToHave"]
    checks = []
    for i, requirement in enumerate(items):
        a = assessments[i] if i < len(assessments) and isinstance(assessments[i], dict) else {}
        status = str(a.get("status", "missing")).lower().strip()
        checks.append({
            "requirement": requirement,
            "status": status if status in STATUS_VALUE else "missing",
            "evidence": str(a.get("evidence") or "").strip(),
            "type": "must" if i < len(reqs["mustHave"]) else "nice",
        })
    must = [c for c in checks if c["type"] == "must"]
    nice = [c for c in checks if c["type"] == "nice"]
    must_part = sum(STATUS_VALUE[c["status"]] for c in must) / len(must)
    min_exp = reqs.get("minExperienceYrs") or 0
    if min_exp <= 0 or experience_yrs >= min_exp:
        exp_part = 1.0
    elif experience_yrs >= min_exp - 1:
        exp_part = 0.5
    else:
        exp_part = 0.0
    if nice:
        score = 65 * must_part + 20 * sum(STATUS_VALUE[c["status"]] for c in nice) / len(nice) + 15 * exp_part
    else:
        score = 80 * must_part + 20 * exp_part
    return {
        "matchScore": int(round(score)),
        "mustMet": sum(c["status"] == "met" for c in must),
        "mustTotal": len(must),
        "experienceOk": exp_part == 1.0,
        "gaps": [c["requirement"] for c in checks if c["status"] == "missing"],
        "assessments": checks,
    }


_TEXT_CACHE: dict[str, str] = {}
_INDEX_CACHE: dict[tuple, "pool_search.Index"] = {}


def _doc_text(record: dict) -> str:
    """Everything searchable about a candidate: the resume text plus the extracted profile."""
    key = record.get("fileHash") or record.get("id")
    body = _TEXT_CACHE.get(key) if key else None
    if body is None:
        body = candidate_text(record)
        if key:
            _TEXT_CACHE[key] = body
    return " ".join([body, " ".join(record.get("skills", [])), record.get("currentRole", ""),
                     record.get("summary", ""), record.get("category", "")])


def _index_for(records: list[dict]) -> "pool_search.Index":
    key = tuple(r.get("fileHash") or r.get("id") or f"{r.get('candidateName')}|{i}" for i, r in enumerate(records))
    if key not in _INDEX_CACHE:
        if len(_INDEX_CACHE) > 8:
            _INDEX_CACHE.clear()
        _INDEX_CACHE[key] = pool_search.Index([_doc_text(r) for r in records])
    return _INDEX_CACHE[key]


def rank_pool(pool: list[dict], reqs: dict) -> list[dict]:
    """Stage 1: rank every candidate against the requirements, without calling the model.

    Returns dicts with the record and why it ranked where it did, best first. Candidates more than
    a year below the minimum experience are ranked lower, not removed, in case the extracted
    experience is wrong.
    """
    if not pool:
        return []
    index = _index_for(pool)
    rows = pool_search.rank_for_requirements(index, reqs["mustHave"], reqs["niceToHave"])
    min_exp = reqs.get("minExperienceYrs") or 0
    ranked = []
    for record, row in zip(pool, rows):
        below = bool(min_exp) and record.get("totalExperienceYrs", 0) < min_exp - 1
        ranked.append({"record": record, **row, "belowExperience": below,
                       "searchScore": round(row["score"] * (0.6 if below else 1.0) * 100, 1)})
    ranked.sort(key=lambda r: (r["searchScore"], r["record"].get("fitScore", 0)), reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["searchRank"] = i
    return ranked


def _prefilter(pool: list[dict], reqs: dict, k: int) -> list[dict]:
    """The k candidates that search ranks highest for these requirements."""
    return [r["record"] for r in rank_pool(pool, reqs)[:k]]


def match_candidates(reqs: dict, pool: list[dict], model: str, k: int = 10, progress=None,
                     ranked: list[dict] | None = None) -> list[dict]:
    """Stage 2: the model checks the top k candidates from search against every requirement."""
    ranked = ranked if ranked is not None else rank_pool(pool, reqs)
    shortlist = ranked[:k]
    numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(reqs["mustHave"] + reqs["niceToHave"], start=1))
    results = []
    for i, hit in enumerate(shortlist, start=1):
        record = hit["record"]
        if progress:
            progress(i, len(shortlist), record)
        prompt = (ASSESSMENT_PROMPT.replace("{requirements}", numbered)
                  .replace("{resume}", candidate_text(record)[:MAX_RESUME_CHARS]))
        base = {"id": record.get("id"), "candidateName": record["candidateName"],
                "experienceYrs": record.get("totalExperienceYrs", 0), "searchRank": hit["searchRank"]}
        try:
            data = _parse_json(_generate(model, prompt, fmt=ASSESSMENT_SCHEMA))
            scored = score_match(reqs, data.get("assessments") or [], base["experienceYrs"])
            results.append({**base, "summary": str(data.get("summary") or "").strip(), **scored})
        except (ValueError, json.JSONDecodeError) as e:
            results.append({**base, "error": f"couldn't be assessed ({e})"})
    results.sort(key=lambda r: (r.get("matchScore", -1), r.get("mustMet", 0), -r["searchRank"]), reverse=True)
    return results
