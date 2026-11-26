"""Stage 1 of matching: rank the whole talent pool without calling the model.

Each resume's full text is indexed with BM25, the ranking method behind most search engines.
Requirements are expanded with HR and data synonyms ("HRBP" also finds "People Partner",
"SQL" also finds "PostgreSQL"), and candidates are ranked mainly by how many requirements
their resume covers. This takes well under a second for thousands of resumes, so the slow
model step only has to read the top few.
"""
from __future__ import annotations

import math
import re
from collections import Counter

STOP = {
    "a", "an", "and", "any", "are", "as", "at", "be", "best", "by", "can", "candidate", "candidates", "do",
    "does", "experience", "experienced", "find", "for", "from", "give", "good", "has", "have", "in", "into",
    "is", "it", "list", "me", "of", "on", "or", "our", "people", "show", "skills", "someone", "the", "their",
    "to", "who", "with", "years", "year", "yrs", "background", "worked", "working", "strong", "comfortable",
    "able", "ability", "proven", "solid", "knowledge", "grounding", "hands", "plus", "least", "minimum",
    "would", "you", "your", "role", "shortlist", "why", "which", "what", "how", "should", "we", "i",
    "need", "needs", "looking", "want", "profile", "profiles", "relevant", "well", "etc", "another", "other",
    "through", "using", "use", "used", "across", "within", "including", "like", "such", "various", "related",
    "similar", "equivalent", "preferably", "ideally", "programmes", "programme", "programs", "program",
    "reducing", "improving", "improve", "drive", "driving", "ensure", "ensuring", "support", "supporting",
    "familiarity", "familiar", "exposure", "understanding", "expertise", "skilled", "excellent", "great",
    "demonstrated", "track", "record", "must", "nice", "required", "preferred", "least", "more", "than",
}

# Each group holds equivalent terms. A requirement that mentions any of them is searched with all of them.
SYNONYM_GROUPS = [
    ["hr business partner", "hrbp", "people partner", "hr generalist", "human resources business partner"],
    ["employee relations", "industrial relations", "grievance", "grievances", "disciplinary", "domestic inquiry"],
    ["performance management", "appraisal", "appraisals", "pms", "performance review", "performance reviews",
     "performance cycle", "performance cycles"],
    ["hr analytics", "people analytics", "people metrics", "workforce analytics", "hr dashboards", "hr metrics", "hr mis"],
    ["hris", "workday", "successfactors", "darwinbox", "oracle hcm", "keka", "sap hr"],
    ["attrition", "retention", "turnover", "early exits"],
    ["onboarding", "induction"],
    ["engagement", "employee engagement", "pulse survey"],
    ["stakeholders", "stakeholder", "business heads", "leadership team"],
    ["recruitment", "recruiting", "hiring", "talent acquisition", "sourcing"],
    ["sql", "postgresql", "postgres", "mysql", "sql server", "t sql", "pl sql", "bigquery", "snowflake"],
    ["python", "pandas", "numpy"],
    ["tableau", "power bi", "powerbi", "looker", "qlik"],
    ["data analysis", "data analyst", "analytics", "analyst"],
    ["bfsi", "banking", "bank", "financial services", "insurance", "insurer", "nbfc", "lending", "lender",
     "fintech", "credit card", "microfinance", "loan", "loans"],
    ["credit risk", "risk", "underwriting", "collections", "default"],
    ["statistics", "statistical", "regression", "hypothesis testing"],
    ["excel", "ms excel", "spreadsheets", "pivot tables", "vlookup"],
    ["machine learning", "ml", "scikit learn", "tensorflow", "pytorch"],
    ["javascript", "js", "node js", "nodejs", "react", "typescript"],
    ["aws", "gcp", "azure", "cloud"],
    ["seo", "search engine optimisation", "search engine optimization"],
    ["sales", "business development", "key accounts", "key account management"],
    ["payroll", "pf esi", "statutory compliance"],
]


def normalize(text: str) -> str:
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return f" {re.sub(r' +', ' ', text).strip()} "


def _words(norm: str) -> list[str]:
    return norm.split()


def _ngrams(words: list[str]) -> Counter:
    grams = Counter(words)
    grams.update(" ".join(words[i:i + 2]) for i in range(len(words) - 1))
    grams.update(" ".join(words[i:i + 3]) for i in range(len(words) - 2))
    return grams


_NORM_GROUPS = [[normalize(t).strip() for t in g] for g in SYNONYM_GROUPS]


def query_groups(text: str) -> list[list[str]]:
    """Turn a requirement or question into groups of search terms; any term in a group counts as a hit."""
    norm = normalize(text)
    groups, covered = [], set()
    for g in _NORM_GROUPS:
        hits = [t for t in g if f" {t} " in norm]
        if hits:
            groups.append(g)
            for t in hits:
                covered.update(t.split())
    seen = set()
    for w in _words(norm):
        if w in STOP or w in covered or w in seen or re.fullmatch(r"\d+\+?", w) or len(w) < 2:
            continue
        seen.add(w)
        groups.append([w])
    return groups


class Index:
    """BM25 over word 1- to 3-grams of each document."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = []
        self.length = []
        df = Counter()
        for d in docs:
            words = _words(normalize(d))
            grams = _ngrams(words)
            self.tf.append(grams)
            self.length.append(len(words) or 1)
            df.update(grams.keys())
        self.n = len(docs)
        self.avgdl = (sum(self.length) / self.n) if self.n else 1.0
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def has(self, i: int, term: str) -> bool:
        return term in self.tf[i]

    def bm25(self, i: int, term: str) -> float:
        f = self.tf[i].get(term, 0)
        if not f:
            return 0.0
        norm = self.k1 * (1 - self.b + self.b * self.length[i] / self.avgdl)
        return self.idf.get(term, 0.0) * f * (self.k1 + 1) / (f + norm)

    def group_score(self, i: int, group: list[str]) -> float:
        return max(self.bm25(i, t) for t in group)

    def coverage(self, i: int, groups: list[list[str]]) -> float:
        if not groups:
            return 0.0
        return sum(any(self.has(i, t) for t in g) for g in groups) / len(groups)


def rank_for_requirements(index: Index, must: list[str], nice: list[str]) -> list[dict]:
    """Score every document against a job's requirements. Returns one dict per document, in index order."""
    must_q = [query_groups(r) for r in must]
    nice_q = [query_groups(r) for r in nice]
    raw = []
    for i in range(index.n):
        cov_must = [index.coverage(i, q) for q in must_q]
        cov_nice = [index.coverage(i, q) for q in nice_q]
        bm = (2 * sum(index.group_score(i, g) for q in must_q for g in q)
              + sum(index.group_score(i, g) for q in nice_q for g in q))
        denom = 2 * len(must_q) + len(nice_q)
        cov = (2 * sum(cov_must) + sum(cov_nice)) / denom if denom else 0.0
        raw.append({"coverage": cov, "bm25": bm,
                    "mustCovered": sum(c >= 0.5 for c in cov_must), "mustTotal": len(must_q),
                    "niceCovered": sum(c >= 0.5 for c in cov_nice), "niceTotal": len(nice_q)})
    top = max((r["bm25"] for r in raw), default=0.0) or 1.0
    for r in raw:
        r["score"] = 0.65 * r["coverage"] + 0.35 * r["bm25"] / top
    return raw


def rank_for_question(index: Index, question: str) -> list[float]:
    groups = query_groups(question)
    if not groups:
        return [0.0] * index.n
    bm = [sum(index.group_score(i, g) for g in groups) for i in range(index.n)]
    top = max(bm, default=0.0) or 1.0
    return [0.6 * index.coverage(i, groups) + 0.4 * bm[i] / top for i in range(index.n)]
