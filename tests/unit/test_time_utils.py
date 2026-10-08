import pandas as pd

from src.common.time_utils import (
    EASTERN_TZ,
    SessionPhase,
    classify_session_phase,
    filter_rth,
    to_eastern,
)


def test_to_eastern_conversion():
    utc_ts = pd.Timestamp("2024-01-02 14:30:00", tz="UTC")
    df = pd.DataFrame({"timestamp": [utc_ts]})
    et_df = to_eastern(df)
    assert str(et_df["timestamp"].dt.tz) == EASTERN_TZ
    assert et_df["timestamp"].iloc[0].hour == 9
    assert et_df["timestamp"].iloc[0].minute == 30


def test_filter_rth_boundaries():
    open_et = pd.Timestamp("2024-01-02 09:30:00", tz=EASTERN_TZ)
    pre_et = pd.Timestamp("2024-01-02 09:29:00", tz=EASTERN_TZ)
    post_et = pd.Timestamp("2024-01-02 16:01:00", tz=EASTERN_TZ)
    close_et = pd.Timestamp("2024-01-02 16:00:00", tz=EASTERN_TZ)

    df = pd.DataFrame({"timestamp": [pre_et, open_et, close_et, post_et]})
    rth_df = filter_rth(df)
    assert len(rth_df) == 2
    assert list(rth_df["timestamp"]) == [open_et, close_et]


def test_classify_session_phase():
    ts_or = pd.Timestamp("2024-01-02 09:35:00", tz=EASTERN_TZ)
    ts_trading = pd.Timestamp("2024-01-02 10:30:00", tz=EASTERN_TZ)
    ts_fe = pd.Timestamp("2024-01-02 15:59:00", tz=EASTERN_TZ)
    ts_pre = pd.Timestamp("2024-01-02 09:15:00", tz=EASTERN_TZ)

    assert classify_session_phase(ts_or) == SessionPhase.OPENING_RANGE
    assert classify_session_phase(ts_trading) == SessionPhase.TRADING
    assert classify_session_phase(ts_fe) == SessionPhase.FORCE_EXIT
    assert classify_session_phase(ts_pre) == SessionPhase.PRE_MARKET
