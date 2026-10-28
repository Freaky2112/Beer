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
    return {"user": session.get("user")}


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


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "recent")
    order = {
        "recent": "tasted_on DESC, id DESC",
        "rating": "rating DESC, tasted_on DESC",
        "name": "name ASC",
    }.get(sort, "tasted_on DESC, id DESC")
    sql = "SELECT * FROM beers"
    args = []
    if q:
        sql += " WHERE name LIKE %s OR brewery LIKE %s OR style LIKE %s"
        args = [f"%{q}%"] * 3
    sql += f" ORDER BY {order}"
    with db() as c, c.cursor() as cur:
        cur.execute(sql, args)
        beers = cur.fetchall()
        cur.execute("SELECT COUNT(*) n, ROUND(AVG(rating),1) avg FROM beers")
        stats = cur.fetchone()
    return render_template(
        "index.html", beers=beers, stats=stats, q=q, sort=sort,
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
            if keep != old:
                remove_photo(old)
            return redirect(url_for("index"))
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
