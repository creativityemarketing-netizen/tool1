"""
Instagram Post Date Finder
==========================
Reads an Excel or CSV file with Instagram post links or Post IDs,
fetches the publication date of each post, and saves results back to Excel.

Also supports: given a post ID/shortcode → returns the full URL.

Usage:
    python instagram_date_finder.py --file posts.xlsx
    python instagram_date_finder.py --file posts.csv --column "Post ID"
    python instagram_date_finder.py --post https://www.instagram.com/p/ABC123/
    python instagram_date_finder.py --post ABC123
    python instagram_date_finder.py --login
"""

import argparse
import re
import sys
import time
import logging
from pathlib import Path

import pandas as pd
import instaloader

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Helpers ─────────────────────────────────────────────────────────────────

INSTAGRAM_BASE = "https://www.instagram.com/p/"

SHORTCODE_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)"
)

# Post ID format from exports: "3818230543192566459_36116147692"
# The shortcode is encoded in the numeric ID but instaloader can work
# with Post.from_shortcode only. We also try direct URL lookup.
NUMERIC_ID_RE = re.compile(r"^\d+_\d+$")


def extract_shortcode(value: str) -> str | None:
    """
    Extract the shortcode from:
      - a full Instagram URL     (https://www.instagram.com/p/ABC123/)
      - a reel URL               (https://www.instagram.com/reel/ABC123/)
      - a raw shortcode          (ABC123)
      - a numeric Post ID        (3818230543192566459_36116147692)
    """
    value = str(value).strip()
    if not value or value.lower() in ("nan", "none", ""):
        return None

    # Full URL → extract shortcode
    m = SHORTCODE_RE.search(value)
    if m:
        return m.group(1)

    # Numeric export ID → convert to shortcode
    if NUMERIC_ID_RE.match(value):
        numeric = int(value.split("_")[0])
        return numeric_id_to_shortcode(numeric)

    # Assume it is already a shortcode
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value

    return None


def numeric_id_to_shortcode(media_id: int) -> str:
    """Convert a numeric Instagram media ID to its Base64 shortcode."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    shortcode = ""
    while media_id > 0:
        shortcode = alphabet[media_id % 64] + shortcode
        media_id //= 64
    return shortcode


def shortcode_to_url(shortcode: str) -> str:
    return f"{INSTAGRAM_BASE}{shortcode}/"


def get_post_date(loader: instaloader.Instaloader, shortcode: str) -> str:
    """
    Fetch the publication date of an Instagram post by its shortcode.
    Returns date as 'YYYY-MM-DD HH:MM:SS' string, or an error message.
    """
    try:
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        return post.date.strftime("%Y-%m-%d %H:%M:%S")
    except instaloader.exceptions.InstaloaderException as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR: {e}"


# ─── Core processing ─────────────────────────────────────────────────────────

def read_file(file_path: Path) -> pd.DataFrame:
    suffix = file_path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(file_path, dtype=str)
    elif suffix == ".csv":
        return pd.read_csv(file_path, dtype=str)
    else:
        log.error("Unsupported file format: %s (use .xlsx, .xls, or .csv)", suffix)
        sys.exit(1)


def detect_column(df: pd.DataFrame) -> str:
    """Auto-detect the best column to use for post identification."""
    preferred = ["Post URL", "Link", "URL", "Post ID", "ID", "Shortcode"]
    for col in preferred:
        if col in df.columns:
            return col
    log.error("Could not auto-detect column. Available: %s", list(df.columns))
    sys.exit(1)


def process_file(
    file_path: str,
    column: str | None,
    output_path: str | None,
    loader: instaloader.Instaloader,
    delay: float = 2.0,
) -> None:
    path = Path(file_path)
    if not path.exists():
        log.error("File not found: %s", file_path)
        sys.exit(1)

    log.info("Reading: %s", path)
    df = read_file(path)

    if column is None:
        column = detect_column(df)
        log.info("Auto-detected column: '%s'", column)

    if column not in df.columns:
        log.error(
            "Column '%s' not found. Available columns: %s",
            column, list(df.columns),
        )
        sys.exit(1)

    log.info("Processing %d rows from column '%s' …", len(df), column)

    dates     = []
    urls      = []
    shortcodes = []

    for i, value in enumerate(df[column], start=1):
        shortcode = extract_shortcode(value)

        if shortcode is None:
            log.warning("Row %d – cannot parse: %r", i, value)
            shortcodes.append("INVALID")
            urls.append("INVALID")
            dates.append("INVALID")
            continue

        url = shortcode_to_url(shortcode)
        log.info("Row %d/%d – %s", i, len(df), url)

        date = get_post_date(loader, shortcode)
        log.info("         → %s", date)

        shortcodes.append(shortcode)
        urls.append(url)
        dates.append(date)

        if i < len(df):
            time.sleep(delay)

    # Only add columns that don't already exist or are empty
    if "Shortcode" not in df.columns:
        df["Shortcode"] = shortcodes
    if "Post URL" not in df.columns:
        df["Post URL"] = urls
    if "Publication Date" not in df.columns:
        df["Publication Date"] = dates
    else:
        # Fill only missing dates
        df["Publication Date"] = [
            d if str(df.get("Publication Date", pd.Series()).iloc[i - 1]).strip() in ("", "nan")
            else df.get("Publication Date", pd.Series()).iloc[i - 1]
            for i, d in enumerate(dates, 1)
        ]

    # Always add a clean date column
    df["Publication Date (fetched)"] = dates

    # Save as Excel regardless of input format
    if output_path:
        out = Path(output_path)
    else:
        out = path.with_name(path.stem + "_with_dates.xlsx")

    df.to_excel(out, index=False)
    log.info("✓ Saved → %s", out)
    print(f"\n  Output file: {out}\n")


# ─── Quick single-post lookup ─────────────────────────────────────────────────

def lookup_single(loader: instaloader.Instaloader, value: str) -> None:
    shortcode = extract_shortcode(value)
    if not shortcode:
        print(f"Cannot parse: {value!r}")
        return

    url  = shortcode_to_url(shortcode)
    date = get_post_date(loader, shortcode)

    print(f"\n  Input     : {value}")
    print(f"  Shortcode : {shortcode}")
    print(f"  URL       : {url}")
    print(f"  Date      : {date}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Instagram Post Date Finder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=r"""
Examples:
  # Process a CSV or Excel file (auto-detects the URL/ID column)
  python instagram_date_finder.py --file posts.csv
  python instagram_date_finder.py --file posts.xlsx

  # Specify which column contains the links or IDs
  python instagram_date_finder.py --file posts.xlsx --column "Post URL"
  python instagram_date_finder.py --file posts.xlsx --column "Post ID"

  # Look up a single post by URL or shortcode/ID
  python instagram_date_finder.py --post https://www.instagram.com/p/ABC123/
  python instagram_date_finder.py --post ABC123
  python instagram_date_finder.py --post 3818230543192566459_36116147692

  # Log in with Instagram (recommended for large files to avoid blocks)
  python instagram_date_finder.py --login
  python instagram_date_finder.py --file posts.csv --username myaccount
""",
    )
    p.add_argument("--file",     help="Path to Excel (.xlsx) or CSV (.csv) file")
    p.add_argument("--column",   default=None,
                   help="Column name with Instagram URLs or IDs (auto-detected if not set)")
    p.add_argument("--output",   help="Output .xlsx file path (default: <input>_with_dates.xlsx)")
    p.add_argument("--post",     help="Single post URL, shortcode, or numeric ID to look up")
    p.add_argument("--username", help="Instagram username (loads saved session)")
    p.add_argument("--login",    action="store_true",
                   help="Log in to Instagram interactively and save session")
    p.add_argument("--delay",    type=float, default=2.0,
                   help="Seconds to wait between requests (default: 2)")
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    # ── Set up instaloader ──────────────────────────────────────────────────
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )

    # Load a previously saved session
    if args.username:
        try:
            loader.load_session_from_file(args.username)
            log.info("Session loaded for @%s", args.username)
        except FileNotFoundError:
            log.warning("No saved session for @%s – running as guest", args.username)

    # Interactive login
    if args.login:
        username = args.username or input("Instagram username: ")
        loader.interactive_login(username)
        loader.save_session_to_file()
        log.info("Session saved for @%s", username)
        if not args.file and not args.post:
            return

    # ── Dispatch ────────────────────────────────────────────────────────────
    if args.post:
        lookup_single(loader, args.post)
    elif args.file:
        process_file(args.file, args.column, args.output, loader, delay=args.delay)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
