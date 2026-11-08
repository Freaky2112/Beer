import json
import os
import re
import time
import uuid
from datetime import date, timedelta
from functools import wraps

import pymysql
from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from PIL import Image, ImageOps
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
for _v in ("SECRET_KEY", "ADMIN_USER", "ADMIN_PASSWORD"):
    if not os.environ.get(_v):
        raise SystemExit(f"Missing {_v}: set it in .env and run 'docker compose up -d --build'")
app.secret_key = os.environ["SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    MAX_CONTENT_LENGTH=15 * 1024 * 1024,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_USER = os.environ["ADMIN_USER"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]  # only used to create the first admin

MIN_PW = 8
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
DUMMY_HASH = generate_password_hash("not-a-real-password")


@app.before_request
def load_user():
    g.user = None
    if request.endpoint in (None, "static", "photo", "manifest"):
        return
    uid = session.get("uid")
    if uid:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT id, username, is_admin FROM users WHERE id=%s", (uid,))
            g.user = cur.fetchone()
        if g.user is None:
            session.clear()


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("login"))
        if not g.user["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper


def can_edit(beer):
    """Owners can change their own beers; admins can change any."""
    return bool(g.get("user")) and (
        bool(g.user["is_admin"]) or beer.get("user_id") == g.user["id"]
    )


@app.context_processor
def inject_globals():
    return {"user": g.get("user"), "PRESET_TAGS": PRESET_TAGS, "can_edit": can_edit}


@app.route("/manifest.webmanifest")
def manifest():
    icons = [
        {"src": url_for("static", filename=f"icon-{n}.png"), "sizes": f"{n}x{n}",
         "type": "image/png", "purpose": "any maskable"}
        for n in (192, 512)
    ]
    data = {
        "name": "Beer log", "short_name": "Beer log", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#17201a", "theme_color": "#17201a",
        "icons": icons,
    }
    return Response(json.dumps(data), mimetype="application/manifest+json")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT id, password_hash FROM users WHERE username=%s", (u,))
            row = cur.fetchone()
        # always run one hash check so unknown usernames take as long as wrong passwords
        ok = check_password_hash(row["password_hash"] if row else DUMMY_HASH, p)
        if row and ok:
            session.clear()
            session.permanent = True
            session["uid"] = row["id"]
            return redirect(url_for("index"))
        time.sleep(1)
        flash("Wrong username or password.")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        new = request.form.get("new_password", "")
        if new != request.form.get("confirm_password", ""):
            flash("The new passwords don't match.")
        elif len(new) < MIN_PW:
            flash(f"Use at least {MIN_PW} characters for the new password.")
        else:
            with db() as c, c.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE id=%s", (g.user["id"],))
                row = cur.fetchone()
                if not row or not check_password_hash(
                    row["password_hash"], request.form.get("current_password", "")
                ):
                    time.sleep(1)
                    flash("Your current password is wrong.")
                else:
                    cur.execute(
                        "UPDATE users SET password_hash=%s WHERE id=%s",
                        (generate_password_hash(new), g.user["id"]),
                    )
                    flash("Password changed.", "ok")
        return redirect(url_for("account"))
    return render_template("account.html")


@app.route("/admin/users")
@admin_required
def admin_users():
    with db() as c, c.cursor() as cur:
        cur.execute(
            "SELECT u.id, u.username, u.is_admin, COUNT(b.id) AS n FROM users u"
            " LEFT JOIN beers b ON b.user_id = u.id GROUP BY u.id ORDER BY u.username"
        )
        users = cur.fetchall()
    return render_template("admin_users.html", users=users)


@app.post("/admin/users/add")
@admin_required
def admin_add_user():
    name = request.form.get("username", "").strip()
    pw = request.form.get("password", "")
    if not USERNAME_RE.match(name):
        flash("Username: 3 to 30 letters, digits, dots, dashes or underscores.")
    elif len(pw) < MIN_PW:
        flash(f"Use at least {MIN_PW} characters for the password.")
    else:
        try:
            with db() as c, c.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (%s,%s,%s)",
                    (name, generate_password_hash(pw), 1 if request.form.get("is_admin") else 0),
                )
            flash(f"User {name} created.", "ok")
        except pymysql.err.IntegrityError:
            flash("That username is already taken.")
    return redirect(url_for("admin_users"))


@app.post("/admin/users/<int:uid>/password")
@admin_required
def admin_reset_password(uid):
    pw = request.form.get("new_password", "")
    if len(pw) < MIN_PW:
        flash(f"Use at least {MIN_PW} characters for the password.")
    else:
        with db() as c, c.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash=%s WHERE id=%s",
                (generate_password_hash(pw), uid),
            )
        flash("Password updated.", "ok")
    return redirect(url_for("admin_users"))


@app.post("/admin/users/<int:uid>/delete")
@admin_required
def admin_delete_user(uid):
    if uid == g.user["id"]:
        flash("You can't delete your own account.")
    else:
        with db() as c, c.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        flash("User deleted. Their beers stay in the log.", "ok")
    return redirect(url_for("admin_users"))


PRESET_TAGS = [
    "citrus", "tropical", "fruity", "floral", "piney", "hoppy", "bitter",
    "malty", "caramel", "toasty", "roasty", "coffee", "chocolate", "smoky",
    "spicy", "sweet", "sour", "funky", "dry", "creamy",
]

TAGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS beer_tags (
    beer_id INT NOT NULL,
    tag VARCHAR(30) NOT NULL,
    PRIMARY KEY (beer_id, tag),
    KEY idx_tag (tag),
    FOREIGN KEY (beer_id) REFERENCES beers(id) ON DELETE CASCADE
) CHARACTER SET utf8mb4
"""

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_admin TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_username (username)
) CHARACTER SET utf8mb4
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS beers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    brewery VARCHAR(150),
    style VARCHAR(80),
    abv DECIMAL(4,1),
    rating DECIMAL(3,1) NOT NULL DEFAULT 7.0,
    review TEXT,
    tasted_on DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4
"""


def db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "db"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def init_db():
    for _ in range(30):
        try:
            with db() as c, c.cursor() as cur:
                cur.execute(SCHEMA)
                cur.execute(TAGS_SCHEMA)
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS app_meta ("
                    "k VARCHAR(50) PRIMARY KEY, v VARCHAR(50) NOT NULL)"
                )
                # Old installs used a 1-5 scale: double those ratings exactly once.
                # INSERT IGNORE affects 1 row only for the worker that wins the race.
                cur.execute(
                    "INSERT IGNORE INTO app_meta (k, v) VALUES ('rating_scale', '10')"
                )
                if cur.rowcount == 1:
                    cur.execute("UPDATE beers SET rating = rating * 2")
                # Allow half points (7.5): widen the column if it is still an integer type.
                cur.execute(
                    "SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
                    " AND TABLE_NAME = 'beers' AND COLUMN_NAME = 'rating'"
                )
                col = cur.fetchone()
                if col and col["DATA_TYPE"].lower() != "decimal":
                    cur.execute(
                        "ALTER TABLE beers MODIFY rating DECIMAL(3,1) NOT NULL DEFAULT 7.0"
                    )
                cur.execute(
                    "ALTER TABLE beers ADD COLUMN IF NOT EXISTS photo VARCHAR(64) NULL"
                )
                cur.execute(
                    "ALTER TABLE beers ADD COLUMN IF NOT EXISTS ibu SMALLINT NULL"
                )
                cur.execute(
                    "ALTER TABLE beers ADD COLUMN IF NOT EXISTS serve_temp DECIMAL(4,1) NULL"
                )
                # --- multi-user: users table, beer owner, first admin from .env
                cur.execute(USERS_SCHEMA)
                cur.execute("ALTER TABLE beers ADD COLUMN IF NOT EXISTS user_id INT NULL")
                cur.execute(
                    "SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE"
                    " CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'beers'"
                    " AND CONSTRAINT_NAME = 'fk_beers_user'"
                )
                if not cur.fetchone():
                    cur.execute(
                        "ALTER TABLE beers ADD CONSTRAINT fk_beers_user FOREIGN KEY"
                        " (user_id) REFERENCES users(id) ON DELETE SET NULL"
                    )
                cur.execute("SELECT COUNT(*) AS n FROM users")
                if cur.fetchone()["n"] == 0:
                    cur.execute(
                        "INSERT IGNORE INTO users (username, password_hash, is_admin)"
                        " VALUES (%s, %s, 1)",
                        (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD)),
                    )
                    if cur.rowcount == 1:  # we created it: existing beers become theirs
                        cur.execute(
                            "UPDATE beers SET user_id = %s WHERE user_id IS NULL",
                            (cur.lastrowid,),
                        )
            return
        except pymysql.err.OperationalError:
            time.sleep(2)
    raise RuntimeError("Database not reachable")


init_db()


def num(value, cast):
    value = (value or "").strip().replace(",", ".")
    try:
        return cast(value) if value else None
    except ValueError:
        return None


def rating_from(value):
    """1-10 scale in half points; blank or invalid falls back to 7."""
    r = num(value, float)
    if r is None:
        return 7.0
    return min(max(round(r * 2) / 2, 0.5), 10.0)


def form_data():
    f = request.form
    return (
        f["name"].strip(),
        f.get("brewery", "").strip() or None,
        f.get("style", "").strip() or None,
        num(f.get("abv"), float),
        rating_from(f.get("rating")),
        f.get("review", "").strip() or None,
        f.get("tasted_on") or date.today().isoformat(),
        num(f.get("ibu"), lambda v: int(float(v))),
        num(f.get("serve_temp"), float),
    )


def parse_tags():
    raw = request.form.getlist("tags")
    raw += request.form.get("custom_tags", "").split(",")
    seen, out = set(), []
    for t in raw:
        t = " ".join(t.lower().split())[:30]
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:15]


def set_tags(cur, beer_id, tags):
    cur.execute("DELETE FROM beer_tags WHERE beer_id=%s", (beer_id,))
    if tags:
        cur.executemany(
            "INSERT INTO beer_tags (beer_id, tag) VALUES (%s,%s)",
            [(beer_id, t) for t in tags],
        )


def valid_date(v):
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        return ""


SORTS = {
    "recent": "tasted_on DESC, beers.id DESC",
    "rating": "rating DESC, tasted_on DESC",
    "name": "name ASC",
    "abv": "abv DESC, name ASC",
    "ibu": "ibu DESC, name ASC",
}


@app.route("/")
def index():
    a = request.args
    q = a.get("q", "").strip()
    sort = a.get("sort", "recent")
    if sort not in SORTS:
        sort = "recent"
    style = a.get("style", "").strip()
    by = a.get("by", "").strip()
    min_rating = num(a.get("min_rating"), int) or 0
    date_from = valid_date(a.get("date_from", ""))
    date_to = valid_date(a.get("date_to", ""))
    sel_tags = []
    for t in a.getlist("tag"):
        t = " ".join(t.lower().split())
        if t and t not in sel_tags:
            sel_tags.append(t)

    where, args = [], []
    if q:
        where.append("(name LIKE %s OR brewery LIKE %s OR style LIKE %s)")
        args += [f"%{q}%"] * 3
    if style:
        where.append("style = %s")
        args.append(style)
    if by:
        where.append("users.username = %s")
        args.append(by)
    if min_rating:
        where.append("rating >= %s")
        args.append(min_rating)
    if date_from:
        where.append("tasted_on >= %s")
        args.append(date_from)
    if date_to:
        where.append("tasted_on <= %s")
        args.append(date_to)
    for t in sel_tags:
        where.append(
            "EXISTS (SELECT 1 FROM beer_tags bt WHERE bt.beer_id = beers.id AND bt.tag = %s)"
        )
        args.append(t)
    filtered = bool(where)

    sql = (
        "SELECT beers.*, users.username AS author FROM beers"
        " LEFT JOIN users ON users.id = beers.user_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + SORTS[sort]

    with db() as c, c.cursor() as cur:
        cur.execute(sql, args)
        beers = cur.fetchall()
        tagmap = {}
        if beers:
            marks = ",".join(["%s"] * len(beers))
            cur.execute(
                f"SELECT beer_id, tag FROM beer_tags WHERE beer_id IN ({marks}) ORDER BY tag",
                [b["id"] for b in beers],
            )
            for row in cur.fetchall():
                tagmap.setdefault(row["beer_id"], []).append(row["tag"])
        cur.execute("SELECT COUNT(*) n, ROUND(AVG(rating),1) avg FROM beers")
        stats = cur.fetchone()
        cur.execute(
            "SELECT DISTINCT style FROM beers WHERE style IS NOT NULL AND style <> '' ORDER BY style"
        )
        styles = [r["style"] for r in cur.fetchall()]
        cur.execute(
            "SELECT DISTINCT u.username FROM users u JOIN beers b ON b.user_id = u.id"
            " ORDER BY u.username"
        )
        authors = [r["username"] for r in cur.fetchall()]
        cur.execute("SELECT tag, COUNT(*) n FROM beer_tags GROUP BY tag ORDER BY tag")
        tag_counts = cur.fetchall()
    return render_template(
        "index.html", beers=beers, tagmap=tagmap, stats=stats, q=q, sort=sort,
        style=style, by=by, authors=authors, min_rating=min_rating, date_from=date_from, date_to=date_to,
        sel_tags=sel_tags, filtered=filtered, styles=styles, tag_counts=tag_counts,
        today=date.today().isoformat(),
    )


def save_photo(file):
    """Validate, resize and store an uploaded image. Returns filename or None."""
    if not file or not file.filename:
        return None
    try:
        img = Image.open(file.stream)
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1200, 1200))
    except Exception:
        flash("That file isn't a valid image, so the photo was skipped.")
        return None
    name = uuid.uuid4().hex + ".jpg"
    img.save(os.path.join(UPLOAD_DIR, name), "JPEG", quality=85)
    return name


def remove_photo(name):
    if name:
        try:
            os.remove(os.path.join(UPLOAD_DIR, os.path.basename(name)))
        except FileNotFoundError:
            pass


@app.route("/uploads/<path:name>")
def photo(name):
    return send_from_directory(UPLOAD_DIR, name, max_age=86400)


@app.post("/add")
@login_required
def add():
    photo_name = save_photo(request.files.get("photo"))
    with db() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO beers (name, brewery, style, abv, rating, review, tasted_on,"
            " ibu, serve_temp, photo, user_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*form_data(), photo_name, g.user["id"]),
        )
        set_tags(cur, cur.lastrowid, parse_tags())
    return redirect(url_for("index"))


@app.route("/edit/<int:beer_id>", methods=["GET", "POST"])
@login_required
def edit(beer_id):
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM beers WHERE id=%s", (beer_id,))
        beer = cur.fetchone()
        if not beer:
            abort(404)
        if not can_edit(beer):
            flash("You can only change beers you logged yourself.")
            return redirect(url_for("index"))
        if request.method == "POST":
            old = beer["photo"]
            new = save_photo(request.files.get("photo"))
            keep = old
            if new:
                keep = new
            elif request.form.get("remove_photo"):
                keep = None
            cur.execute(
                "UPDATE beers SET name=%s, brewery=%s, style=%s, abv=%s,"
                " rating=%s, review=%s, tasted_on=%s, ibu=%s, serve_temp=%s,"
                " photo=%s WHERE id=%s",
                (*form_data(), keep, beer_id),
            )
            set_tags(cur, beer_id, parse_tags())
            if keep != old:
                remove_photo(old)
            return redirect(url_for("index"))
        cur.execute("SELECT tag FROM beer_tags WHERE beer_id=%s ORDER BY tag", (beer_id,))
        beer["tags"] = [r["tag"] for r in cur.fetchall()]
    return render_template("edit.html", b=beer)


@app.post("/delete/<int:beer_id>")
@login_required
def delete(beer_id):
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT photo, user_id FROM beers WHERE id=%s", (beer_id,))
        row = cur.fetchone()
        if row and not can_edit(row):
            flash("You can only delete beers you logged yourself.")
            return redirect(url_for("index"))
        cur.execute("DELETE FROM beers WHERE id=%s", (beer_id,))
    if row:
        remove_photo(row["photo"])
    return redirect(url_for("index"))
