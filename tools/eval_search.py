"""Measure how well the search stage finds the right candidates, using the pool's answer key.

    python tools/eval_search.py                      # sample_pool_500, top 25
    python tools/eval_search.py --pool sample_pool_500 --k 25

Search runs on the resume files directly, so no model is needed. The requirements for the two
sample jobs are the ones qwen3:4b extracted (sample_jobs/*.requirements.json).
Recall@k = share of genuinely qualified candidates that search puts in the top k, i.e. the
candidates the model will actually get to check.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core  # noqa: E402
import pool_search  # noqa: E402

JOBS = {"hrbp_job": "hr_business_partner", "data_analyst_job": "data_analyst_bfsi"}


def records_from_files(folder: Path) -> list[dict]:
    out = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in core.SUPPORTED_TYPES:
            continue
        text = core.extract_text(p.name, p.read_bytes())
        years = re.search(r"(\d+(?:\.\d+)?)\s+years of\s+experience", text.replace("\n", " "))
        skills = re.search(r"\nSkills\n(.+?)\nEducation", text, re.S)
        out.append({"id": p.stem, "fileHash": p.name, "candidateName": text.splitlines()[0].strip(),
                    "file": p.name, "resumeText": text,
                    "totalExperienceYrs": float(years.group(1)) if years else 0.0,
                    "skills": [s.strip() for s in skills.group(1).replace("\n", " ").split(",")] if skills else []})
    return out


def evaluate(pool, key, ks, label=""):
    rows = []
    for col, job in JOBS.items():
        reqs = json.loads((core.SAMPLE_JOBS_DIR / f"{job}.requirements.json").read_text())
        t0 = time.perf_counter()
        ranked = core.rank_pool(pool, reqs)
        ms = (time.perf_counter() - t0) * 1000
        rank_of = {r["record"]["file"]: r["searchRank"] for r in ranked}
        strong = [f for f, v in key.items() if v[col] == "strong"]
        near = [f for f, v in key.items() if v[col] == "near"]
        res = {"job": job, "pool": len(pool), "strong": len(strong), "ms": round(ms)}
        for k in ks:
            res[f"recall@{k}"] = sum(rank_of[f] <= k for f in strong) / len(strong)
        res["worst_rank"] = max(rank_of[f] for f in strong)
        res["near_in_top25"] = sum(rank_of[f] <= 25 for f in near)
        by_phrasing = {}
        for f in strong:
            by_phrasing.setdefault(key[f]["phrasing"] or "-", []).append(rank_of[f])
        res["ranks_by_phrasing"] = {p: sorted(v) for p, v in by_phrasing.items()}
        rows.append(res)
    for r in rows:
        print(label, json.dumps(r))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=str(ROOT / "sample_pool_500"))
    ap.add_argument("--k", type=int, nargs="+", default=[10, 25, 50])
    ap.add_argument("--no-synonyms", action="store_true", help="switch synonym expansion off, for comparison")
    args = ap.parse_args()
    folder = Path(args.pool)
    key = {r["file"]: r for r in csv.DictReader(open(folder / "answer_key.csv", encoding="utf-8"))}
    pool = records_from_files(folder)
    if args.no_synonyms:
        pool_search._NORM_GROUPS.clear()
    evaluate(pool, key, args.k, "no-synonyms" if args.no_synonyms else "with-synonyms")


if __name__ == "__main__":
    main()
