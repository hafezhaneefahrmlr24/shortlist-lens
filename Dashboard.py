import pandas as pd
import plotly.express as px
import streamlit as st

import core
import ui

st.set_page_config(page_title="ShortlistLens", page_icon="🔎", layout="wide")
ui.model_picker()
blind = ui.blind_toggle()

st.title("ShortlistLens")
st.caption("Offline, evidence-backed CV shortlisting. A local AI model reads the resumes; no candidate data leaves this computer.")

pool = core.load_pool()
if not pool:
    st.info("The talent pool is empty. Go to **Add Resumes** to upload resumes or load the sample set.")
    st.stop()

df = pd.DataFrame(pool)
for col, default in [("fitScore", 0), ("totalExperienceYrs", 0.0), ("category", "Non-Tech"),
                     ("currentRole", ""), ("education", ""), ("summary", "")]:
    if col not in df:
        df[col] = default
df["skills"] = df.get("skills", pd.Series([[]] * len(df))).apply(lambda s: s if isinstance(s, list) else [])
df["fitScore"] = pd.to_numeric(df["fitScore"], errors="coerce").fillna(0).astype(int)
df["totalExperienceYrs"] = pd.to_numeric(df["totalExperienceYrs"], errors="coerce").fillna(0.0)
df["displayName"] = [ui.display_name(r, blind) for r in pool]
df["summary"] = [ui.redact(r.get("summary", ""), r, blind) for r in pool]

k1, k2, k3, k4 = st.columns(4)
k1.metric("Candidates", len(df))
k2.metric("Average fit score", f"{df['fitScore'].mean():.0f}")
k3.metric("Average experience", f"{df['totalExperienceYrs'].mean():.1f} yrs")
k4.metric("Tech / Non-Tech", f"{(df['category'] == 'Tech').sum()} / {(df['category'] != 'Tech').sum()}")

st.subheader("Filter")
all_skills = sorted({s for skills in df["skills"] for s in skills}, key=str.casefold)
c1, c2, c3 = st.columns([3, 1, 2])
skill_filter = c1.multiselect("Must have all of these skills", all_skills)
category_filter = c2.selectbox("Category", ["All", "Tech", "Non-Tech"])
max_exp = float(max(1.0, df["totalExperienceYrs"].max()))
min_exp = c3.slider("Minimum experience (years)", 0.0, max_exp, 0.0, 0.5)

view = df[df["totalExperienceYrs"] >= min_exp]
if category_filter != "All":
    view = view[view["category"] == category_filter]
if skill_filter:
    wanted = {s.casefold() for s in skill_filter}
    view = view[view["skills"].apply(lambda skills: wanted <= {s.casefold() for s in skills})]
st.caption(f"{len(view)} of {len(df)} candidates match.")

left, right = st.columns([3, 2])
with left:
    st.subheader("Most common skills")
    counts = view["skills"].explode().dropna().value_counts().head(10).sort_values()
    if counts.empty:
        st.write("No skills to show.")
    else:
        fig = px.bar(x=counts.values, y=counts.index, orientation="h",
                     labels={"x": "Candidates", "y": ""})
        fig.update_layout(margin=dict(l=0, r=10, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
with right:
    st.subheader("Experience vs fit score")
    fig = px.scatter(view, x="totalExperienceYrs", y="fitScore", color="category",
                     hover_name="displayName",
                     labels={"totalExperienceYrs": "Experience (yrs)", "fitScore": "Fit score",
                             "category": "Category"})
    fig.update_layout(margin=dict(l=0, r=10, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

st.subheader("Candidates")
score_cols = [c for c in ["clarityScore", "experienceScore", "skillsScore", "impactScore"] if c in view]
table = view.assign(skillList=view["skills"].apply(", ".join))[
    ["displayName", "fitScore", *score_cols, "totalExperienceYrs", "category", "currentRole",
     "skillList", "education", "summary"]
].rename(columns={
    "displayName": "Name", "fitScore": "Fit score", "clarityScore": "Clarity /20",
    "experienceScore": "Experience /30", "skillsScore": "Skills /30", "impactScore": "Impact /20",
    "totalExperienceYrs": "Experience (yrs)", "category": "Category", "currentRole": "Current role",
    "skillList": "Skills", "education": "Education", "summary": "AI summary",
})
st.dataframe(table.sort_values("Fit score", ascending=False), width="stretch", hide_index=True)
st.caption("Fit score = clarity + experience + skills + impact, each scored separately by the model. "
           "It's a screening aid: it ignores name, gender, age, location and college, and every decision stays with the recruiter.")

st.subheader("Download a resume")
if blind:
    st.info("Turn off blind screening to download original resumes.")
    options = {}
else:
    options = {f"{r['candidateName']} ({r.get('originalFilename', 'no file')})": r for r in pool}
choice = st.selectbox("Candidate", [""] + list(options)) if options else ""
if choice:
    record = options[choice]
    path = core.RESUME_DIR / record.get("storedFilename", "")
    if record.get("storedFilename") and path.exists():
        st.download_button(f"Download {record['originalFilename']}", path.read_bytes(),
                           file_name=record["originalFilename"])
    else:
        st.warning("The original file for this candidate isn't stored.")

with st.sidebar:
    st.header("Manage the pool")
    labels = {f"{r['candidateName']} · {r.get('id', '')}": r.get("id") for r in pool}
    to_remove = st.multiselect("Remove candidates", list(labels))
    if to_remove and st.button("Remove selected"):
        core.save_pool(core.remove_candidates(pool, {labels[label] for label in to_remove}))
        st.rerun()
    confirm = st.checkbox("I understand this deletes every candidate")
    if st.button("Clear the entire pool", disabled=not confirm):
        core.clear_pool()
        st.rerun()
