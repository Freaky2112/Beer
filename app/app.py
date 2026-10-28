import hmac
import os
import time
import uuid
from datetime import date
from functools import wraps

import pymysql
from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_from_directory, session, url_for)
from PIL import Image, ImageOps

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
)

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_USER = os.environ["ADMIN_USER"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("user"):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


@app.context_processor
def inject_user():
    return {"user": session.get("user"), "PRESET_TAGS": PRESET_TAGS}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        ok_u = hmac.compare_digest(u.encode(), ADMIN_USER.encode())
        ok_p = hmac.compare_digest(p.encode(), ADMIN_PASSWORD.encode())
        if ok_u and ok_p:
            session.clear()
            session["user"] = u
            return redirect(url_for("index"))
        time.sleep(1)
        flash("Wrong username or password.")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS beers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    brewery VARCHAR(150),
    style VARCHAR(80),
    abv DECIMAL(4,1),
    rating TINYINT NOT NULL DEFAULT 3,
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
                    "ALTER TABLE beers ADD COLUMN IF NOT EXISTS photo VARCHAR(64) NULL"
                )
                cur.execute(
                    "ALTER TABLE beers ADD COLUMN IF NOT EXISTS ibu SMALLINT NULL"
                )
                cur.execute(
                    "ALTER TABLE beers ADD COLUMN IF NOT EXISTS serve_temp DECIMAL(4,1) NULL"
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


def form_data():
    f = request.form
    return (
        f["name"].strip(),
        f.get("brewery", "").strip() or None,
        f.get("style", "").strip() or None,
        num(f.get("abv"), float),
        min(max(int(f.get("rating", 3)), 1), 5),
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
    "recent": "tasted_on DESC, id DESC",
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

    sql = "SELECT * FROM beers"
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
        cur.execute("SELECT tag, COUNT(*) n FROM beer_tags GROUP BY tag ORDER BY tag")
        tag_counts = cur.fetchall()
    return render_template(
        "index.html", beers=beers, tagmap=tagmap, stats=stats, q=q, sort=sort,
        style=style, min_rating=min_rating, date_from=date_from, date_to=date_to,
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
            " ibu, serve_temp, photo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*form_data(), photo_name),
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
        cur.execute("SELECT photo FROM beers WHERE id=%s", (beer_id,))
        row = cur.fetchone()
        cur.execute("DELETE FROM beers WHERE id=%s", (beer_id,))
    if row:
        remove_photo(row["photo"])
    return redirect(url_for("index"))
