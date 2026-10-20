"""
SLSU-JGE Alumni Management System (UPDATED v2)
Southern Luzon State University - Judge Guillermo Eleazar Campus
Framework: Flask + SQLite + Tailwind CSS  (GREEN THEME)

=== WHAT WAS ADDED/CHANGED IN v2 (marked with [NEW]/[CHANGED] comments) ===
[CHANGED] Color theme -> university green (handled in base.html Tailwind config)
[CHANGED] MAX_CONTENT_LENGTH raised to 55 MB for video uploads
[NEW]     Tables: post_media, post_reactions, chat_groups, group_members, group_messages
[NEW]     Photo/Video attachment on posts (stored as base64 data URI in post_media)
[NEW]     Facebook-style 6 reactions (like/love/wow/haha/sad/angry) -> /post/<id>/react
[NEW]     Group chat: create group, add/remove members, rename, delete, group messages
[CHANGED] Messages page now lists 1-on-1 conversations AND group chats with unread badges
Existing tables/routes/features (users, graduate_master, posts, comments, likes,
messages, notifications, homecoming_events, announcements, admin panel) are preserved.
"""
import os
import csv
import io
import base64
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect, url_for,
    session, flash, abort, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "slsu_alumni.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "slsu-jge-alumni-change-this-key-in-production")
# [CHANGED] raised from 8 MB -> 55 MB to allow MP4 uploads (max 50 MB video)
app.config["MAX_CONTENT_LENGTH"] = 55 * 1024 * 1024

# [NEW] Facebook-style reactions: code -> emoji
REACTIONS = {
    "like": "👍", "love": "❤️", "wow": "😮",
    "haha": "😂", "sad": "😢", "angry": "😡",
}

# [NEW] Allowed media types for posts
ALLOWED_MEDIA = {
    "jpg": ("image/jpeg", "image"), "jpeg": ("image/jpeg", "image"),
    "png": ("image/png", "image"), "gif": ("image/gif", "image"),
    "webp": ("image/webp", "image"),
    "mp4": ("video/mp4", "video"), "webm": ("video/webm", "video"),
}

DEPARTMENTS = [
    "College of Teacher Education",
    "College of Arts and Sciences",
    "College of Computer Studies",
    "College of Business and Management",
    "College of Engineering",
    "College of Agriculture and Allied Sciences",
    "College of Criminal Justice Education",
    "College of Industrial Technology",
]

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# [CHANGED] Schema: existing tables kept verbatim; 5 NEW tables appended.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    student_number TEXT NOT NULL,
    department TEXT,
    year_graduated INTEGER,
    email TEXT UNIQUE NOT NULL,
    contact TEXT,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_unverified',
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    is_admin INTEGER NOT NULL DEFAULT 0,
    bio TEXT DEFAULT '',
    employment TEXT DEFAULT '',
    location TEXT DEFAULT '',
    profile_pic TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS graduate_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    student_number TEXT NOT NULL UNIQUE,
    department TEXT,
    year_graduated INTEGER,
    date_added TEXT NOT NULL,
    added_by_admin TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- [KEPT] legacy likes table (no longer used by UI; preserved per "do not remove")
CREATE TABLE IF NOT EXISTS likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    UNIQUE(user_id, post_id)
);

CREATE TABLE IF NOT EXISTS comment_likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    UNIQUE(user_id, comment_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    link TEXT DEFAULT '',
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS homecoming_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    target_year INTEGER,
    event_date TEXT,
    venue TEXT,
    registration_deadline TEXT,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    is_published INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- ===================== [NEW TABLES — v2] =====================
CREATE TABLE IF NOT EXISTS post_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL,            -- 'image' or 'video'
    media_data TEXT NOT NULL,            -- base64 data URI
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reaction_type TEXT NOT NULL,         -- like|love|wow|haha|sad|angry
    created_at TEXT NOT NULL,
    UNIQUE(post_id, user_id)
);

CREATE TABLE IF NOT EXISTS chat_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TEXT NOT NULL,
    last_read_at TEXT,
    UNIQUE(group_id, user_id)
);

CREATE TABLE IF NOT EXISTS group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    message_text TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
"""


def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur = db.execute("SELECT COUNT(*) AS c FROM users WHERE is_admin = 1")
    if cur.fetchone()["c"] == 0:
        db.execute(
            """INSERT INTO users (full_name, student_number, department, year_graduated,
               email, contact, password_hash, status, verification_status, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("System Administrator", "ADMIN-0001", "Administration", 2000,
             "admin@slsu.edu.ph", "09000000000",
             generate_password_hash("admin123"), "approved", "verified", 1, now),
        )

    cur = db.execute("SELECT COUNT(*) AS c FROM users WHERE email = ?", ("alumni@slsu.edu.ph",))
    if cur.fetchone()["c"] == 0:
        db.execute(
            """INSERT INTO users (full_name, student_number, department, year_graduated,
               email, contact, password_hash, status, verification_status, is_admin,
               bio, employment, location, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("Juan Dela Cruz", "2018-0001", "College of Computer Studies", 2018,
             "alumni@slsu.edu.ph", "09170000000",
             generate_password_hash("alumni123"), "approved", "verified", 0,
             "Proud SLSU-JGE graduate.", "Software Engineer at TechCorp", "Manila, Philippines", now),
        )

    cur = db.execute("SELECT COUNT(*) AS c FROM graduate_master")
    if cur.fetchone()["c"] == 0:
        samples = [
            ("Juan Dela Cruz", "2018-0001", "College of Computer Studies", 2018),
            ("Maria Santos", "2018-0025", "College of Teacher Education", 2018),
            ("Jose Reyes", "2019-0102", "College of Engineering", 2019),
            ("Ana Mendoza", "2019-0210", "College of Business and Management", 2019),
            ("Pedro Aquino", "2020-0055", "College of Agriculture and Allied Sciences", 2020),
            ("Gloria Bautista", "2020-0311", "College of Arts and Sciences", 2020),
            ("Ramon Castillo", "2021-0078", "College of Criminal Justice Education", 2021),
            ("Luzviminda David", "2021-0145", "College of Industrial Technology", 2021),
            ("Carlo Espiritu", "2022-0090", "College of Computer Studies", 2022),
            ("Bianca Flores", "2022-0201", "College of Teacher Education", 2022),
            ("Miguel Garcia", "2023-0133", "College of Engineering", 2023),
            ("Sofia Hernandez", "2023-0267", "College of Business and Management", 2023),
        ]
        for name, sn, dept, yr in samples:
            db.execute(
                """INSERT OR IGNORE INTO graduate_master
                   (full_name, student_number, department, year_graduated, date_added, added_by_admin)
                   VALUES (?,?,?,?,?,?)""",
                (name, sn, dept, yr, now, "System Administrator"),
            )

    cur = db.execute("SELECT COUNT(*) AS c FROM announcements")
    if cur.fetchone()["c"] == 0:
        db.execute(
            "INSERT INTO announcements (title, content, created_by, is_published, created_at) VALUES (?,?,?,?,?)",
            ("Welcome to the SLSU-JGE Alumni Portal",
             "This portal connects graduates of Southern Luzon State University - Judge Guillermo Eleazar Campus. "
             "Browse the public Graduate Directory, register your account, and join your fellow alumni.",
             1, 1, now),
        )

    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Auth / helper functions
# ---------------------------------------------------------------------------

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to access that page.", "warning")
            return redirect(url_for("login", next=request.path))
        user = current_user()
        if not user or user["status"] != "approved":
            session.clear()
            flash("Your account is not yet approved or has been deactivated.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id") or not session.get("is_admin"):
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def notify(user_id, ntype, content, link=""):
    db = get_db()
    db.execute(
        "INSERT INTO notifications (user_id, type, content, link, is_read, created_at) VALUES (?,?,?,?,0,?)",
        (user_id, ntype, content, link, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()


def unread_count():
    user = current_user()
    if not user:
        return 0
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0",
        (user["id"],),
    ).fetchone()
    return row["c"] if row else 0


def distinct_departments():
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT department FROM graduate_master WHERE department IS NOT NULL AND department != '' ORDER BY department"
    ).fetchall()
    depts = [r["department"] for r in rows]
    for d in DEPARTMENTS:
        if d not in depts:
            depts.append(d)
    return sorted(depts)


# [NEW] Group chats the current user belongs to, with last message + unread count
def get_user_groups(db, uid):
    return db.execute(
        """SELECT g.id, g.name, g.created_by,
           (SELECT message_text FROM group_messages WHERE group_id = g.id ORDER BY sent_at DESC LIMIT 1) AS last_msg,
           (SELECT sent_at FROM group_messages WHERE group_id = g.id ORDER BY sent_at DESC LIMIT 1) AS last_at,
           (SELECT COUNT(*) FROM group_messages gm2 WHERE gm2.group_id = g.id AND gm2.sender_id != ?
              AND gm2.sent_at > COALESCE((SELECT last_read_at FROM group_members WHERE group_id = g.id AND user_id = ?), '0000-01-01')) AS unread
           FROM chat_groups g JOIN group_members gm ON gm.group_id = g.id
           WHERE gm.user_id = ? ORDER BY COALESCE(last_at, '0000-01-01') DESC""",
        (uid, uid, uid),
    ).fetchall()


@app.template_filter("initials")
def initials(name):
    if not name:
        return "?"
    return "".join(w[0] for w in str(name).split())[:2].upper()


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "unread_count": unread_count(),
        "now_year": datetime.now().year,
        "REACTIONS": REACTIONS,  # [NEW] exposed to templates for reaction picker
    }


# ---------------------------------------------------------------------------
# Public routes (unchanged)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db = get_db()
    announcements = db.execute(
        "SELECT a.*, u.full_name AS author FROM announcements a LEFT JOIN users u ON u.id = a.created_by "
        "WHERE a.is_published = 1 ORDER BY a.created_at DESC LIMIT 10"
    ).fetchall()
    stats = {
        "graduates": db.execute("SELECT COUNT(*) AS c FROM graduate_master").fetchone()["c"],
        "alumni": db.execute("SELECT COUNT(*) AS c FROM users WHERE status='approved' AND is_admin=0").fetchone()["c"],
        "events": db.execute("SELECT COUNT(*) AS c FROM homecoming_events").fetchone()["c"],
    }
    return render_template("index.html", announcements=announcements, stats=stats)


@app.route("/directory")
def directory():
    db = get_db()
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "").strip()
    year_from = request.args.get("year_from", "").strip()
    year_to = request.args.get("year_to", "").strip()

    sql = "SELECT * FROM graduate_master WHERE 1=1"
    params = []
    if q:
        sql += " AND (full_name LIKE ? OR student_number LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if dept:
        sql += " AND department = ?"
        params.append(dept)
    if year_from.isdigit():
        sql += " AND year_graduated >= ?"
        params.append(int(year_from))
    if year_to.isdigit():
        sql += " AND year_graduated <= ?"
        params.append(int(year_to))
    sql += " ORDER BY year_graduated DESC, full_name ASC LIMIT 500"

    graduates = db.execute(sql, params).fetchall()
    depts = distinct_departments()
    return render_template("directory.html", graduates=graduates, depts=depts,
                           q=q, dept=dept, year_from=year_from, year_to=year_to)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        student_number = request.form.get("student_number", "").strip()
        department = request.form.get("department", "").strip()
        year_graduated = request.form.get("year_graduated", "").strip()
        email = request.form.get("email", "").strip().lower()
        contact = request.form.get("contact", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = []
        if not full_name or not student_number or not email or not password:
            errors.append("Full name, student number, email, and password are required.")
        if password and len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if year_graduated and not year_graduated.isdigit():
            errors.append("Year graduated must be a number.")

        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            errors.append("An account with this email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("register.html", depts=distinct_departments(), form=request.form)

        match = db.execute(
            """SELECT * FROM graduate_master WHERE student_number = ?
               AND lower(full_name) = lower(?) AND year_graduated = ?""",
            (student_number, full_name, int(year_graduated) if year_graduated.isdigit() else -1),
        ).fetchone()

        if match:
            status = "pending_verified"
            verification = "verified"
            msg = ("Your information has been verified in our records. "
                   "Waiting for final approval by the administrator.")
        else:
            status = "pending_unverified"
            verification = "unverified"
            msg = ("Your information could not be automatically verified. "
                   "An administrator will review your registration manually.")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            """INSERT INTO users (full_name, student_number, department, year_graduated,
               email, contact, password_hash, status, verification_status, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,0,?)""",
            (full_name, student_number, department,
             int(year_graduated) if year_graduated.isdigit() else None,
             email, contact, generate_password_hash(password), status, verification, now),
        )
        db.commit()
        flash(msg, "success")
        return redirect(url_for("login"))

    return render_template("register.html", depts=distinct_departments(), form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")

        if user["status"] == "pending_verified":
            flash("Your account is verified and awaiting admin approval.", "warning")
            return render_template("login.html")
        if user["status"] == "pending_unverified":
            flash("Your registration is under manual review by an administrator.", "warning")
            return render_template("login.html")
        if user["status"] == "rejected":
            flash("Your registration was rejected. Contact the administrator.", "error")
            return render_template("login.html")
        if user["status"] == "deactivated":
            flash("Your account has been deactivated.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["is_admin"] = bool(user["is_admin"])
        db.execute("UPDATE users SET last_login = ? WHERE id = ?",
                   (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["id"]))
        db.commit()
        nxt = request.args.get("next")
        return redirect(nxt or (url_for("admin_dashboard") if user["is_admin"] else url_for("feed")))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# [CHANGED] Alumni feed — media upload + reactions replace single like
# ---------------------------------------------------------------------------

@app.route("/feed", methods=["GET", "POST"])
@login_required
def feed():
    user = current_user()
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        # [NEW] optional media attachment
        media_file = request.files.get("media")
        media_rec = None
        if media_file and media_file.filename:
            ext = media_file.filename.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_MEDIA:
                flash("Unsupported file. Use JPG/PNG/GIF/WEBP photo or MP4/WEBM video (max 50MB).", "error")
                return redirect(url_for("feed"))
            raw = media_file.read()
            if len(raw) > 50 * 1024 * 1024:
                flash("File too large. Maximum is 50 MB.", "error")
                return redirect(url_for("feed"))
            mime, mtype = ALLOWED_MEDIA[ext]
            data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            media_rec = (mtype, data_uri)

        if not content and not media_rec:
            flash("A post needs text, a photo, or a video.", "error")
            return redirect(url_for("feed"))

        cur = db.execute("INSERT INTO posts (user_id, content, created_at) VALUES (?,?,?)",
                         (user["id"], content, now))
        if media_rec:
            db.execute(
                "INSERT INTO post_media (post_id, media_type, media_data, created_at) VALUES (?,?,?,?)",
                (cur.lastrowid, media_rec[0], media_rec[1], now),
            )
        db.commit()
        return redirect(url_for("feed"))

    posts = db.execute(
        """SELECT p.*, u.full_name, u.department, u.year_graduated
           FROM posts p JOIN users u ON u.id = p.user_id
           ORDER BY p.created_at DESC LIMIT 100""",
    ).fetchall()

    # [NEW] reactions + media aggregated in one pass each
    reaction_rows = db.execute("SELECT post_id, reaction_type, user_id FROM post_reactions").fetchall()
    r_counts, r_mine = {}, {}
    for r in reaction_rows:
        pid = r["post_id"]
        r_counts.setdefault(pid, {})
        r_counts[pid][r["reaction_type"]] = r_counts[pid].get(r["reaction_type"], 0) + 1
        if r["user_id"] == user["id"]:
            r_mine[pid] = r["reaction_type"]

    media_rows = db.execute("SELECT post_id, media_type, media_data FROM post_media").fetchall()
    media_map = {m["post_id"]: (m["media_type"], m["media_data"]) for m in media_rows}

    post_data = []
    for p in posts:
        comments = db.execute(
            """SELECT c.*, u.full_name FROM comments c JOIN users u ON u.id = c.user_id
               WHERE c.post_id = ? ORDER BY c.created_at ASC""", (p["id"],)
        ).fetchall()
        top = [c for c in comments if c["parent_id"] is None]
        replies = {}
        for c in comments:
            if c["parent_id"]:
                replies.setdefault(c["parent_id"], []).append(c)
        post_data.append((p, top, replies, media_map.get(p["id"]),
                          r_counts.get(p["id"], {}), r_mine.get(p["id"])))

    return render_template("feed.html", post_data=post_data)


# [NEW] Reaction endpoint — replaces old single-heart like. Same type again = remove.
@app.route("/post/<int:post_id>/react", methods=["POST"])
@login_required
def react(post_id):
    user = current_user()
    rtype = request.form.get("reaction_type", "like")
    if rtype not in REACTIONS:
        rtype = "like"
    db = get_db()
    existing = db.execute(
        "SELECT id, reaction_type FROM post_reactions WHERE post_id=? AND user_id=?",
        (post_id, user["id"]),
    ).fetchone()
    if existing and existing["reaction_type"] == rtype:
        db.execute("DELETE FROM post_reactions WHERE id=?", (existing["id"],))
    else:
        db.execute(
            "INSERT OR REPLACE INTO post_reactions (post_id, user_id, reaction_type, created_at) VALUES (?,?,?,?)",
            (post_id, user["id"], rtype, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        post = db.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()
        if post and post["user_id"] != user["id"]:
            notify(post["user_id"], "reaction",
                   f"{user['full_name']} reacted {REACTIONS[rtype]} to your post.", url_for("feed"))
    db.commit()
    return redirect(url_for("feed"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    user = current_user()
    content = request.form.get("content", "").strip()
    parent_id = request.form.get("parent_id") or None
    if content:
        db = get_db()
        db.execute(
            "INSERT INTO comments (post_id, user_id, parent_id, content, created_at) VALUES (?,?,?,?,?)",
            (post_id, user["id"], parent_id, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        post = db.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()
        if post and post["user_id"] != user["id"]:
            notify(post["user_id"], "comment", f"{user['full_name']} commented on your post.", url_for("feed"))
        db.commit()
    return redirect(url_for("feed"))


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()
    if post and (post["user_id"] == user["id"] or user["is_admin"]):
        db.execute("DELETE FROM posts WHERE id=?", (post_id,))  # media + reactions cascade-delete
        db.commit()
    return redirect(url_for("feed"))


@app.route("/homecoming")
@login_required
def my_homecoming():
    user = current_user()
    db = get_db()
    events = db.execute(
        """SELECT * FROM homecoming_events WHERE target_year IS NULL OR target_year = ?
           ORDER BY event_date ASC""", (user["year_graduated"],)
    ).fetchall()
    return render_template("homecoming.html", events=events)


@app.route("/alumni")
@login_required
def alumni_directory():
    db = get_db()
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "").strip()
    sql = "SELECT * FROM users WHERE status='approved' AND is_admin=0"
    params = []
    if q:
        sql += " AND (full_name LIKE ? OR student_number LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if dept:
        sql += " AND department = ?"
        params.append(dept)
    sql += " ORDER BY full_name ASC LIMIT 500"
    alumni = db.execute(sql, params).fetchall()
    depts = sorted({a["department"] for a in alumni if a["department"]})
    return render_template("alumni_directory.html", alumni=alumni, depts=depts, q=q, dept=dept)


# ---------------------------------------------------------------------------
# [CHANGED] Messages — 1-on-1 (unchanged logic) + [NEW] group chats
# ---------------------------------------------------------------------------

def _direct_conversations(db, uid):
    rows = db.execute(
        "SELECT sender_id, receiver_id, content, created_at FROM messages "
        "WHERE sender_id = ? OR receiver_id = ? ORDER BY created_at DESC",
        (uid, uid),
    ).fetchall()
    last = {}
    for r in rows:
        other = r["receiver_id"] if r["sender_id"] == uid else r["sender_id"]
        if other not in last:
            last[other] = r
    convos = []
    for other_id, r in last.items():
        urow = db.execute("SELECT full_name FROM users WHERE id = ?", (other_id,)).fetchone()
        convos.append({
            "other_id": other_id,
            "other_name": urow["full_name"] if urow else "Unknown",
            "last_msg": r["content"],
            "last_at": r["created_at"],
        })
    return convos


@app.route("/messages", defaults={"other_id": None}, methods=["GET", "POST"])
@app.route("/messages/<int:other_id>", methods=["GET", "POST"])
@login_required
def messages(other_id):
    user = current_user()
    db = get_db()

    if request.method == "POST" and other_id:
        content = request.form.get("content", "").strip()
        if content:
            db.execute(
                "INSERT INTO messages (sender_id, receiver_id, content, created_at) VALUES (?,?,?,?)",
                (user["id"], other_id, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            notify(other_id, "message", f"New message from {user['full_name']}.",
                   url_for("messages", other_id=user["id"]))
            db.commit()
        return redirect(url_for("messages", other_id=other_id))

    convos = _direct_conversations(db, user["id"])
    groups = get_user_groups(db, user["id"])  # [NEW]

    thread = []
    other = None
    if other_id:
        other = db.execute("SELECT * FROM users WHERE id=?", (other_id,)).fetchone()
        thread = db.execute(
            """SELECT sender_id, content, created_at FROM messages
               WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
               ORDER BY created_at ASC""",
            (user["id"], other_id, other_id, user["id"]),
        ).fetchall()
        db.execute("UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=?",
                   (other_id, user["id"]))
        db.commit()

    return render_template("messages.html", convos=convos, groups=groups,
                           thread=thread, other=other, other_id=other_id,
                           group=None, members=None, alumni=None, is_creator=False)


# [NEW] Create a group chat
@app.route("/messages/group/new", methods=["GET", "POST"])
@login_required
def group_new():
    user = current_user()
    db = get_db()
    alumni = db.execute(
        "SELECT id, full_name, department, year_graduated FROM users "
        "WHERE status='approved' AND is_admin=0 AND id != ? ORDER BY full_name",
        (user["id"],),
    ).fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        member_ids = request.form.getlist("members")
        if not name:
            flash("Group name is required.", "error")
            return redirect(url_for("group_new"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = db.execute("INSERT INTO chat_groups (name, created_by, created_at) VALUES (?,?,?)",
                         (name, user["id"], now))
        gid = cur.lastrowid
        db.execute(
            "INSERT INTO group_members (group_id, user_id, joined_at, last_read_at) VALUES (?,?,?,?)",
            (gid, user["id"], now, now),
        )
        for mid in member_ids:
            try:
                mid = int(mid)
            except ValueError:
                continue
            db.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, joined_at) VALUES (?,?,?)",
                       (gid, mid, now))
            notify(mid, "group", f"{user['full_name']} added you to the group '{name}'.",
                   url_for("group_thread", gid=gid))
        db.commit()
        flash("Group created.", "success")
        return redirect(url_for("group_thread", gid=gid))
    return render_template("create_group.html", alumni=alumni)


# [NEW] View / send in a group chat
@app.route("/messages/group/<int:gid>", methods=["GET", "POST"])
@login_required
def group_thread(gid):
    user = current_user()
    db = get_db()
    membership = db.execute(
        "SELECT * FROM group_members WHERE group_id=? AND user_id=?", (gid, user["id"])
    ).fetchone()
    if not membership:
        abort(403)
    group = db.execute("SELECT * FROM chat_groups WHERE id=?", (gid,)).fetchone()
    if not group:
        abort(404)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if request.method == "POST":
        text = request.form.get("content", "").strip()
        if text:
            db.execute(
                "INSERT INTO group_messages (group_id, sender_id, message_text, sent_at) VALUES (?,?,?,?)",
                (gid, user["id"], text, now),
            )
            members = db.execute(
                "SELECT user_id FROM group_members WHERE group_id=? AND user_id != ?", (gid, user["id"])
            ).fetchall()
            for m in members:
                notify(m["user_id"], "group",
                       f"New message in '{group['name']}' from {user['full_name']}.",
                       url_for("group_thread", gid=gid))
            db.commit()
        return redirect(url_for("group_thread", gid=gid))

    db.execute("UPDATE group_members SET last_read_at=? WHERE group_id=? AND user_id=?",
               (now, gid, user["id"]))
    db.commit()

    thread = db.execute(
        """SELECT gm.sender_id, gm.message_text AS content, gm.sent_at AS created_at,
                  u.full_name AS sender_name
           FROM group_messages gm JOIN users u ON u.id = gm.sender_id
           WHERE gm.group_id=? ORDER BY gm.sent_at ASC""", (gid,),
    ).fetchall()
    members = db.execute(
        """SELECT gm.user_id, u.full_name, u.email
           FROM group_members gm JOIN users u ON u.id = gm.user_id
           WHERE gm.group_id=? ORDER BY u.full_name""", (gid,),
    ).fetchall()
    alumni = db.execute(
        """SELECT id, full_name FROM users WHERE status='approved' AND is_admin=0
           AND id NOT IN (SELECT user_id FROM group_members WHERE group_id=?) ORDER BY full_name""",
        (gid,),
    ).fetchall()

    convos = _direct_conversations(db, user["id"])
    groups = get_user_groups(db, user["id"])
    is_creator = group["created_by"] == user["id"]
    return render_template("messages.html", convos=convos, groups=groups, thread=thread,
                           other=None, other_id=None, group=group, members=members,
                           alumni=alumni, is_creator=is_creator)


# [NEW] Group admin actions (creator only)
def _group_creator_only(db, gid, uid):
    g = db.execute("SELECT created_by FROM chat_groups WHERE id=?", (gid,)).fetchone()
    return g and g["created_by"] == uid


@app.route("/messages/group/<int:gid>/add", methods=["POST"])
@login_required
def group_add_member(gid):
    user = current_user()
    db = get_db()
    if not _group_creator_only(db, gid, user["id"]):
        abort(403)
    new_id = request.form.get("user_id")
    if new_id and new_id.isdigit():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, joined_at) VALUES (?,?,?)",
                   (gid, int(new_id), now))
        g = db.execute("SELECT name FROM chat_groups WHERE id=?", (gid,)).fetchone()
        notify(int(new_id), "group", f"You were added to the group '{g['name']}'.", url_for("group_thread", gid=gid))
        db.commit()
        flash("Member added.", "success")
    return redirect(url_for("group_thread", gid=gid))


@app.route("/messages/group/<int:gid>/remove/<int:uid>", methods=["POST"])
@login_required
def group_remove_member(gid, uid):
    user = current_user()
    db = get_db()
    if not _group_creator_only(db, gid, user["id"]):
        abort(403)
    g = db.execute("SELECT created_by FROM chat_groups WHERE id=?", (gid,)).fetchone()
    if g and g["created_by"] == uid:
        flash("The group creator cannot be removed.", "error")
    else:
        db.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (gid, uid))
        db.commit()
        flash("Member removed.", "info")
    return redirect(url_for("group_thread", gid=gid))


@app.route("/messages/group/<int:gid>/rename", methods=["POST"])
@login_required
def group_rename(gid):
    user = current_user()
    db = get_db()
    if not _group_creator_only(db, gid, user["id"]):
        abort(403)
    new_name = request.form.get("name", "").strip()
    if new_name:
        db.execute("UPDATE chat_groups SET name=? WHERE id=?", (new_name, gid))
        db.commit()
        flash("Group renamed.", "success")
    return redirect(url_for("group_thread", gid=gid))


@app.route("/messages/group/<int:gid>/delete", methods=["POST"])
@login_required
def group_delete(gid):
    user = current_user()
    db = get_db()
    if not _group_creator_only(db, gid, user["id"]):
        abort(403)
    db.execute("DELETE FROM chat_groups WHERE id=?", (gid,))  # members + messages cascade
    db.commit()
    flash("Group deleted.", "info")
    return redirect(url_for("messages"))


@app.route("/notifications")
@login_required
def notifications():
    user = current_user()
    db = get_db()
    items = db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
                       (user["id"],)).fetchall()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
    db.commit()
    return render_template("notifications.html", items=items)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    db = get_db()
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()
        employment = request.form.get("employment", "").strip()
        location = request.form.get("location", "").strip()
        contact = request.form.get("contact", "").strip()
        profile_pic = request.form.get("profile_pic", "").strip()
        db.execute(
            "UPDATE users SET bio=?, employment=?, location=?, contact=?, profile_pic=? WHERE id=?",
            (bio, employment, location, contact, profile_pic, user["id"]),
        )
        db.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


# ---------------------------------------------------------------------------
# Admin panel (unchanged)
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {
        "pending": db.execute("SELECT COUNT(*) c FROM users WHERE status LIKE 'pending_%'").fetchone()["c"],
        "approved": db.execute("SELECT COUNT(*) c FROM users WHERE status='approved' AND is_admin=0").fetchone()["c"],
        "graduates": db.execute("SELECT COUNT(*) c FROM graduate_master").fetchone()["c"],
        "posts": db.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"],
    }
    recent = db.execute(
        "SELECT * FROM users WHERE status LIKE 'pending_%' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    return render_template("admin/dashboard.html", stats=stats, recent=recent)


@app.route("/admin/graduates", methods=["GET", "POST"])
@admin_required
def admin_graduates():
    db = get_db()
    if request.method == "POST" and request.form.get("action") == "add":
        full_name = request.form.get("full_name", "").strip()
        student_number = request.form.get("student_number", "").strip()
        department = request.form.get("department", "").strip()
        year_graduated = request.form.get("year_graduated", "").strip()
        if full_name and student_number:
            try:
                db.execute(
                    """INSERT OR IGNORE INTO graduate_master
                       (full_name, student_number, department, year_graduated, date_added, added_by_admin)
                       VALUES (?,?,?,?,?,?)""",
                    (full_name, student_number, department,
                     int(year_graduated) if year_graduated.isdigit() else None,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_user()["full_name"]),
                )
                db.commit()
                flash("Graduate added to master list.", "success")
            except Exception as e:
                flash(f"Error: {e}", "error")
        else:
            flash("Full name and student number are required.", "error")
        return redirect(url_for("admin_graduates"))

    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM graduate_master WHERE 1=1"
    params = []
    if q:
        sql += " AND (full_name LIKE ? OR student_number LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY year_graduated DESC, full_name ASC LIMIT 500"
    graduates = db.execute(sql, params).fetchall()
    return render_template("admin/graduates.html", graduates=graduates, q=q, depts=distinct_departments())


@app.route("/admin/graduates/upload", methods=["POST"])
@admin_required
def admin_graduates_upload():
    db = get_db()
    file = request.files.get("file")
    if not file or not file.filename.endswith(".csv"):
        flash("Please upload a CSV file.", "error")
        return redirect(url_for("admin_graduates"))
    try:
        stream = io.StringIO(file.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        added = 0
        for row in reader:
            name = (row.get("full_name") or row.get("Full Name") or "").strip()
            sn = (row.get("student_number") or row.get("Student Number") or "").strip()
            dept = (row.get("department") or row.get("Department") or "").strip()
            yr = (row.get("year_graduated") or row.get("Year Graduated") or "").strip()
            if name and sn:
                db.execute(
                    """INSERT OR IGNORE INTO graduate_master
                       (full_name, student_number, department, year_graduated, date_added, added_by_admin)
                       VALUES (?,?,?,?,?,?)""",
                    (name, sn, dept, int(yr) if str(yr).isdigit() else None, now, current_user()["full_name"]),
                )
                added += 1
        db.commit()
        flash(f"Bulk upload complete: {added} record(s) processed.", "success")
    except Exception as e:
        flash(f"Upload failed: {e}", "error")
    return redirect(url_for("admin_graduates"))


@app.route("/admin/graduates/<int:gid>/edit", methods=["POST"])
@admin_required
def admin_graduate_edit(gid):
    db = get_db()
    full_name = request.form.get("full_name", "").strip()
    student_number = request.form.get("student_number", "").strip()
    department = request.form.get("department", "").strip()
    year_graduated = request.form.get("year_graduated", "").strip()
    db.execute(
        "UPDATE graduate_master SET full_name=?, student_number=?, department=?, year_graduated=? WHERE id=?",
        (full_name, student_number, department,
         int(year_graduated) if year_graduated.isdigit() else None, gid),
    )
    db.commit()
    flash("Graduate record updated.", "success")
    return redirect(url_for("admin_graduates"))


@app.route("/admin/graduates/<int:gid>/delete", methods=["POST"])
@admin_required
def admin_graduate_delete(gid):
    db = get_db()
    db.execute("DELETE FROM graduate_master WHERE id=?", (gid,))
    db.commit()
    flash("Graduate record deleted.", "info")
    return redirect(url_for("admin_graduates"))


@app.route("/admin/approvals")
@admin_required
def admin_approvals():
    db = get_db()
    pending = db.execute(
        "SELECT * FROM users WHERE status LIKE 'pending_%' ORDER BY created_at ASC"
    ).fetchall()
    return render_template("admin/approvals.html", pending=pending)


@app.route("/admin/approvals/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_approval_action(uid, action):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        abort(404)
    if action == "approve":
        db.execute("UPDATE users SET status='approved' WHERE id=?", (uid,))
        notify(uid, "approval", "Your alumni account has been approved. Welcome to the SLSU-JGE alumni community!",
               url_for("feed"))
        flash(f"Approved {user['full_name']}.", "success")
    elif action == "reject":
        db.execute("UPDATE users SET status='rejected' WHERE id=?", (uid,))
        notify(uid, "approval", "Your registration was not approved. Contact the registrar for assistance.", "")
        flash(f"Rejected {user['full_name']}.", "info")
    elif action == "verify":
        db.execute("UPDATE users SET status='pending_verified', verification_status='verified' WHERE id=?", (uid,))
        flash(f"Manually verified {user['full_name']}.", "success")
    db.commit()
    return redirect(url_for("admin_approvals"))


@app.route("/admin/users")
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users WHERE is_admin=0 ORDER BY created_at DESC").fetchall()
    return render_template("admin/users.html", users=users)


@app.route("/admin/users/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_user_action(uid, action):
    me = current_user()
    if uid == me["id"]:
        flash("You cannot modify your own account.", "error")
        return redirect(url_for("admin_users"))
    db = get_db()
    if action == "deactivate":
        db.execute("UPDATE users SET status='deactivated' WHERE id=?", (uid,))
        flash("Account deactivated.", "info")
    elif action == "activate":
        db.execute("UPDATE users SET status='approved' WHERE id=?", (uid,))
        flash("Account activated.", "success")
    db.commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/homecoming", methods=["GET", "POST"])
@admin_required
def admin_homecoming():
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        target_year = request.form.get("target_year", "").strip()
        event_date = request.form.get("event_date", "").strip()
        venue = request.form.get("venue", "").strip()
        deadline = request.form.get("registration_deadline", "").strip()
        description = request.form.get("description", "").strip()
        if title:
            db.execute(
                """INSERT INTO homecoming_events (title, target_year, event_date, venue,
                   registration_deadline, description, created_at) VALUES (?,?,?,?,?,?,?)""",
                (title, int(target_year) if target_year.isdigit() else None,
                 event_date or None, venue, deadline or None, description,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            db.commit()
            flash("Homecoming event added.", "success")
        return redirect(url_for("admin_homecoming"))
    events = db.execute("SELECT * FROM homecoming_events ORDER BY event_date DESC").fetchall()
    return render_template("admin/homecoming.html", events=events)


@app.route("/admin/homecoming/<int:eid>/delete", methods=["POST"])
@admin_required
def admin_homecoming_delete(eid):
    db = get_db()
    db.execute("DELETE FROM homecoming_events WHERE id=?", (eid,))
    db.commit()
    flash("Event deleted.", "info")
    return redirect(url_for("admin_homecoming"))


@app.route("/admin/announcements", methods=["GET", "POST"])
@admin_required
def admin_announcements():
    db = get_db()
    me = current_user()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        if title and content:
            db.execute(
                "INSERT INTO announcements (title, content, created_by, is_published, created_at) VALUES (?,?,?,1,?)",
                (title, content, me["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            db.commit()
            alumni = db.execute("SELECT id FROM users WHERE status='approved' AND is_admin=0").fetchall()
            for a in alumni:
                notify(a["id"], "announcement", f"New announcement: {title}", url_for("index"))
            flash("Announcement published and alumni notified.", "success")
        return redirect(url_for("admin_announcements"))
    items = db.execute(
        "SELECT a.*, u.full_name AS author FROM announcements a LEFT JOIN users u ON u.id=a.created_by "
        "ORDER BY a.created_at DESC"
    ).fetchall()
    return render_template("admin/announcements.html", items=items)


@app.route("/admin/announcements/<int:aid>/delete", methods=["POST"])
@admin_required
def admin_announcement_delete(aid):
    db = get_db()
    db.execute("DELETE FROM announcements WHERE id=?", (aid,))
    db.commit()
    flash("Announcement deleted.", "info")
    return redirect(url_for("admin_announcements"))


@app.route("/api/verify")
def api_verify():
    name = request.args.get("name", "").strip()
    sn = request.args.get("student_number", "").strip()
    year = request.args.get("year", "").strip()
    db = get_db()
    match = db.execute(
        """SELECT 1 FROM graduate_master WHERE student_number=? AND lower(full_name)=lower(?)
           AND year_graduated=?""",
        (sn, name, int(year) if year.isdigit() else -1),
    ).fetchone()
    return jsonify({"verified": bool(match)})


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You do not have permission to access this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
