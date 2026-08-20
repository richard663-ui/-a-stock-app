# -*- coding: utf-8 -*-
"""Simple password gate for the private Streamlit dashboard.

The password must come from Streamlit secrets or the APP_PASSWORD environment
variable. It is never hardcoded in the repository.
"""
from __future__ import annotations

import hmac
import os

import streamlit as st


def _expected_password() -> str:
    try:
        value = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        value = ""
    return str(value or os.environ.get("APP_PASSWORD", "")).strip()


def require_password() -> None:
    """Stop the page until the correct password is entered.

    Successful login continues in the same script run instead of forcing a
    second full-app rerun. This is friendlier to mobile Safari/WebKit sessions.
    """
    if st.session_state.get("authenticated") is True:
        return

    expected = _expected_password()
    st.title("A股盯盘｜私人访问")
    st.caption("该页面仅供授权用户使用。")

    if not expected:
        st.error("管理员尚未在 Streamlit Secrets 中配置 APP_PASSWORD。")
        st.stop()

    with st.form("password_form", clear_on_submit=False):
        entered = st.text_input("访问密码", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("进入系统", use_container_width=True)

    if submitted:
        if hmac.compare_digest(str(entered), expected):
            st.session_state["authenticated"] = True
            return
        st.error("密码错误。")

    st.stop()


def logout_button() -> None:
    if st.sidebar.button("退出登录", use_container_width=True):
        st.session_state.pop("authenticated", None)
        st.rerun()
