from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlparse

import instaloader
from instaloader import BadCredentialsException, LoginException, TwoFactorAuthRequiredException
from instaloader.__main__ import import_session
from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

JOBS: dict[str, dict] = {}
SESSION_DIR = Path("sessions")
PENDING_2FA: dict[str, instaloader.Instaloader] = {}


def clean_username(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        return parts[0].lstrip("@") if parts else ""
    return value.lstrip("@").split("/")[0].strip()


def parse_words(value: str) -> list[str]:
    raw = [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    if len(raw) == 1 and " " in raw[0]:
        raw = [part.strip() for part in raw[0].split() if part.strip()]
    seen = set()
    words = []
    for word in raw:
        key = word.casefold()
        if key not in seen:
            seen.add(key)
            words.append(word)
    return words


def compact_number(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def local_session_file(username: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", username.strip())
    return SESSION_DIR / f"{safe}.session"


def create_loader(
    session_username: str = "",
    session_file: str = "",
    browser_cookies: str = "",
    cookie_file: str = "",
) -> instaloader.Instaloader:
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
    if browser_cookies:
        import_session(browser_cookies.lower(), loader, cookie_file)
    elif cookie_file:
        load_cookies_txt(loader, cookie_file)
    elif session_username and session_file:
        loader.load_session_from_file(session_username, filename=session_file)
    elif session_username:
        local_file = local_session_file(session_username)
        if local_file.exists():
            loader.load_session_from_file(session_username, filename=str(local_file))
        else:
            loader.load_session_from_file(session_username)
    return loader


def load_cookies_txt(loader: instaloader.Instaloader, cookie_file: str) -> None:
    cookie_jar = MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    cookies = {cookie.name: cookie.value for cookie in cookie_jar if "instagram" in cookie.domain}
    if not cookies:
        raise ValueError("No Instagram cookies found in the cookies.txt file.")
    loader.context.update_cookies(cookies)
    username = loader.test_login()
    if not username:
        raise ValueError("The cookies.txt file does not contain a valid logged-in Instagram session.")
    loader.context.username = username


def profile_payload(profile: instaloader.Profile) -> dict:
    return {
        "username": profile.username,
        "full_name": profile.full_name or "",
        "biography": profile.biography or "",
        "external_url": profile.external_url or "",
        "followers": compact_number(profile.followers),
        "followees": compact_number(profile.followees),
        "media_count": compact_number(profile.mediacount),
        "is_private": bool(profile.is_private),
        "is_verified": bool(profile.is_verified),
        "profile_pic_url": profile.profile_pic_url or "",
    }


def get_profile(
    username: str,
    session_username: str = "",
    session_file: str = "",
    browser_cookies: str = "",
    cookie_file: str = "",
) -> instaloader.Profile:
    loader = create_loader(session_username, session_file, browser_cookies, cookie_file)
    return instaloader.Profile.from_username(loader.context, username)


def post_payload(post: instaloader.Post, matched_words: list[str]) -> dict:
    caption = post.caption or ""
    date_utc = post.date_utc.replace(tzinfo=timezone.utc)
    return {
        "shortcode": post.shortcode,
        "url": f"https://www.instagram.com/p/{post.shortcode}/",
        "caption": caption,
        "date": date_utc.strftime("%Y-%m-%d"),
        "likes": compact_number(post.likes),
        "comments": compact_number(post.comments),
        "typename": post.typename or "",
        "is_video": bool(post.is_video),
        "video_view_count": compact_number(getattr(post, "video_view_count", 0)),
        "thumbnail_url": post.url or "",
        "matched_words": matched_words,
    }


def caption_matches(caption: str, words: list[str], mode: str) -> list[str]:
    lowered = caption.casefold()
    matches = [word for word in words if word.casefold() in lowered]
    if mode == "all" and len(matches) != len(words):
        return []
    return matches


def format_error(exc: Exception) -> str:
    text = str(exc)
    low = text.casefold()
    if "login" in low or "403" in low or "checkpoint" in low:
        return "Instagram blocked anonymous lookup. Open Optional Instaloader session and use browser cookies from a browser where Instagram is logged in, or use a valid session file."
    if "two-factor" in low or "2fa" in low:
        return "Instagram asks for two-factor login. Log in with Instaloader in a terminal once, then reuse the saved session file."
    if "bad credentials" in low or "incorrect" in low:
        return "Instagram login failed. Check the login username and password."
    if "cookie decryption" in low or "unable to get key" in low:
        return "Could not decrypt browser cookies. Export Instagram cookies to a cookies.txt file, then leave browser set to None and put that file path in the cookie file field."
    if "cookies.txt" in low:
        return text
    if "private" in low:
        return "This Instagram profile is private or not accessible with the current session."
    if "not found" in low or "404" in low or "does not exist" in low:
        return "Instagram profile not found or blocked from public lookup. If it opens in your browser, use browser cookies or a valid Instaloader session."
    return f"Could not search this Instagram profile. Details: {text}"


def format_login_error(exc: Exception) -> str:
    text = str(exc)
    low = text.casefold()
    if isinstance(exc, BadCredentialsException) or "bad credentials" in low or "incorrect" in low:
        return "Instagram login failed. Check the username and password."
    if isinstance(exc, TwoFactorAuthRequiredException) or "two-factor" in low or "2fa" in low:
        return "Instagram asks for a two-factor code."
    if "checkpoint" in low:
        return "Instagram requires a checkpoint verification. Open Instagram in the browser, finish the security check, then try again."
    if "403" in low or "blocked" in low or "please wait" in low:
        return "Instagram blocked this login request. Try again later, or log in with Instaloader in a terminal once and reuse the saved session."
    if isinstance(exc, LoginException):
        return f"Instagram login failed. Details: {text}"
    return f"Could not log in. Details: {text}"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or request.form
    login_username = payload.get("login_username", "").strip()
    login_password = payload.get("login_password", "")

    if not login_username or not login_password:
        return jsonify({"error": "Enter your Instagram login username and password."}), 400

    try:
        SESSION_DIR.mkdir(exist_ok=True)
        session_file = local_session_file(login_username)
        loader = create_loader()
        try:
            loader.login(login_username, login_password)
        except TwoFactorAuthRequiredException:
            PENDING_2FA[login_username] = loader
            return jsonify(
                {
                    "requires_2fa": True,
                    "message": "Enter the two-factor code from Instagram.",
                    "session_username": login_username,
                }
            )
        loader.save_session_to_file(str(session_file))
        return jsonify(
            {
                "message": "Login saved. You can now load profiles from this computer.",
                "session_username": login_username,
                "session_file": str(session_file.resolve()),
            }
        )
    except Exception as exc:
        return jsonify({"error": format_login_error(exc)}), 502


@app.route("/login-2fa", methods=["POST"])
def login_2fa():
    payload = request.get_json(silent=True) or request.form
    login_username = payload.get("login_username", "").strip()
    code = payload.get("two_factor_code", "").strip().replace(" ", "")

    if not login_username or not code:
        return jsonify({"error": "Enter the Instagram username and two-factor code."}), 400

    loader = PENDING_2FA.get(login_username)
    if not loader:
        return jsonify({"error": "No two-factor login is waiting. Enter password and log in again."}), 400

    try:
        SESSION_DIR.mkdir(exist_ok=True)
        session_file = local_session_file(login_username)
        loader.two_factor_login(code)
        loader.save_session_to_file(str(session_file))
        PENDING_2FA.pop(login_username, None)
        return jsonify(
            {
                "message": "Login saved. You can now load profiles from this computer.",
                "session_username": login_username,
                "session_file": str(session_file.resolve()),
            }
        )
    except Exception as exc:
        return jsonify({"error": format_login_error(exc)}), 502


@app.route("/profile", methods=["POST"])
def profile():
    payload = request.get_json(silent=True) or request.form
    username = clean_username(payload.get("username", ""))
    session_username = payload.get("session_username", "").strip()
    session_file = payload.get("session_file", "").strip()
    browser_cookies = payload.get("browser_cookies", "").strip()
    cookie_file = payload.get("cookie_file", "").strip()

    if not username:
        return jsonify({"error": "Enter an Instagram username or profile URL."}), 400
    if session_file and not Path(session_file).exists():
        return jsonify({"error": "The session file path does not exist on this machine."}), 400
    if cookie_file and not Path(cookie_file).exists():
        return jsonify({"error": "The browser cookie file path does not exist on this machine."}), 400

    try:
        profile_data = get_profile(username, session_username, session_file, browser_cookies, cookie_file)
        return jsonify({"profile": profile_payload(profile_data)})
    except Exception as exc:
        return jsonify({"error": format_error(exc)}), 502


@app.route("/search", methods=["POST"])
def search():
    payload = request.get_json(silent=True) or request.form
    username = clean_username(payload.get("username", ""))
    words = parse_words(payload.get("words", ""))
    mode = payload.get("mode", "any")
    max_posts = compact_number(payload.get("max_posts"))
    session_username = payload.get("session_username", "").strip()
    session_file = payload.get("session_file", "").strip()
    browser_cookies = payload.get("browser_cookies", "").strip()
    cookie_file = payload.get("cookie_file", "").strip()

    if not username:
        return jsonify({"error": "Enter an Instagram username or profile URL."}), 400
    if not words:
        return jsonify({"error": "Enter one or more words to find in captions."}), 400
    if session_file and not Path(session_file).exists():
        return jsonify({"error": "The session file path does not exist on this machine."}), 400
    if cookie_file and not Path(cookie_file).exists():
        return jsonify({"error": "The browser cookie file path does not exist on this machine."}), 400

    try:
        profile = get_profile(username, session_username, session_file, browser_cookies, cookie_file)

        scanned = 0
        matches: list[dict] = []
        for post in profile.get_posts():
            scanned += 1
            caption = post.caption or ""
            matched_words = caption_matches(caption, words, mode)
            if matched_words:
                matches.append(post_payload(post, matched_words))
            if max_posts and scanned >= max_posts:
                break

        job_id = uuid.uuid4().hex
        result = {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "query": {
                "username": username,
                "words": words,
                "mode": mode,
                "max_posts": max_posts,
            },
            "profile": profile_payload(profile),
            "scanned": scanned,
            "matches": matches,
        }
        JOBS[job_id] = result
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": format_error(exc)}), 502


@app.route("/export/<job_id>.<fmt>")
def export_job(job_id: str, fmt: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Export expired. Run a new search."}), 404

    username = job["profile"]["username"]
    if fmt == "json":
        return Response(
            json.dumps(job, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename=instagram-{username}-words.json"},
        )
    if fmt == "csv":
        output = io.StringIO()
        fields = ["shortcode", "url", "date", "likes", "comments", "is_video", "matched_words", "caption"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for item in job["matches"]:
            writer.writerow({field: ", ".join(item[field]) if field == "matched_words" else item.get(field, "") for field in fields})
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=instagram-{username}-words.csv"},
        )
    return jsonify({"error": "Unsupported export format."}), 400


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
