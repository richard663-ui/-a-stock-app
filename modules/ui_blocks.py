# -*- coding: utf-8 -*-
from typing import Any, Dict

import numpy as np
import pandas as pd
import streamlit as st

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from modules.qishi import fund_color, qishi_color


def plot_qishi(qishi: Dict[str, Any], title: str = ""):
    df = qishi.get("df", pd.DataFrame())
    if df is None or df.empty:
        st.warning("K线不足，无法画AI起势图。")
        return
    data = df.tail(100).copy().reset_index(drop=True)
    x = np.arange(len(data))

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3.2, 1.2, 1.2]})
    ax = axes[0]
    for i, row in data.iterrows():
        color = "red" if row["close"] >= row["open"] else "green"
        ax.plot([i, i], [row["low"], row["high"]], color=color, linewidth=1)
        lower = min(row["open"], row["close"])
        height = abs(row["close"] - row["open"])
        ax.add_patch(Rectangle((i - 0.3, lower), 0.6, height if height > 0 else 0.01, color=color, alpha=0.8))
    for ma in ["MA5", "MA10", "MA20", "MA60"]:
        ax.plot(x, data[ma], linewidth=1, label=ma)
    ax.set_title(f"Price + MA  {title}")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.2)

    ax2 = axes[1]
    colors = [qishi_color(s) for s in data["score"]]
    ax2.bar(x, data["score"], color=colors, width=0.8)
    ax2.axhline(40, color="gray", linestyle="--", linewidth=0.6)
    ax2.axhline(60, color="orange", linestyle="--", linewidth=0.6)
    ax2.axhline(75, color="red", linestyle="--", linewidth=0.6)
    ax2.set_ylim(0, 100)
    ax2.set_title("AI Momentum Tracking")
    ax2.grid(alpha=0.2)

    ax3 = axes[2]
    fcolors = [fund_color(s, l) for s, l in zip(data["fund_score"], data["fund_label"])]
    ax3.bar(x, data["fund_score"], color=fcolors, width=0.8)
    ax3.axhline(50, color="gray", linestyle="--", linewidth=0.6)
    ax3.set_ylim(0, 100)
    ax3.set_title("Volume/Fund Proxy")
    ax3.grid(alpha=0.2)

    ticks = np.linspace(0, len(data) - 1, min(8, len(data))).astype(int)
    axes[2].set_xticks(ticks)
    axes[2].set_xticklabels(data.loc[ticks, "date"], rotation=30, ha="right")
    plt.tight_layout()
    st.pyplot(fig)

