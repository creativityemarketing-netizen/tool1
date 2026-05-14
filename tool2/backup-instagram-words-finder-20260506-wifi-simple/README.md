# Instagram Words Finder

Web app for finding words inside captions from an Instagram profile.

## Features

- Enter `@username` or an Instagram profile URL.
- Load the profile first and preview the profile image, bio, and stats.
- Enter one or more words, separated by spaces or commas.
- Match any word or require all words.
- View matching posts with highlighted words in the description/caption.
- Export matches as CSV or JSON.
- Optional Instaloader session support for profiles that require login.

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Session Notes

For profiles that Instagram blocks publicly, use one of these options in the optional session section:

- Log in from the page with an Instagram account. The app saves a local Instaloader session file on that computer.
- Choose a browser where Instagram is already logged in.
- Export Instagram cookies to a Netscape `cookies.txt` file, leave the browser dropdown as `None`, and paste the cookies file path.
- Create or reuse an Instaloader session and enter the session username plus session file path.

Use this only for profiles and content you are allowed to inspect.
