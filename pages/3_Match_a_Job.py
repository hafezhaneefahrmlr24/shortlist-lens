import time

import pandas as pd
import streamlit as st

import core
import ui

st.set_page_config(page_title="Match a Job", page_icon="🎯", layout="wide")
model = ui.model_picker()
blind = ui.blind_toggle()

st.title("Match candidates to a job")
st.write("Paste a job description. The app works in two stages: a fast search ranks every resume in the pool "
         "against the job's requirements, then the local model checks the top candidates against each requirement "
         "and quotes the evidence. The match score is calculated from those checks, not guessed by the model.")

pool = core.load_pool()
if not pool:
    st.info("The talent pool is empty. Add resumes first.")
    st.stop()

samples = {p.read_text(encoding="utf-8").splitlines()[0].strip(): p for p in sorted(core.SAMPLE_JOBS_DIR.glob("*.txt"))}
if samples:
    pick = st.selectbox("Start from a sample job (optional)", [""] + list(samples))
    if pick and st.session_state.get("jd_sample") != pick:
        st.session_state["jd_sample"] = pick
        st.session_state["jd_text"] = samples[pick].read_text(encoding="utf-8")
jd = st.text_area("Job description", key="jd_text", height=260, placeholder="Paste the job description here")

SECONDS_PER_CHECK = 9  # measured on a MacBook Air with qwen3:4b
if len(pool) > 1:
    k = st.slider("Candidates the model checks in detail", 1, min(50, len(pool)), min(25, len(pool)),
                  help="Search ranks every resume in under a second. The model then reads only the top ones, "
                       f"about {SECONDS_PER_CHECK} seconds each on a laptop.")
    st.caption(f"Search covers all {len(pool)} resumes. Detailed check of {k}: about "
               f"{max(1, round(k * SECONDS_PER_CHECK / 60))} min on a laptop.")
else:
    k = 1

if model is None:
    st.warning("Start Ollama and pick a model in the sidebar first.")
if st.button("Find matches", type="primary", disabled=model is None or not (jd or "").strip()):
    try:
        with st.spinner("Reading the job description..."):
            reqs = core.extract_requirements(jd, model)
        t0 = time.perf_counter()
        ranked = core.rank_pool(pool, reqs)
        search_ms = (time.perf_counter() - t0) * 1000
        st.info(f"Searched {len(pool)} resumes in {search_ms:,.0f} ms. The model is now checking the top {min(k, len(pool))}.")
        bar = st.progress(0.0, text="Checking candidates")
        started = time.time()

        def progress(i, n, record):
            eta = ""
            if i > 1:
                left = (time.time() - started) / (i - 1) * (n - i + 1)
                eta = f" · about {max(1, round(left / 60))} min left" if left >= 60 else f" · about {int(left)} s left"
            bar.progress((i - 1) / n, text=f"Checking {ui.display_name(record, blind)} ({i} of {n}){eta}")

        results = core.match_candidates(reqs, pool, model, k, progress, ranked=ranked)
        bar.progress(1.0, text="Done")
        st.session_state["match"] = {
            "reqs": reqs, "results": results, "total": len(pool), "k": min(k, len(pool)),
            "search_ms": search_ms, "check_s": time.time() - started,
            "search": [{"record": h["record"], "searchRank": h["searchRank"], "searchScore": h["searchScore"],
                        "mustCovered": h["mustCovered"], "mustTotal": h["mustTotal"],
                        "niceCovered": h["niceCovered"], "niceTotal": h["niceTotal"],
                        "belowExperience": h["belowExperience"]} for h in ranked[:50]],
        }
    except core.OllamaError as e:
        st.error(str(e))
    except ValueError as e:
        st.error(f"Couldn't read the job description: {e}")

match = st.session_state.get("match")
if match:
    reqs, results = match["reqs"], match["results"]
    assessed = [r for r in results if "error" not in r]

    st.subheader(f"What the {reqs['roleTitle']} role needs")
    c1, c2, c3 = st.columns([2, 2, 1])
    c1.markdown("**Must have**\n" + "\n".join(f"- {r}" for r in reqs["mustHave"]))
    c2.markdown("**Nice to have**\n" + ("\n".join(f"- {r}" for r in reqs["niceToHave"]) or "- None stated"))
    c3.metric("Minimum experience", f"{reqs['minExperienceYrs']:g} yrs" if reqs["minExperienceYrs"] else "Not stated")

    st.subheader("Ranked shortlist")
    weights = ("must-haves 65%, nice-to-haves 20%, experience 15%" if reqs["niceToHave"]
               else "must-haves 80%, experience 20%")
    if "search_ms" in match:
        st.caption(f"Search ranked all {match['total']} resumes in {match['search_ms']:,.0f} ms; the model checked the "
                   f"top {match['k']} in {match['check_s'] / 60:.1f} min. Match score = {weights}. "
                   "Met counts in full, partial counts half.")
    else:
        st.caption(f"Assessed {len(results)} of {match['total']} candidates. Match score = {weights}. "
                   "Met counts in full, partial counts half.")
    rows = [{
        "Rank": i,
        "Candidate": ui.display_name(r, blind),
        "Match": r["matchScore"],
        "Must-haves met": f"{r['mustMet']} of {r['mustTotal']}",
        "Experience": f"{r['experienceYrs']:g} yrs" + ("" if r["experienceOk"] else " (below minimum)"),
        "Gaps": ", ".join(r["gaps"]) or "None",
        "Search rank": r.get("searchRank"),
        "Summary": ui.redact(r["summary"], r, blind),
    } for i, r in enumerate(assessed, start=1)]
    table = pd.DataFrame(rows)
    if not table.empty:
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "Match": st.column_config.ProgressColumn("Match", min_value=0, max_value=100, format="%d")})
        st.download_button("Download shortlist (CSV, opens in Excel)", table.to_csv(index=False).encode("utf-8"),
                           file_name="shortlist.csv", mime="text/csv")

    if match.get("search"):
        with st.expander(f"How search ranked the pool (top {len(match['search'])} of {match['total']})"):
            st.caption("Search looks for each requirement's words and common synonyms in the full resume text. "
                       "It decides who the model reads, so a candidate it ranks low is never checked.")
            st.dataframe(pd.DataFrame([{
                "Search rank": h["searchRank"],
                "Candidate": ui.display_name(h["record"], blind),
                "Search score": h["searchScore"],
                "Must-haves found": f"{h['mustCovered']} of {h['mustTotal']}",
                "Nice-to-haves found": f"{h['niceCovered']} of {h['niceTotal']}",
                "Experience": f"{h['record'].get('totalExperienceYrs', 0):g} yrs" + (" (low)" if h["belowExperience"] else ""),
                "Checked by model": "Yes" if h["searchRank"] <= match["k"] else "",
            } for h in match["search"]]), hide_index=True, width="stretch")

    st.subheader("Evidence for each candidate")
    labels = {"met": "✅ Met", "partial": "🟡 Partial", "missing": "❌ Missing"}
    for r in assessed:
        with st.expander(f"{ui.display_name(r, blind)}: {r['matchScore']}"):
            st.write(ui.redact(r["summary"], r, blind))
            # st.table wraps long text, so the evidence quotes are readable in full
            st.table(pd.DataFrame([{
                "Requirement": a["requirement"],
                "Type": "Must have" if a["type"] == "must" else "Nice to have",
                "Result": labels[a["status"]],
                "Evidence from the resume": ui.redact(a["evidence"], r, blind) or "—",
            } for a in r["assessments"]]).set_index("Requirement"))
    for r in results:
        if "error" in r:
            st.warning(f"{ui.display_name(r, blind)} {r['error']}")
    st.caption("A screening aid, not a decision. Check the evidence and the original resume before acting.")
