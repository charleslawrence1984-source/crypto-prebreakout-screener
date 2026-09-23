from __future__ import annotations

from typing import Iterable

import pandas as pd


def prepare_styler_frame(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """
    Return a Styler-safe display frame.

    Pandas Styler.map/apply require unique columns and a unique index.
    Preserve requested display order while removing duplicate column names,
    ignore requested columns that are absent, and reset the row index.
    """
    if frame is None:
        return pd.DataFrame()
    visible = list(dict.fromkeys(
        column for column in columns
        if column in frame.columns
    ))
    return frame.loc[:, visible].copy().reset_index(drop=True)
