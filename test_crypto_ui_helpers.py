import pandas as pd

from crypto_ui_helpers import prepare_styler_frame


def test_prepare_styler_frame_deduplicates_columns_and_index():
    frame = pd.DataFrame(
        {
            "Status": ["BUY", "WATCH"],
            "Stretch target": [1.2, 1.4],
            "Score": [82, 77],
        },
        index=[5, 5],
    )

    display = prepare_styler_frame(
        frame,
        ["Status", "Stretch target", "Stretch target", "Missing", "Score"],
    )

    assert list(display.columns) == ["Status", "Stretch target", "Score"]
    assert display.columns.is_unique
    assert display.index.is_unique
    assert list(display.index) == [0, 1]

    # Force Styler to compute, matching the failure path seen in Streamlit.
    styler = display.style
    styler = styler.map(lambda _: "", subset=["Status"])
    styler = styler.map(lambda _: "", subset=["Score"])
    styler._compute()
