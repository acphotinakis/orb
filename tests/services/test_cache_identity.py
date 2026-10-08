"""
tests/services/test_cache_identity.py
======================================
P1-T05/T06/T07 (processor level): identity invalidation, refresh,
corruption recovery, and concurrent writes.
"""

import dataclasses
import json
import threading

import pandas as pd

from src.data.processor import DataProcessor
from src.services.artifact_store import (
    dataset_identity,
    fingerprint_dataframe,
    identity_short_hash,
)


def _identity_for(raw_df, config, feed=None):
    return dataset_identity(
        fingerprint_dataframe(raw_df),
        feed=feed or config.data.feed,
        symbol=config.data.symbol,
        timeframe=config.data.timeframe,
        date_min=str(pd.Timestamp(raw_df["timestamp"].min()).date()),
        date_max=str(pd.Timestamp(raw_df["timestamp"].max()).date()),
        or_minutes=config.strategy.opening_range_minutes,
        force_exit_time=config.strategy.force_exit_time,
    )


def _processor(config, tmp_path):
    return DataProcessor(config=config, output_dir=tmp_path / "proc")


def test_or_duration_change_rebuilds(tmp_path, mock_app_config, synthetic_rth_bars):
    """T05: 15-min then 30-min range on the same raw data must not reuse flags."""
    proc = _processor(mock_app_config, tmp_path)
    dest = tmp_path / "proc" / "sessions.parquet"

    out15 = proc.process(
        synthetic_rth_bars,
        output_path=dest,
        identity=_identity_for(synthetic_rth_bars, mock_app_config),
    )
    cfg30 = dataclasses.replace(
        mock_app_config,
        strategy=dataclasses.replace(
            mock_app_config.strategy, opening_range_minutes=30
        ),
    )
    proc30 = _processor(cfg30, tmp_path)
    out30 = proc30.process(
        synthetic_rth_bars,
        output_path=dest,
        identity=_identity_for(synthetic_rth_bars, cfg30),
    )
    assert len(out30[out30["is_opening_range"]]) > len(out15[out15["is_opening_range"]])
    sidecar = json.loads(
        (tmp_path / "proc" / "sessions.parquet.identity.json").read_text()
    )
    assert sidecar["identity"]["opening_range_minutes"] == 30


def test_exit_time_and_feed_change_identity(mock_app_config, synthetic_rth_bars):
    """T05: changed exit time / feed produce different identities (separate keys)."""
    base = _identity_for(synthetic_rth_bars, mock_app_config)
    cfg_exit = dataclasses.replace(
        mock_app_config,
        strategy=dataclasses.replace(
            mock_app_config.strategy, force_exit_time="15:30:00"
        ),
    )
    changed_exit = _identity_for(synthetic_rth_bars, cfg_exit)
    changed_feed = _identity_for(synthetic_rth_bars, mock_app_config, feed="sip")
    assert identity_short_hash(base) != identity_short_hash(changed_exit)
    assert identity_short_hash(base) != identity_short_hash(changed_feed)


def test_revised_raw_data_invalidates(tmp_path, mock_app_config, synthetic_rth_bars):
    """T06: identical config with one revised bar rebuilds (new fingerprint)."""
    proc = _processor(mock_app_config, tmp_path)
    dest = tmp_path / "proc" / "sessions.parquet"
    ident_a = _identity_for(synthetic_rth_bars, mock_app_config)
    out_a = proc.process(synthetic_rth_bars, output_path=dest, identity=ident_a)

    revised = synthetic_rth_bars.copy()
    revised.loc[100, "close"] = 999.0
    ident_b = _identity_for(revised, mock_app_config)
    assert ident_a["source_fingerprint"] != ident_b["source_fingerprint"]
    out_b = proc.process(revised, output_path=dest, identity=ident_b)
    assert float(out_b.loc[out_b["minute_of_day"] == 100, "close"].iloc[0]) == 999.0
    assert len(out_a) == len(out_b)


def test_explicit_refresh_rebuilds(tmp_path, mock_app_config, synthetic_rth_bars):
    """T06: force_refresh reaches the processor even with identical identity."""
    proc = _processor(mock_app_config, tmp_path)
    dest = tmp_path / "proc" / "sessions.parquet"
    ident = _identity_for(synthetic_rth_bars, mock_app_config)
    first = proc.process(synthetic_rth_bars, output_path=dest, identity=ident)
    second = proc.process(
        synthetic_rth_bars, output_path=dest, identity=ident, force_refresh=True
    )
    pd.testing.assert_frame_equal(first, second)
    sidecar = json.loads(
        (tmp_path / "proc" / "sessions.parquet.identity.json").read_text()
    )
    assert sidecar["identity"] == ident


def test_corrupt_cache_and_sidecar_rebuild(
    tmp_path, mock_app_config, synthetic_rth_bars
):
    """T06/T07: corrupt parquet or sidecar never surfaces; it rebuilds."""
    proc = _processor(mock_app_config, tmp_path)
    dest = tmp_path / "proc" / "sessions.parquet"
    ident = _identity_for(synthetic_rth_bars, mock_app_config)
    expected = proc.process(synthetic_rth_bars, output_path=dest, identity=ident)

    dest.write_bytes(b"not a parquet file")
    rebuilt = proc.process(synthetic_rth_bars, output_path=dest, identity=ident)
    pd.testing.assert_frame_equal(expected, rebuilt)

    (tmp_path / "proc" / "sessions.parquet.identity.json").write_text("{broken")
    rebuilt2 = proc.process(synthetic_rth_bars, output_path=dest, identity=ident)
    pd.testing.assert_frame_equal(expected, rebuilt2)


def test_missing_cache_builds(tmp_path, mock_app_config, synthetic_rth_bars):
    proc = _processor(mock_app_config, tmp_path)
    out = proc.process(
        synthetic_rth_bars,
        output_path=tmp_path / "fresh" / "sessions.parquet",
        identity=_identity_for(synthetic_rth_bars, mock_app_config),
    )
    assert len(out) == len(synthetic_rth_bars)


def test_concurrent_writes_same_key_stay_valid(
    tmp_path, mock_app_config, synthetic_rth_bars
):
    """T07: concurrent writers to one key yield a valid old-or-new dataset."""
    proc = _processor(mock_app_config, tmp_path)
    dest = tmp_path / "proc" / "sessions.parquet"
    ident = _identity_for(synthetic_rth_bars, mock_app_config)
    results, errors = [], []

    def _work():
        try:
            results.append(
                proc.process(synthetic_rth_bars, output_path=dest, identity=ident)
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(results) == 8
    for frame in results[1:]:
        pd.testing.assert_frame_equal(results[0], frame)
    reloaded = proc.load_processed(dest)
    pd.testing.assert_frame_equal(results[0], reloaded)
