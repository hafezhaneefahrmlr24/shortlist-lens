import streamlit as st

import core
import ui

st.set_page_config(page_title="Ask the Pool", page_icon="💬", layout="wide")
model = ui.model_picker()

st.title("Ask the talent pool")
st.write("Ask in plain English, for example *BFSI candidates with SQL* or *editors with 5+ years*. "
         "The app searches every resume first (understanding common HR and data synonyms) and sends only "
         "the best matches to the model, so every answer stays grounded in real candidates and fits in the "
         "model's context window.")

pool = core.load_pool()
if not pool:
    st.info("The talent pool is empty. Add resumes first.")
    st.stop()

k = st.sidebar.slider("Candidates sent to the model", 5, 25, 12)
if st.sidebar.button("Clear chat"):
    st.session_state.chat = []

if "chat" not in st.session_state:
    st.session_state.chat = []

for message in st.session_state.chat:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("shortlist"):
            with st.expander(f"Based on {len(message['shortlist'])} of {message['total']} candidates"):
                st.write(", ".join(message["shortlist"]))

question = st.chat_input("Who has SQL and BFSI experience?")
if question:
    st.session_state.chat.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    names: list[str] = []
    with st.chat_message("assistant"):
        if model is None:
            answer = "Start Ollama and pick a model in the sidebar first."
        else:
            with st.spinner("Searching the pool and asking the model..."):
                try:
                    answer, shortlist = core.answer_question(question, pool, model, k)
                    names = [r["candidateName"] for r in shortlist]
                except core.OllamaError as e:
                    answer = f"Couldn't get an answer: {e}"
        st.markdown(answer)
        if names:
            with st.expander(f"Based on {len(names)} of {len(pool)} candidates"):
                st.write(", ".join(names))
    st.session_state.chat.append({"role": "assistant", "content": answer,
                                  "shortlist": names, "total": len(pool)})
