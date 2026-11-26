"""Shared sidebar: pick a local model from the ones installed in Ollama."""
import re

import streamlit as st

import core


def model_picker() -> str | None:
    st.sidebar.header("Local model")
    try:
        models = core.list_models()
    except core.OllamaError as e:
        st.sidebar.error(str(e))
        return None
    if not models:
        st.sidebar.warning("Ollama is running but has no models. In Terminal, run: `ollama pull qwen3:4b`")
        return None
    current = st.session_state.get("model") or core.pick_default_model(models)
    index = models.index(current) if current in models else 0
    model = st.sidebar.selectbox(
        "Model", models, index=index,
        help="Any model installed in Ollama. Everything runs on this computer.",
    )
    st.session_state["model"] = model
    st.sidebar.caption("Runs locally through Ollama. No resume data leaves this computer.")
    return model


def blind_toggle() -> bool:
    """Sidebar switch that hides candidate names across the views."""
    value = st.sidebar.toggle(
        "Blind screening", value=st.session_state.get("blind_mode", False),
        help="Hide candidate names, so screening focuses on skills and evidence.",
    )
    st.session_state["blind_mode"] = value
    return value


def display_name(record: dict, blind: bool) -> str:
    if not blind:
        return record.get("candidateName", "")
    return f"Candidate {str(record.get('id') or '')[:4].upper() or '?'}"


def redact(text: str, record: dict, blind: bool) -> str:
    """In blind mode, replace the candidate's name inside free text such as summaries."""
    if not blind or not text:
        return text
    name = record.get("candidateName", "")
    for part in sorted({name, *name.split()}, key=len, reverse=True):
        if len(part) > 1:
            text = re.sub(rf"\b{re.escape(part)}\b", display_name(record, True), text)
    return text
