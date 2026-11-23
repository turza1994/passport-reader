"""Medical processing - passport-first candidate intake."""

from __future__ import annotations

import streamlit as st

from core.db import init_db

st.set_page_config(
    page_title="Medical processing",
    layout="wide",
)

init_db()

navigation = st.navigation(
    [
        st.Page(
            "app_pages/candidates.py",
            title="Candidates",
            icon=":material/group:",
            default=True,
        ),
        st.Page(
            "app_pages/new_candidate.py",
            title="New candidate",
            icon=":material/person_add:",
        ),
    ]
)

navigation.run()
