#!/usr/bin/env python3
"""Isolated diagnostic for the Milan-listed WisdomTree 3OIS ETN.
This script does not update oil_data.json and does not create trading signals.
"""
import json
import math
import traceback
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

TARGET = "3OIS.MI"
CONTROL = "3OIS.L"  # diagnostic control only; never used as a replacement
PERIOD = "2y"
OHLC = ["Open", "High", "Low", "Close"]


def normalize(df):
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        # yfinance may return either (Price, Ticker) or (Ticker, Price).
        if TARGET in out.columns.get_level_values(-1):
            out.columns = out.columns.get_level_values(0)
        elif TARGET in out.columns.get_level_values(0):
            out.columns = out.columns.get_level_values(1)
        else:
            out.columns = out.columns.get_level_values(0)
    out = out.loc[:, ~out.columns.duplicated()]
    return out


def inspect_frame(label, df):
    df = normalize(df)
    missing = [c for c in OHLC if c not in df.columns]
    valid = df.dropna(subset=[c for c in OHLC if c in df.columns]) if not missing else pd.DataFrame()
    result = {
        "method": label,
        "rows_returned": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "missing_ohlc_columns": missing,
        "valid_ohlc_rows": int(len(valid)),
        "first_date": str(df.index.min()) if len(df) else None,
        "last_date": str(df.index.max()) if len(df) else None,
        "last_5_rows": [],
    }
    if len(valid):
        tail = valid.tail(5).reset_index()
        for _, row in tail.iterrows():
            item = {}
            for col, value in row.items():
                if hasattr(value, "isoformat"):
                    item[str(col)] = value.isoformat()
                elif pd.isna(value):
                    item[str(col)] = None
                elif isinstance(value, (int, float)):
                    item[str(col)] = float(value)
                else:
                    item[str(col)] = str(value)
            result["last_5_rows"].append(item)
    return result, valid


def run_download():
    try:
        df = yf.download(
            TARGET, period=PERIOD, interval="1d",
            auto_adjust=False, actions=True, repair=True,
            group_by="column", threads=False, progress=False,
            timeout=45, multi_level_index=False,
        )
        report, valid = inspect_frame("yf.download 3OIS.MI 2y", df)
        if len(valid):
            valid.to_csv("diagnostic_3ois_mi.csv", index=True)
        return report
    except Exception as exc:
        return {"method": "yf.download 3OIS.MI 2y", "error": repr(exc), "traceback": traceback.format_exc()}


def run_history():
    try:
        df = yf.Ticker(TARGET).history(
            period=PERIOD, interval="1d",
            auto_adjust=False, actions=True, repair=True,
            timeout=45, raise_errors=True,
        )
        report, valid = inspect_frame("Ticker.history 3OIS.MI 2y", df)
        if len(valid) and not __import__("os").path.exists("diagnostic_3ois_mi.csv"):
            valid.to_csv("diagnostic_3ois_mi.csv", index=True)
        return report
    except Exception as exc:
        return {"method": "Ticker.history 3OIS.MI 2y", "error": repr(exc), "traceback": traceback.format_exc()}


def run_control():
    try:
        df = yf.download(
            CONTROL, period="2y", interval="1d",
            auto_adjust=False, actions=True, repair=True,
            group_by="column", threads=False, progress=False,
            timeout=45, multi_level_index=False,
        )
        report, _ = inspect_frame("control only: yf.download 3OIS.L 2y", df)
        return report
    except Exception as exc:
        return {"method": "control only: yf.download 3OIS.L 2y", "error": repr(exc)}


def main():
    report = {
        "instrument": TARGET,
        "description": "WisdomTree WTI Crude Oil 3x Daily Short ETN — Milan, EUR",
        "purpose": "isolated data-source diagnostic only; no signals, no orders, no oil_data.json changes",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "yfinance_version": getattr(yf, "__version__", "unknown"),
        "period": PERIOD,
        "tests": [run_download(), run_history(), run_control()],
    }
    with open("diagnostic_3ois.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    target_ok = any(
        t.get("valid_ohlc_rows", 0) >= 60
        for t in report["tests"]
        if t.get("method", "").startswith(("yf.download 3OIS.MI", "Ticker.history 3OIS.MI"))
    )
    print("3OIS_MI_HISTORY_OK=" + ("YES" if target_ok else "NO"))


if __name__ == "__main__":
    main()
