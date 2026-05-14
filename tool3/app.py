"""
Instagram Post Date Finder — Web App
Searches an Excel/CSV database by post link or ID.
If the exact ID is not found, returns the estimated date range
based on the 2 neighboring IDs (lower and upper) in chronological order.
"""

import re
import bisect
from pathlib import Path
from datetime import datetime

import pandas as pd
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

# ─── App setup ───────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]    = 50 * 1024 * 1024   # 50 MB max upload
app.config["MAX_FORM_MEMORY_SIZE"]  = 50 * 1024 * 1024   # keep uploads in RAM, never spool to disk

# ─── In-memory database ───────────────────────────────────────────────────────

DB:           dict  = {}   # shortcode/id-key → row dict
NUMERIC_INDEX: list = []   # [(numeric_id: int, row_dict)] sorted ascending
DB_INFO:      dict  = {}   # metadata

SHORTCODE_RE  = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
NUMERIC_ID_RE = re.compile(r"^\d+(?:_\d+)?$")


# ─── ID helpers ──────────────────────────────────────────────────────────────

def numeric_id_to_shortcode(media_id: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    sc = ""
    while media_id > 0:
        sc = alphabet[media_id % 64] + sc
        media_id //= 64
    return sc


def shortcode_to_numeric_id(shortcode: str) -> int | None:
    """Convert Instagram shortcode (base-64) back to numeric media ID."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    n = 0
    for char in shortcode:
        idx = alphabet.find(char)
        if idx == -1:
            return None
        n = n * 64 + idx
    return n


def extract_shortcode(value: str) -> str | None:
    """Extract shortcode from URL, numeric ID, or raw shortcode."""
    value = str(value).strip()
    if not value or value.lower() in ("nan", "none", ""):
        return None
    m = SHORTCODE_RE.search(value)
    if m:
        return m.group(1)
    if NUMERIC_ID_RE.match(value):
        numeric = int(value.split("_")[0])
        return numeric_id_to_shortcode(numeric)
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return None


def extract_numeric_id(value: str) -> int | None:
    """Extract the leading numeric media ID from a Post ID string."""
    value = str(value).strip()
    if not value or value.lower() in ("nan", "none", ""):
        return None
    # Format: "3818230543192566459_36116147692"  or  just "3818230543192566459"
    part = value.split("_")[0]
    if re.fullmatch(r"\d+", part):
        return int(part)
    return None


def format_date(raw: str) -> str:
    """Return a human-readable date from an ISO string."""
    if not raw or str(raw).lower() in ("nan", "none", ""):
        return str(raw)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(str(raw).strip(), fmt)
            return dt.strftime("%d %B %Y  —  %H:%M")
        except ValueError:
            continue
    return str(raw)


def row_to_dict(row: pd.Series) -> dict:
    d = {}
    for k, v in row.items():
        d[str(k)] = None if (not isinstance(v, str) and pd.isna(v)) else str(v)
    return d


# ─── Load database ───────────────────────────────────────────────────────────

def load_file(path: Path, file_bytes: bytes = None) -> tuple[bool, str]:
    """
    Load a database from a file path, or directly from bytes (no disk write needed).
    """
    global DB, NUMERIC_INDEX, DB_INFO

    try:
        suffix = path.suffix.lower()
        if file_bytes is not None:
            from io import BytesIO
            src = BytesIO(file_bytes)
            df = pd.read_excel(src, dtype=str) if suffix in (".xlsx", ".xls") else pd.read_csv(src, dtype=str)
        else:
            df = pd.read_excel(path, dtype=str) if suffix in (".xlsx", ".xls") else pd.read_csv(path, dtype=str)
    except Exception as e:
        return False, f"Cannot read file: {e}"

    index:   dict = {}
    num_idx: list = []

    col_map  = {c.lower(): c for c in df.columns}
    url_col  = col_map.get("post url")  or col_map.get("link")  or col_map.get("url")
    id_col   = col_map.get("post id")   or col_map.get("id")    or col_map.get("shortcode")
    date_col = (
        col_map.get("published at")
        or col_map.get("publication date")
        or col_map.get("date")
        or col_map.get("published_at")
    )

    for _, row in df.iterrows():
        row_d = row_to_dict(row)

        # Formatted date
        if date_col and date_col in row_d and row_d[date_col]:
            row_d["_date_formatted"] = format_date(row_d[date_col])
            row_d["_date_raw"]       = row_d[date_col]
        else:
            row_d["_date_formatted"] = None
            row_d["_date_raw"]       = None

        keys_to_index = set()
        numeric_id    = None

        # From URL column
        if url_col:
            sc = extract_shortcode(str(row.get(url_col, "")))
            if sc:
                keys_to_index.add(sc.lower())
                row_d["_shortcode"] = sc
                row_d["_url"] = f"https://www.instagram.com/p/{sc}/"

        # From ID column
        if id_col:
            raw_id = str(row.get(id_col, "")).strip()
            if raw_id and raw_id.lower() not in ("nan", "none"):
                sc = extract_shortcode(raw_id)
                if sc:
                    keys_to_index.add(sc.lower())
                    if "_shortcode" not in row_d:
                        row_d["_shortcode"] = sc
                        row_d["_url"] = f"https://www.instagram.com/p/{sc}/"
                keys_to_index.add(raw_id.lower())

                # Numeric index
                numeric_id = extract_numeric_id(raw_id)
                if numeric_id is not None:
                    row_d["_numeric_id"] = str(numeric_id)

        for key in keys_to_index:
            index[key] = row_d

        if numeric_id is not None:
            num_idx.append((numeric_id, row_d))

    # Sort numeric index ascending (oldest → newest)
    num_idx.sort(key=lambda x: x[0])

    DB            = index
    NUMERIC_INDEX = num_idx
    DB_INFO = {
        "filename":  "database",
        "rows":      len(df),
        "indexed":   len(index),
        "columns":   list(df.columns),
        "date_col":  date_col,
        "url_col":   url_col,
        "id_col":    id_col,
        "loaded_at": datetime.now().strftime("%d %B %Y %H:%M"),
    }
    return True, f"Loaded {len(df)} posts ({len(index)} indexed entries)"


# ─── Search helpers ───────────────────────────────────────────────────────────

def search_exact(query: str) -> dict | None:
    """Exact match by shortcode or ID key."""
    query = query.strip()
    sc = extract_shortcode(query)
    if sc and sc.lower() in DB:
        return DB[sc.lower()]
    if query.lower() in DB:
        return DB[query.lower()]
    return None


def search_range(query: str, n: int = 2) -> dict | None:
    """
    Fuzzy search: find the n IDs immediately below and above the given
    numeric ID in the sorted index, and return a date range.
    """
    if not NUMERIC_INDEX:
        return None

    # Try to extract numeric part from the query
    numeric_id = extract_numeric_id(query.strip())
    if numeric_id is None:
        # Maybe it's a raw number string
        clean = re.sub(r"[^0-9]", "", query.strip())
        if clean:
            numeric_id = int(clean)
    if numeric_id is None:
        return None

    keys = [x[0] for x in NUMERIC_INDEX]
    pos  = bisect.bisect_left(keys, numeric_id)

    # n neighbors below (older posts)
    below = NUMERIC_INDEX[max(0, pos - n) : pos]
    # n neighbors above (newer posts)
    above = NUMERIC_INDEX[pos : min(len(NUMERIC_INDEX), pos + n)]

    if not below and not above:
        return None

    def row_summary(nid: int, row: dict) -> dict:
        return {
            "numeric_id":     str(nid),
            "post_id":        row.get("Post ID") or row.get("id") or str(nid),
            "date_formatted": row.get("_date_formatted") or row.get("Published At") or "—",
            "date_raw":       row.get("_date_raw") or "",
            "url":            row.get("_url") or row.get("Post URL") or "",
            "direction":      "before",
        }

    neighbors = []
    for nid, row in below:
        s = row_summary(nid, row)
        s["direction"] = "before"
        neighbors.append(s)
    for nid, row in above:
        s = row_summary(nid, row)
        s["direction"] = "after"
        neighbors.append(s)

    lower_date = below[-1][1].get("_date_formatted") if below else None
    upper_date = above[0][1].get("_date_formatted")  if above else None

    # Build the range label
    if lower_date and upper_date:
        range_label = f"Between  {lower_date}  and  {upper_date}"
    elif lower_date:
        range_label = f"After  {lower_date}"
    elif upper_date:
        range_label = f"Before  {upper_date}"
    else:
        range_label = "Unknown"

    # Generate the Instagram URL for the queried ID
    generated_sc  = numeric_id_to_shortcode(numeric_id)
    generated_url = f"https://www.instagram.com/p/{generated_sc}/"

    return {
        "type":          "range",
        "numeric_id":    str(numeric_id),   # string to preserve precision in JavaScript
        "shortcode":     generated_sc,
        "generated_url": generated_url,
        "range_label":   range_label,
        "lower_date":    lower_date,
        "upper_date":    upper_date,
        "neighbors":     neighbors,
        "total_in_db":   len(NUMERIC_INDEX),
    }


# ─── Auto-load ────────────────────────────────────────────────────────────────

LAST_DB_FILE = BASE_DIR / ".last_database"   # remembers which file was last used

def save_last_db(path: Path):
    """Persist the path of the last successfully loaded database."""
    try:
        LAST_DB_FILE.write_text(str(path), encoding="utf-8")
    except Exception:
        pass

def auto_load():
    """Load database on startup — prefer the last file the user uploaded."""
    # 1. Try the remembered file first
    if LAST_DB_FILE.exists():
        remembered = Path(LAST_DB_FILE.read_text(encoding="utf-8").strip())
        if remembered.exists():
            ok, msg = load_file(remembered)
            if ok:
                print(f"[auto-load] {msg} from {remembered.name} (remembered)")
                return

    # 2. Fall back to first file found in the folder
    candidates = (
        sorted(BASE_DIR.glob("*.csv"))
        + sorted(BASE_DIR.glob("*.xlsx"))
        + sorted(UPLOAD_DIR.glob("*.csv"))
        + sorted(UPLOAD_DIR.glob("*.xlsx"))
    )
    for f in candidates:
        ok, msg = load_file(f)
        if ok:
            save_last_db(f)
            print(f"[auto-load] {msg} from {f.name}")
            return
    print("[auto-load] No database file found.")

auto_load()

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    return jsonify(DB_INFO)


@app.route("/api/search", methods=["POST"])
def api_search():
    data  = request.get_json(silent=True) or {}
    query = str(data.get("query", "")).strip()

    if not query:
        return jsonify({"ok": False, "error": "Empty query"})
    if not DB:
        return jsonify({"ok": False, "error": "No database loaded. Please upload a file."})

    # 1. Try exact match
    result = search_exact(query)
    if result:
        return jsonify({"ok": True, "match": "exact", "data": result})

    # 2. If the query is an Instagram URL, convert the shortcode to a numeric ID
    #    and use that for the range search (avoids the "wrong digit count" error).
    url_match = SHORTCODE_RE.search(query)
    if url_match:
        shortcode  = url_match.group(1)
        numeric_id = shortcode_to_numeric_id(shortcode)
        if numeric_id is not None:
            range_result = search_range(str(numeric_id), n=1)
            if range_result:
                return jsonify({"ok": True, "match": "range", "data": range_result})
        return jsonify({"ok": False, "error": "Post not found and no neighboring IDs could be estimated."})

    # 3. Try range search (fuzzy by numeric ID)
    # Validate the numeric ID looks like a real Instagram ID (19 digits)
    numeric_part = re.sub(r"[^0-9]", "", query.split("_")[0])
    if numeric_part and len(numeric_part) != 19:
        return jsonify({
            "ok":    False,
            "error": f"Invalid Post ID — {len(numeric_part)} digits entered, Instagram Post IDs must be exactly 19 digits.",
            "hint":  "Please check the ID and make sure no digit is missing or extra."
        })

    range_result = search_range(query, n=1)
    if range_result:
        return jsonify({"ok": True, "match": "range", "data": range_result})

    return jsonify({"ok": False, "error": "Post not found and no neighboring IDs could be estimated."})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    try:
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file provided"})

        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "No filename received"})

        suffix = Path(f.filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            return jsonify({"ok": False, "error": f"Unsupported format '{suffix}'. Use .xlsx, .xls or .csv"})

        # Read into memory first — no disk space needed for the initial load
        file_bytes = f.read()

        # Use a single fixed filename (overwrite old) to avoid filling the disk
        dest = UPLOAD_DIR / f"active_database{suffix}"

        # Delete any old database files in uploads/ to free space
        for old in UPLOAD_DIR.iterdir():
            if old.is_file() and old != dest:
                try:
                    old.unlink()
                except Exception:
                    pass

        # Load directly from memory
        ok, msg = load_file(Path(f"active_database{suffix}"), file_bytes=file_bytes)
        if not ok:
            return jsonify({"ok": False, "error": msg})

        # Save one copy to disk for persistence across restarts
        try:
            dest.write_bytes(file_bytes)
            save_last_db(dest)
        except OSError:
            # Disk still full — loaded in memory for this session, warn user
            return jsonify({
                "ok":      True,
                "message": msg + " (loaded for this session only — disk full, cannot save permanently)",
                "info":    DB_INFO
            })

        return jsonify({"ok": True, "message": msg, "info": DB_INFO})

    except Exception as e:
        import traceback
        traceback.print_exc()          # prints full error in the terminal
        return jsonify({"ok": False, "error": f"Server error: {e}"})


if __name__ == "__main__":
    import os, socket
    port = int(os.environ.get("PORT", 5001))
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "unknown"

    print("\n  ============================================")
    print("   Instagram Date Finder is running!")
    print("  ============================================")
    print(f"  This computer  :  http://localhost:{port}")
    print(f"  Other devices  :  http://{local_ip}:{port}")
    print("  (other devices must be on the same WiFi)")
    print("  ============================================\n")
    app.run(host="0.0.0.0", debug=False, port=port)
