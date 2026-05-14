from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlparse

import instaloader
import requests as http_requests
from instaloader import BadCredentialsException, LoginException, TwoFactorAuthRequiredException
from instaloader.__main__ import import_session
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)

JOBS: dict[str, dict] = {}
SESSION_DIR = Path("sessions")
PENDING_2FA: dict[str, instaloader.Instaloader] = {}
LAST_COOKIE_SOURCE: dict[str, str] = {"browser_cookies": "", "cookie_file": ""}
DEFAULT_COOKIE_FILE = r"C:\Users\HP\Downloads\www.instagram.com_cookies.txt"
DEFAULT_SCAN_LIMIT = 120
DEFAULT_MATCH_LIMIT = 30
DEFAULT_SEARCH_SECONDS = 240


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


def request_limit(value: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except (TypeError, ValueError):
        return default


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def local_session_file(username: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", username.strip())
    return SESSION_DIR / f"{safe}.session"


def create_loader(
    session_username: str = "",
    session_file: str = "",
    browser_cookies: str = "",
    cookie_file: str = "",
) -> instaloader.Instaloader:
    if not (session_username or session_file or browser_cookies or cookie_file) and Path(DEFAULT_COOKIE_FILE).exists():
        cookie_file = DEFAULT_COOKIE_FILE

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


def instagram_cookies_from_file(cookie_file: str) -> dict[str, str]:
    cookie_jar = MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    return {cookie.name: cookie.value for cookie in cookie_jar if "instagram" in cookie.domain}


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
    if not (session_username or session_file or browser_cookies or cookie_file) and Path(DEFAULT_COOKIE_FILE).exists():
        cookie_file = DEFAULT_COOKIE_FILE
    loader = create_loader(session_username, session_file, browser_cookies, cookie_file)
    return instaloader.Profile.from_username(loader.context, username)


def iter_profile_content(profile: instaloader.Profile, per_source_limit: int = 0, content_area: str = "both"):
    seen = set()
    if content_area == "posts":
        sources = (("post", profile.get_posts),)
    elif content_area == "reels":
        sources = (("reel", profile.get_reels),)
    else:
        sources = (("post", profile.get_posts), ("reel", profile.get_reels))
    for source, getter in sources:
        source_scanned = 0
        for post in getter():
            source_scanned += 1
            shortcode = getattr(post, "shortcode", "")
            if shortcode in seen:
                continue
            seen.add(shortcode)
            yield source, post
            if per_source_limit and source_scanned >= per_source_limit:
                break


def post_payload(post: instaloader.Post, matched_words: list[str], source: str = "post") -> dict:
    caption = post.caption or ""
    date_utc = post.date_utc.replace(tzinfo=timezone.utc)
    path = "reel" if source == "reel" else "p"
    return {
        "shortcode": post.shortcode,
        "url": f"https://www.instagram.com/{path}/{post.shortcode}/",
        "source": source,
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
        if not (session_username or session_file or browser_cookies or cookie_file) and Path(DEFAULT_COOKIE_FILE).exists():
            cookie_file = DEFAULT_COOKIE_FILE
        LAST_COOKIE_SOURCE.update({"browser_cookies": browser_cookies, "cookie_file": cookie_file})
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
    content_area = payload.get("content_area", "both")
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
        if not (session_username or session_file or browser_cookies or cookie_file) and Path(DEFAULT_COOKIE_FILE).exists():
            cookie_file = DEFAULT_COOKIE_FILE
        LAST_COOKIE_SOURCE.update({"browser_cookies": browser_cookies, "cookie_file": cookie_file})
        profile = get_profile(username, session_username, session_file, browser_cookies, cookie_file)

        scanned = 0
        matches: list[dict] = []
        per_source_limit = max(1, max_posts // 2) if max_posts and content_area == "both" else max_posts
        for source, post in iter_profile_content(profile, per_source_limit, content_area):
            scanned += 1
            caption = post.caption or ""
            matched_words = caption_matches(caption, words, mode)
            if matched_words:
                matches.append(post_payload(post, matched_words, source))
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


def stream_search_posts(
    username: str,
    words: list[str],
    mode: str,
    session_username: str = "",
    session_file: str = "",
    browser_cookies: str = "",
    cookie_file: str = "",
    scan_limit: int = DEFAULT_SCAN_LIMIT,
    match_limit: int = DEFAULT_MATCH_LIMIT,
    time_limit: int = DEFAULT_SEARCH_SECONDS,
    content_area: str = "both",
):
    job_id = uuid.uuid4().hex
    matches: list[dict] = []

    try:
        profile = get_profile(username, session_username, session_file, browser_cookies, cookie_file)
        result = {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "query": {"username": username, "words": words, "mode": mode, "max_posts": 0},
            "profile": profile_payload(profile),
            "scanned": 0,
            "matches": matches,
        }
        JOBS[job_id] = result
        yield sse("start", {"job_id": job_id, "profile": result["profile"]})

        scanned = 0
        current_source = ""
        started = time.monotonic()
        stop_reason = "finished"
        per_source_limit = max(1, scan_limit // 2) if scan_limit and content_area == "both" else scan_limit
        for source, post in iter_profile_content(profile, per_source_limit, content_area):
            scanned += 1
            current_source = source
            result["scanned"] = scanned
            caption = post.caption or ""
            matched_words = caption_matches(caption, words, mode)
            if matched_words:
                item = post_payload(post, matched_words, source)
                matches.append(item)
                yield sse("item", {"job_id": job_id, "item": item, "matches": len(matches), "scanned": scanned, "source": source})
            elif scanned == 1 or scanned % 5 == 0:
                yield sse("progress", {"job_id": job_id, "matches": len(matches), "scanned": scanned, "source": source})

            if match_limit and len(matches) >= match_limit:
                stop_reason = "match_limit"
                break
            if scan_limit and scanned >= scan_limit:
                stop_reason = "scan_limit"
                break
            if time_limit and time.monotonic() - started >= time_limit:
                stop_reason = "time_limit"
                break

        yield sse("done", {"job_id": job_id, "matches": len(matches), "scanned": scanned, "source": current_source, "reason": stop_reason})
    except Exception as exc:
        JOBS[job_id] = {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "query": {"username": username, "words": words, "mode": mode, "max_posts": 0},
            "profile": {"username": username},
            "scanned": 0,
            "matches": matches,
        }
        yield sse("error", {"error": format_error(exc), "job_id": job_id, "matches": len(matches)})


@app.route("/search-stream")
def search_stream():
    username = clean_username(request.args.get("username", ""))
    words = parse_words(request.args.get("words", ""))
    mode = request.args.get("mode", "any")
    session_username = request.args.get("session_username", "").strip()
    session_file = request.args.get("session_file", "").strip()
    browser_cookies = request.args.get("browser_cookies", "").strip()
    cookie_file = request.args.get("cookie_file", "").strip()
    content_area = request.args.get("content_area", "both").strip()
    scan_limit = request_limit(request.args.get("scan_limit"), DEFAULT_SCAN_LIMIT)
    match_limit = request_limit(request.args.get("match_limit"), DEFAULT_MATCH_LIMIT)
    time_limit = request_limit(request.args.get("time_limit"), DEFAULT_SEARCH_SECONDS)

    if not username:
        return Response(sse("error", {"error": "Enter an Instagram username or profile URL."}), mimetype="text/event-stream")
    if not words:
        return Response(sse("error", {"error": "Enter one or more words to find in captions."}), mimetype="text/event-stream")
    if session_file and not Path(session_file).exists():
        return Response(sse("error", {"error": "The session file path does not exist on this machine."}), mimetype="text/event-stream")
    if cookie_file and not Path(cookie_file).exists():
        return Response(sse("error", {"error": "The browser cookie file path does not exist on this machine."}), mimetype="text/event-stream")

    if not (session_username or session_file or browser_cookies or cookie_file) and Path(DEFAULT_COOKIE_FILE).exists():
        cookie_file = DEFAULT_COOKIE_FILE
    LAST_COOKIE_SOURCE.update({"browser_cookies": browser_cookies, "cookie_file": cookie_file})
    return Response(
        stream_with_context(
            stream_search_posts(
                username,
                words,
                mode,
                session_username,
                session_file,
                browser_cookies,
                cookie_file,
                scan_limit,
                match_limit,
                time_limit,
                content_area,
            )
        ),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        fields = ["shortcode", "url", "source", "date", "likes", "comments", "is_video", "matched_words", "caption"]
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


@app.route("/proxy-image")
def proxy_image():
    url = request.args.get("url", "").strip()
    if not url:
        return "", 400
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http") or not parsed.netloc.endswith(("instagram.com", "cdninstagram.com", "fbcdn.net")):
        return "", 400

    try:
        cookies = {}
        cookie_file = LAST_COOKIE_SOURCE.get("cookie_file", "")
        if cookie_file and Path(cookie_file).exists():
            cookies = instagram_cookies_from_file(cookie_file)

        response = http_requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"},
            cookies=cookies,
            timeout=15,
        )
        response.raise_for_status()
        return Response(response.content, content_type=response.headers.get("Content-Type", "image/jpeg"))
    except Exception:
        return "", 502


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
