# -*- coding: utf-8 -*-
"""Process-local pandas compatibility shim for QMT historical replay only.

Pandas 3 may represent pd.date_range at microsecond precision while QMT epoch-ms
conversion yields millisecond precision. merge_asof requires identical datetime
dtypes. This shim normalizes only explicit left_on/right_on datetime keys to ns.
It is imported only by the historical audit/self-test process, never by live QMT.
"""
from __future__ import annotations

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype

_ORIGINAL = pd.merge_asof


def _ns(frame, key):
    if key and key in frame.columns and is_datetime64_any_dtype(frame[key].dtype):
        frame = frame.copy()
        frame[key] = pd.to_datetime(frame[key], errors="coerce").astype("datetime64[ns]")
    return frame


def _merge_asof_ns(left, right, *args, **kwargs):
    left_on = kwargs.get("left_on")
    right_on = kwargs.get("right_on")
    if left_on and right_on:
        left = _ns(left, left_on)
        right = _ns(right, right_on)
    return _ORIGINAL(left, right, *args, **kwargs)


if pd.merge_asof is not _merge_asof_ns:
    pd.merge_asof = _merge_asof_ns
