import streamlit as st

import core
import ui

st.set_page_config(page_title="Add Resumes", page_icon="📄", layout="wide")
model = ui.model_picker()

st.title("Add resumes")
st.write("Upload PDF or DOCX resumes. The local model reads each one, extracts the key details, "
         "and adds the candidate to the talent pool. Files already in the pool are skipped.")

STATUS_DISPLAY = {"added": st.success, "duplicate": st.info, "no_text": st.warning, "error": st.error}


def process(files: list[tuple[str, bytes]]) -> None:
    pool = core.load_pool()
    counts = {"added": 0, "duplicate": 0, "no_text": 0, "error": 0}
    bar = st.progress(0.0, text="Starting")
    for i, (name, data) in enumerate(files, start=1):
        bar.progress((i - 1) / len(files), text=f"Reading {name} ({i} of {len(files)})")
        status, message, _ = core.add_resume(name, data, model, pool)
        counts[status] += 1
        STATUS_DISPLAY[status](message)
        if status == "added":
            core.save_pool(pool)  # save after each resume, so progress survives an interruption
    bar.progress(1.0, text="Done")
    st.write(f"**Added {counts['added']}**, skipped {counts['duplicate']} duplicate(s), "
             f"{counts['no_text']} unreadable, {counts['error']} error(s).")


if model is None:
    st.warning("Start Ollama and pick a model in the sidebar first.")
    st.stop()

uploaded = st.file_uploader("Resumes", type=["pdf", "docx"], accept_multiple_files=True)
if uploaded and st.button(f"Process {len(uploaded)} file(s)", type="primary"):
    process([(f.name, f.getvalue()) for f in uploaded])

st.divider()
st.subheader("Try it with sample resumes")
samples = sorted(p for p in core.SAMPLE_DIR.glob("*") if p.suffix.lower() in core.SUPPORTED_TYPES)
st.write(f"{len(samples)} synthetic resumes are included for demos.")
if samples and st.button("Load sample resumes"):
    process([(p.name, p.read_bytes()) for p in samples])

st.divider()
st.subheader("Hundreds of resumes?")
st.write("Use the bulk import in Terminal instead. It shows an ETA, saves after every resume, and can be "
         "stopped and resumed. Each resume takes about 10 seconds on a laptop, once.")
st.code("caffeinate -i ./venv/bin/python bulk_import.py sample_pool_500", language="bash")
st.caption("sample_pool_500 holds 500 synthetic resumes with an answer key. "
           "On Windows or Linux, leave out caffeinate -i.")
