import os
import re
import sqlite3
from functools import wraps
from html import escape
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, abort, flash, redirect, render_template_string, request, session, url_for

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("GITHUB_PROXY_DB", APP_DIR / "data" / "github_proxy.sqlite3"))
SECRET_KEY = os.environ.get("SECRET_KEY")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
USER_AGENT = "GitHubReleaseProxy/1.0 (+https://github.com)"
RELEASE_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases(?:/(tag|latest)(?:/([^?#]+))?)?/?(?:[?#].*)?$", re.I)
ASSET_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases/download/([^/?#]+)/([^/?#]+)(?:[?#].*)?$", re.I)

if not SECRET_KEY or SECRET_KEY == "change-me-before-production":
    raise RuntimeError("SECRET_KEY must be set to a non-default random value")
if not ADMIN_PASSWORD or ADMIN_PASSWORD in {"admin", "change-this-admin-password"}:
    raise RuntimeError("ADMIN_PASSWORD must be set to a non-default value")

app = Flask(__name__)
app.secret_key = SECRET_KEY


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                release_url TEXT NOT NULL UNIQUE,
                release_scope TEXT NOT NULL DEFAULT 'repo',
                release_tag TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(allowed_releases)")}
        if "release_scope" not in existing_columns:
            conn.execute("ALTER TABLE allowed_releases ADD COLUMN release_scope TEXT NOT NULL DEFAULT 'repo'")
        if "release_tag" not in existing_columns:
            conn.execute("ALTER TABLE allowed_releases ADD COLUMN release_tag TEXT")
        for row_id, release_url in conn.execute("SELECT id, release_url FROM allowed_releases WHERE release_tag IS NULL"):
            match = RELEASE_RE.match(release_url)
            if not match:
                continue
            release_type, release_tag = (match.group(3) or "").lower(), match.group(4)
            if release_type == "tag":
                if not release_tag:
                    continue
                conn.execute("UPDATE allowed_releases SET release_scope=?, release_tag=? WHERE id=?", ("tag", release_tag.rstrip("/"), row_id))
            elif release_type == "latest":
                conn.execute("UPDATE allowed_releases SET release_scope=?, release_tag=? WHERE id=?", ("latest", "", row_id))
            else:
                conn.execute("UPDATE allowed_releases SET release_scope=?, release_tag=? WHERE id=?", ("repo", "", row_id))
        conn.commit()


def db_rows(query, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchall()


def db_execute(query, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(query, params)
        conn.commit()


def path_has_dot_segment(path):
    decoded_path = unquote(path)
    return any(segment in {".", ".."} for segment in decoded_path.split("/"))


def normalize_release_url(raw_url):
    raw_url = raw_url.strip()
    match = RELEASE_RE.match(raw_url)
    if not match:
        raise ValueError("只允许添加形如 https://github.com/{owner}/{repo}/releases、/releases/latest 或 /releases/tag/{tag} 的链接")
    parsed = urlparse(raw_url)
    if path_has_dot_segment(parsed.path):
        raise ValueError("Release 链接不能包含 . 或 .. 路径片段")
    owner, repo = match.group(1), match.group(2)
    release_type = (match.group(3) or "").lower()
    release_tag = match.group(4)
    normalized = f"https://github.com{parsed.path.rstrip('/')}"
    if release_type == "tag":
        if not release_tag:
            raise ValueError("/releases/tag 后必须包含非空 tag")
        return owner, repo, normalized, "tag", release_tag.rstrip("/")
    if release_type == "latest":
        if release_tag:
            raise ValueError("/releases/latest 后不能包含额外路径")
        return owner, repo, normalized, "latest", None
    return owner, repo, normalized, "repo", None


def is_allowed_release(owner, repo, release_type=None, release_tag=None):
    rows = db_rows("SELECT release_scope, release_tag FROM allowed_releases WHERE lower(owner)=lower(?) AND lower(repo)=lower(?)", (owner, repo))
    for row in rows:
        if row["release_scope"] == "repo":
            return True
        if release_type == "latest" and row["release_scope"] == "latest":
            return True
        if release_tag and row["release_scope"] == "tag" and row["release_tag"] == release_tag:
            return True
    return False


def latest_release_tag(owner, repo):
    upstream = fetch_github(f"https://github.com/{owner}/{repo}/releases/latest")
    final_match = RELEASE_RE.match(upstream.url)
    if upstream.status_code >= 400 or not final_match or (final_match.group(3) or "").lower() != "tag":
        return None
    return final_match.group(4).rstrip("/")


def final_release_url_is_allowed(final_url, owner, repo, release_type, release_tag):
    try:
        final_owner, final_repo, _normalized, final_type, final_tag = normalize_release_url(final_url)
    except ValueError:
        return False
    if final_owner.lower() != owner.lower() or final_repo.lower() != repo.lower():
        return False
    if release_type == "latest" and final_type == "tag":
        return is_allowed_release(owner, repo, "latest")
    return is_allowed_release(final_owner, final_repo, final_type, final_tag)


def response_download_redirects_are_allowed(upstream, owner, repo, release_tag):
    for response in [*upstream.history, upstream]:
        parsed = urlparse(response.url)
        if parsed.netloc.lower() != "github.com":
            continue
        match = ASSET_RE.match(response.url)
        if not match:
            continue
        final_owner, final_repo, final_tag = match.group(1), match.group(2), match.group(3)
        if final_owner.lower() != owner.lower() or final_repo.lower() != repo.lower():
            return False
        if final_tag != release_tag:
            return False
    return True


def is_allowed_download(owner, repo, release_tag):
    if is_allowed_release(owner, repo, "tag", release_tag):
        return True
    rows = db_rows("SELECT 1 FROM allowed_releases WHERE lower(owner)=lower(?) AND lower(repo)=lower(?) AND release_scope='latest' LIMIT 1", (owner, repo))
    if not rows:
        return False
    return latest_release_tag(owner, repo) == release_tag


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


BASE_HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title><style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;line-height:1.6}input{padding:.55rem;width:min(100%,560px)}button,a.button{padding:.55rem .9rem;border:0;background:#0969da;color:white;text-decoration:none;border-radius:6px;cursor:pointer}.card{border:1px solid #d0d7de;border-radius:8px;padding:1rem;margin:1rem 0}.msg{background:#fff8c5;padding:.7rem;border-radius:6px}code{background:#f6f8fa;padding:.1rem .25rem;border-radius:4px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #d0d7de;padding:.5rem;text-align:left}</style></head>
<body><h1>{{ title }}</h1>{% with messages = get_flashed_messages() %}{% if messages %}{% for m in messages %}<p class="msg">{{ m }}</p>{% endfor %}{% endif %}{% endwith %}{{ body|safe }}</body></html>
"""


def page(title, body):
    return render_template_string(BASE_HTML, title=title, body=body)


@app.before_request
def setup():
    init_db()


@app.route("/")
def index():
    rows = db_rows("SELECT * FROM allowed_releases ORDER BY owner, repo, release_url")
    items = "".join(f"<li><a href='{url_for('proxy_release', encoded_url=quote(r['release_url'], safe=''))}'>{escape(r['release_url'])}</a></li>" for r in rows)
    return page("GitHub Release 代理", f"<div class='card'><p>只能代理后台白名单中的 GitHub Release 页面及其 Release 文件下载。</p><p><a class='button' href='{url_for('admin')}'>后台管理</a></p></div><h2>允许访问的 Release</h2><ul>{items or '<li>暂无白名单</li>'}</ul>")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin_authenticated"] = True
            return redirect(request.args.get("next") or url_for("admin"))
        flash("密码错误")
    return page("后台登录", "<form method='post'><p><input type='password' name='password' placeholder='管理员密码'></p><button>登录</button></form>")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        validate_csrf()
        try:
            owner, repo, release_url, release_scope, release_tag = normalize_release_url(request.form.get("release_url", ""))
            db_execute("INSERT OR IGNORE INTO allowed_releases(owner, repo, release_url, release_scope, release_tag) VALUES (?, ?, ?, ?, ?)", (owner, repo, release_url, release_scope, release_tag))
            flash("已添加白名单")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("admin"))
    rows = db_rows("SELECT * FROM allowed_releases ORDER BY created_at DESC")
    csrf_token = get_csrf_token()
    table = "".join(f"<tr><td>{escape(r['owner'])}/{escape(r['repo'])}</td><td><a href='{url_for('proxy_release', encoded_url=quote(r['release_url'], safe=''))}'>{escape(r['release_url'])}</a></td><td><form method='post' action='{url_for('delete_release', release_id=r['id'])}'><input type='hidden' name='csrf_token' value='{csrf_token}'><button>删除</button></form></td></tr>" for r in rows)
    body = f"<p><a href='{url_for('index')}'>返回首页</a> · <a href='{url_for('admin_logout')}'>退出</a></p><div class='card'><form method='post'><input type='hidden' name='csrf_token' value='{csrf_token}'><p><input name='release_url' placeholder='https://github.com/owner/repo/releases 或 /releases/tag/v1.0.0' required></p><button>添加允许访问的 Release</button></form></div><table><tr><th>仓库</th><th>Release 链接</th><th>操作</th></tr>{table}</table>"
    return page("后台管理", body)


@app.route("/admin/delete/<int:release_id>", methods=["POST"])
@admin_required
def delete_release(release_id):
    validate_csrf()
    db_execute("DELETE FROM allowed_releases WHERE id=?", (release_id,))
    flash("已删除")
    return redirect(url_for("admin"))


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = os.urandom(32).hex()
    return session["csrf_token"]


def validate_csrf():
    if not session.get("csrf_token") or request.form.get("csrf_token") != session["csrf_token"]:
        abort(403)


def decode_proxy_target(encoded_url):
    if encoded_url.startswith(("https://", "http://")):
        return encoded_url
    return unquote(encoded_url)


def fetch_github(url, stream=False):
    parsed = urlparse(url)
    if path_has_dot_segment(parsed.path):
        abort(403)
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=stream)


def expanded_asset_fragment_url(fragment):
    fragment_src = fragment.get("src") or fragment.get("data-src")
    if not fragment_src:
        return None
    src = urljoin("https://github.com", fragment_src)
    parsed = urlparse(src)
    if parsed.netloc.lower() != "github.com" or "/releases/expanded_assets/" not in parsed.path or path_has_dot_segment(parsed.path):
        return None
    return src


def is_expanded_asset_url(url):
    parsed = urlparse(url)
    return parsed.netloc.lower() == "github.com" and "/releases/expanded_assets/" in parsed.path and not path_has_dot_segment(parsed.path)


def expand_asset_fragments(soup):
    for fragment in soup.find_all("include-fragment"):
        src = expanded_asset_fragment_url(fragment)
        if not src:
            fragment.decompose()
            continue
        upstream = fetch_github(src)
        if upstream.status_code >= 400:
            fragment.decompose()
            continue
        fragment.replace_with(BeautifulSoup(upstream.text, "html.parser"))


def mark_show_all_asset_controls(soup):
    for tag in soup.find_all(["a", "button"]):
        label = tag.get_text(" ", strip=True).lower()
        if "show all" not in label or "asset" not in label:
            continue
        tag["data-github-proxy-show-assets"] = "true"
        if tag.name == "a":
            tag["href"] = "#"
        if tag.name == "button" and not tag.get("type"):
            tag["type"] = "button"


def append_asset_toggle_script(soup):
    if not soup.body:
        return
    script = soup.new_tag("script")
    script.string = """
(() => {
  const hiddenSelectors = '[hidden], .d-none, .js-release-asset, .js-release-asset-read-more';
  document.addEventListener('click', (event) => {
    const control = event.target.closest('[data-github-proxy-show-assets="true"]');
    if (!control) return;
    event.preventDefault();
    const release = control.closest('details, .Box, section, div');
    const root = release || document;
    root.querySelectorAll(hiddenSelectors).forEach((node) => {
      node.hidden = false;
      node.removeAttribute('hidden');
      node.classList.remove('d-none');
    });
    control.hidden = true;
    control.setAttribute('aria-expanded', 'true');
  });
})();
"""
    soup.body.append(script)


@app.route("/release/<path:encoded_url>")
def proxy_release(encoded_url):
    target = decode_proxy_target(encoded_url)
    try:
        owner, repo, normalized, release_type, release_tag = normalize_release_url(target)
    except ValueError:
        abort(403)
    if not is_allowed_release(owner, repo, release_type, release_tag):
        abort(403)
    upstream = fetch_github(normalized)
    if upstream.status_code >= 400:
        return Response("GitHub upstream error", status=upstream.status_code)
    if not final_release_url_is_allowed(upstream.url, owner, repo, release_type, release_tag):
        abort(403)
    soup = BeautifulSoup(upstream.text, "html.parser")
    expand_asset_fragments(soup)
    for tag in soup.find_all(["script", "iframe", "form"]):
        tag.decompose()
    mark_show_all_asset_controls(soup)
    for a in soup.find_all("a", href=True):
        if a.get("data-github-proxy-show-assets") == "true":
            a["href"] = "#"
            continue
        href = urljoin("https://github.com", a["href"])
        if ASSET_RE.match(href):
            a["href"] = url_for("download", encoded_url=quote(href, safe=""))
        elif is_expanded_asset_url(href):
            a["data-github-proxy-show-assets"] = "true"
            a["href"] = "#"
        elif RELEASE_RE.match(href):
            a["href"] = url_for("proxy_release", encoded_url=quote(href, safe=""))
        else:
            a["href"] = "#blocked"
            a["title"] = "该代理仅允许访问白名单 Release 页面和 Release 下载文件"
    append_asset_toggle_script(soup)
    return Response(str(soup), content_type="text/html; charset=utf-8")


@app.route("/download/<path:encoded_url>")
def download(encoded_url):
    target = decode_proxy_target(encoded_url)
    match = ASSET_RE.match(target)
    if not match:
        abort(403)
    parsed = urlparse(target)
    if path_has_dot_segment(parsed.path):
        abort(403)
    owner, repo, release_tag = match.group(1), match.group(2), match.group(3)
    if not is_allowed_download(owner, repo, release_tag):
        abort(403)
    upstream = fetch_github(target, stream=True)
    if not response_download_redirects_are_allowed(upstream, owner, repo, release_tag):
        upstream.close()
        abort(403)
    excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in excluded]
    return Response(upstream.iter_content(chunk_size=8192), status=upstream.status_code, headers=headers)


@app.errorhandler(403)
def forbidden(_):
    return page("禁止访问", "<p>该代理只允许访问后台白名单中的 GitHub Release 页面以及这些仓库的 Release 下载文件。</p>"), 403


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
