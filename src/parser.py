"""
Parsing and normalization for eSSL-style DailyAttendance exports.

The eSSL fingerprint system (and similar biometric systems) typically export
one row per employee per day, with columns for punch in/out times, computed
work hours, and an attendance status code. Real-world exports vary in exact
header names, so this module maps a range of common variants onto a fixed
internal schema.

Internal (canonical) schema after normalize():
    emp_code       - employee code / id (str)
    emp_name       - employee name (str)
    company        - company/entity name (str, optional)
    department     - department name (str)
    category       - broad employee category, e.g. Staff/Helper/Worker (str, optional)
    subcategory    - finer-grained category, if any (str, optional)
    designation    - job title (str, optional)
    date           - attendance date (datetime64[ns], normalized to midnight)
    shift          - shift code, e.g. "GS" (str, optional)
    time_in        - first punch time (str "HH:MM:SS" or NaT-safe string)
    time_out       - last punch time (str "HH:MM:SS")
    work_hours     - hours worked that day (float)
    ot_hours       - overtime hours that day (float)
    status         - single/short code: P, A, PH, CO, PL, WO, H (str)

Any column not recognized by COLUMN_ALIASES (e.g. "-" placeholder columns
some exports include) is simply dropped.

Column matching is primarily by header name (case/whitespace-insensitive),
so it doesn't care what order the columns are in. As a fallback for files
whose headers don't match anything in COLUMN_ALIASES (or have no usable
headers at all), positions are also mapped against the exact column order
HR confirmed the DailyAttendance export uses:

    1. date
    2. empno         -> emp_code
    3. empname       -> emp_name
    4. company
    5. department
    6. category
    7. subcategory
    8. -  (ignore)
    9. -  (ignore)
    10. shift
    11. intime       -> time_in
    12. outtime      -> time_out
    13. -  (ignore)
    14. -  (ignore)
    15. hours        -> work_hours (e.g. "08:30", a duration, not decimal)
    16. -  (ignore)
    17. -  (ignore)
    18. attendance   -> status ("Present"/"Absent"/"1/2Present"/...)
    19. -  (ignore — punch-in/out log)

See POSITIONAL_COLUMNS below. The real eSSL export has no header row at
all — the first row is already data — so load_file() also has to detect
that and read the CSV with header=None instead of losing row 1 to
pandas's default header=0.
"""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd

# Maps canonical column name -> list of header variants seen in the wild
# (lowercased, whitespace-insensitive match).
COLUMN_ALIASES = {
    "emp_code": ["employee code", "empcode", "emp code", "employee id", "emp id", "code", "empno", "emp no"],
    "emp_name": ["employee name", "empname", "emp name", "name"],
    "company": ["company"],
    "category": ["category"],
    "subcategory": ["subcategory", "sub category", "sub-category", "subcat"],
    "department": ["department", "dept"],
    "designation": ["designation", "title", "role"],
    "date": ["date", "attendance date", "attendancedate", "punch date"],
    "shift": ["shift"],
    "time_in": ["in", "in time", "intime", "punch in", "first in"],
    "time_out": ["out", "out time", "outtime", "punch out", "last out"],
    "work_hours": [
        "work_hours", "work hours", "workhours", "worked hours", "duration", "hours", "total",
    ],
    "ot_hours": [
        "total-ot-hours", "total ot hours", "ot hours", "ot_hours", "overtime",
        "overtime hours",
    ],
    "status": ["attendance", "status", "attendance status", "att status"],
}

# Positional fallback: column 1 = date, column 2 = emp_code, etc., matching
# the exact order HR sends (see module docstring). `None` marks a column to
# ignore. Only used when header-name matching (COLUMN_ALIASES) can't find
# the required columns — see _build_positional_rename_map().
POSITIONAL_COLUMNS = [
    "date", "emp_code", "emp_name", "company", "department", "category", "subcategory",
    None, None, "shift", "time_in", "time_out", None, None, "work_hours",
    None, None, "status",
]

# Word-style status values seen in the wild (the real eSSL export spells
# these out instead of using the short P/A/... codes) -> canonical code.
STATUS_WORD_ALIASES = {
    "present": "P",
    "absent": "A",
    "half day": "HD",
    "halfday": "HD",
    "1/2present": "HD",
    "1/2 present": "HD",
    "paid holiday": "PH",
    "holiday": "H",
    "week off": "WO",
    "weekoff": "WO",
    "comp off": "CO",
    "compoff": "CO",
    "personal leave": "PL",
}

STATUS_LABELS = {
    "P": "Present",
    "A": "Absent",
    "PH": "Paid Holiday",
    "CO": "Comp Off",
    "PL": "Personal Leave",
    "WO": "Week Off",
    "H": "Holiday",
    "HD": "Half Day",
}


def _clean_header(col: str) -> str:
    return str(col).strip().lower()


def _build_rename_map(columns) -> dict:
    """Map raw headers to canonical names. Some real-world exports have more
    than one column that could map to the same canonical name (e.g. both
    "Date" and "Attendance Date"); only the first one encountered (in column
    order) is kept so we never produce duplicate output columns."""
    lookup = {}
    for canon, variants in COLUMN_ALIASES.items():
        for v in variants:
            lookup[v] = canon
    rename = {}
    used_canon = set()
    for col in columns:
        cleaned = _clean_header(col)
        canon = lookup.get(cleaned)
        if canon and canon not in used_canon:
            rename[col] = canon
            used_canon.add(canon)
    return rename


def _build_positional_rename_map(columns) -> dict:
    """Fallback for files whose headers don't match COLUMN_ALIASES: map by
    position using POSITIONAL_COLUMNS (date, empno, empname, company,
    department, category, subcategory, ignore, ignore, shift, intime,
    outtime, hours)."""
    rename = {}
    for i, col in enumerate(columns):
        if i >= len(POSITIONAL_COLUMNS):
            break
        canon = POSITIONAL_COLUMNS[i]
        if canon:
            rename[col] = canon
    return rename


def load_file(uploaded_file) -> pd.DataFrame:
    """Load an uploaded CSV/XLSX/XLSM file (Django UploadedFile, path, or
    file-like object) into a raw DataFrame, trying every sheet for xlsx and
    picking the one that looks like a daily attendance table."""
    name = getattr(uploaded_file, "name", str(uploaded_file))
    is_excel = name.lower().endswith((".xlsx", ".xlsm", ".xls"))

    if is_excel:
        data = uploaded_file.read() if hasattr(uploaded_file, "read") else uploaded_file
        xls = pd.ExcelFile(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
        best_df = None
        best_score = -1
        for sheet in xls.sheet_names:
            try:
                df = xls.parse(sheet)
            except Exception:
                continue
            rename = _build_rename_map(df.columns)
            score = len(set(rename.values()))
            if score > best_score:
                best_score = score
                best_df = df
        if best_df is None:
            raise ValueError("Could not find a recognizable attendance sheet in the workbook.")
        return best_df

    # Some real-world DailyAttendance exports have no header row at all —
    # the file starts directly with data. Reading with the default header=0
    # would silently consume that first data row as column headers. Peek at
    # the first cell: if it parses as a date, there's no header, so re-read
    # with header=None (columns become 0, 1, 2, ... and fall through to
    # POSITIONAL_COLUMNS in normalize()).
    raw = uploaded_file.read() if hasattr(uploaded_file, "read") else uploaded_file
    buf = io.BytesIO(raw) if isinstance(raw, (bytes, bytearray)) else raw
    df = pd.read_csv(buf, header=None)
    first_cell = str(df.iloc[0, 0]).strip()
    if pd.isna(pd.to_datetime(first_cell, errors="coerce", dayfirst=True)):
        df.columns = df.iloc[0].tolist()
        df = df.iloc[1:].reset_index(drop=True)
    return df


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to the canonical schema and coerce types."""
    rename = _build_rename_map(df.columns)
    required = {"emp_code", "emp_name", "date"}
    if required - set(rename.values()):
        # Header names didn't match anything recognized — fall back to the
        # confirmed column order (date, empno, empname, company, department,
        # category, subcategory, -, -, shift, intime, outtime, hours).
        positional_rename = _build_positional_rename_map(df.columns)
        if not (required - set(positional_rename.values())):
            rename = positional_rename

    missing_required = required - set(rename.values())
    if missing_required:
        raise ValueError(
            "Could not find required column(s): "
            f"{', '.join(sorted(missing_required))}. "
            "Expected an eSSL-style export with Employee Code/Name and Date columns "
            "(or the column order date, empno, empname, company, department, "
            "category, subcategory, -, -, shift, intime, outtime, hours)."
        )

    out = df.rename(columns=rename)
    keep = [c for c in COLUMN_ALIASES.keys() if c in out.columns]
    out = out[keep].copy()

    out["emp_code"] = out["emp_code"].astype(str).str.strip()
    out["emp_name"] = out["emp_name"].astype(str).str.strip()
    if "department" in out.columns:
        out["department"] = out["department"].fillna("Unassigned").astype(str).str.strip()
    else:
        out["department"] = "Unassigned"
    for col in ("designation", "company", "category", "subcategory"):
        if col not in out.columns:
            out[col] = ""
        else:
            out[col] = out[col].fillna("").astype(str).str.strip()

    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["date"])

    if "work_hours" in out.columns:
        # work_hours sometimes comes as a decimal (9.21) and sometimes as an
        # "HH:MM" duration string (08:30) — _duration_to_hours handles both.
        out["work_hours"] = _duration_to_hours(out["work_hours"])
    else:
        out["work_hours"] = 0.0

    if "ot_hours" in out.columns:
        out["ot_hours"] = _duration_to_hours(out["ot_hours"])
    else:
        out["ot_hours"] = 0.0

    if "status" in out.columns:
        raw_status = out["status"].astype(str).str.strip()
        word_mapped = raw_status.str.lower().map(STATUS_WORD_ALIASES)
        out["status"] = word_mapped.fillna(raw_status.str.upper())
        out.loc[~out["status"].isin(STATUS_LABELS.keys()), "status"] = pd.NA
    else:
        out["status"] = pd.NA

    # Derive status from work_hours where it's missing: any hours worked -> Present,
    # otherwise Absent. This matches how the eSSL export behaves when a status
    # column isn't present.
    derived_present = out["work_hours"] > 0
    out["status"] = out["status"].fillna(pd.Series("P", index=out.index).where(derived_present, "A"))

    for col in ("time_in", "time_out", "shift"):
        if col not in out.columns:
            out[col] = ""
        else:
            out[col] = out[col].astype(str).replace("nan", "")

    return out.sort_values(["emp_code", "date"]).reset_index(drop=True)


def _duration_to_hours(series: pd.Series) -> pd.Series:
    """Duration columns (work_hours, ot_hours) sometimes come as
    'HH:MM:SS'/'HH:MM' strings and sometimes as decimal hours. Normalize
    both to decimal hours."""

    def convert(v):
        if pd.isna(v):
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s or s.lower() == "nan":
            return 0.0
        if ":" in s:
            parts = s.split(":")
            try:
                parts = [float(p) for p in parts]
            except ValueError:
                return 0.0
            if len(parts) == 3:
                h, m, sec = parts
                return h + m / 60 + sec / 3600
            if len(parts) == 2:
                h, m = parts
                return h + m / 60
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    return series.apply(convert)
