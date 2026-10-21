"""
SLSU-JGE Alumni Management System — PostgreSQL (Neon) FIXED VERSION
MGA NA-AYOS:
  1. cur.lastrowid (HINDI gumagana sa psycopg2) -> RETURNING id + fetchone()["id"]
  2. init_db() nakabalot sa try/except + ON CONFLICT DO NOTHING sa seeds
  3. 'pending_%%' -> 'pending_%' (single % lang, hindi %-formatted ang string)
  4. Mas malinis na cursor handling
"""
import os
import csv
import io
import base64
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import (
    Flask, g, render_template, request, redirect, url_for,
    session, flash, abort, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# DATABASE CONFIGURATION — ilagay ang Neon URL sa Render environment variable
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://username:password@hostname:5432/dbname?sslmode=require"
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "slsu-jge-alumni-change-this-key-in-production")
app.config["MAX_CONTENT_LENGTH"] = 55 * 1024 * 1024

REACTIONS = {
    "like": "👍", "love": "❤️", "wow": "😮",
    "haha": "😂", "sad": "😢", "angry": "😡",
}

ALLOWED_MEDIA = {
    "jpg": ("image/jpeg", "image"), "jpeg": ("image/jpeg", "image"),
    "png": ("image/png", "image"), "gif": ("image/gif", "image"),
    "webp": ("image/webp", "image"),
    "mp4": ("video/mp4", "video"), "webm": ("video/webm", "video"),
}
# [NEW v3] Allowed image types for profile picture upload
ALLOWED_IMAGES = {"jpg", "jpeg", "png", "gif", "webp"}
IMAGE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}

def _process_profile_pic(file_storage):
    """[NEW v3] Convert uploaded image file to base64 data URI. Returns None if invalid/too large."""
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMAGES:
        return None
    raw = file_storage.read()
    if len(raw) > 5 * 1024 * 1024:  # 5MB max for profile pic
        return None
    return f"data:{IMAGE_MIME[ext]};base64,{base64.b64encode(raw).decode()}"

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
# DATABASE HELPERS — PostgreSQL
# ---------------------------------------------------------------------------
def _connect(dict_cursor=True):
    parsed = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=parsed.path[1:],
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port or 5432,
        sslmode="require",
        cursor_factory=RealDictCursor if dict_cursor else None,
    )

def get_db():
    if "db" not in g:
        g.db = _connect(dict_cursor=True)
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# ---------------------------------------------------------------------------
# SCHEMA — PostgreSQL
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
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
    id SERIAL PRIMARY KEY,
    full_name TEXT NOT NULL,
    student_number TEXT NOT NULL UNIQUE,
    department TEXT,
    year_graduated INTEGER,
    date_added TEXT NOT NULL,
    added_by_admin TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS likes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    UNIQUE(user_id, post_id)
);
CREATE TABLE IF NOT EXISTS comment_likes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    UNIQUE(user_id, comment_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    link TEXT DEFAULT '',
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homecoming_events (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    target_year INTEGER,
    event_date TEXT,
    venue TEXT,
    registration_deadline TEXT,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS announcements (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    is_published INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS post_media (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL,
    media_data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS post_reactions (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reaction_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(post_id, user_id)
);
CREATE TABLE IF NOT EXISTS chat_groups (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TEXT NOT NULL,
    last_read_at TEXT,
    UNIQUE(group_id, user_id)
);
CREATE TABLE IF NOT EXISTS group_messages (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    message_text TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
"""

def init_db():
    """NA-AYOS: nakabalot sa try/except, ON CONFLICT sa seeds, hindi nag-crash sa startup."""
    try:
        conn = _connect(dict_cursor=False)
        cur = conn.cursor()
        for stmt in SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cur.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        if cur.fetchone()[0] == 0:
            cur.execute(
                """INSERT INTO users (full_name, student_number, department, year_graduated,
                   email, contact, password_hash, status, verification_status, is_admin, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (email) DO NOTHING""",
                ("System Administrator", "ADMIN-0001", "Administration", 2000,
                 "admin@slsu.edu.ph", "09000000000",
                 generate_password_hash("admin123"), "approved", "verified", 1, now),
            )

        cur.execute("SELECT COUNT(*) FROM users WHERE email = %s", ("alumni@slsu.edu.ph",))
        if cur.fetchone()[0] == 0:
            cur.execute(
                """INSERT INTO users (full_name, student_number, department, year_graduated,
                   email, contact, password_hash, status, verification_status, is_admin,
                   bio, employment, location, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (email) DO NOTHING""",
                ("Juan Dela Cruz", "2018-0001", "College of Computer Studies", 2018,
                 "alumni@slsu.edu.ph", "09170000000",
                 generate_password_hash("alumni123"), "approved", "verified", 0,
                 "Proud SLSU-JGE graduate.", "Software Engineer at TechCorp", "Manila, Philippines", now),
            )

        cur.execute("SELECT COUNT(*) FROM graduate_master")
        if cur.fetchone()[0] == 0:
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
                cur.execute(
                    """INSERT INTO graduate_master
                       (full_name, student_number, department, year_graduated, date_added, added_by_admin)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (student_number) DO NOTHING""",
                    (name, sn, dept, yr, now, "System Administrator"),
                )

        cur.execute("SELECT COUNT(*) FROM announcements")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO announcements (title, content, created_by, is_published, created_at) VALUES (%s,%s,%s,%s,%s)",
                ("Welcome to the SLSU-JGE Alumni Portal",
                 "This portal connects graduates of Southern Luzon State University - Judge Guillermo Eleazar Campus.",
                 1, 1, now),
            )
        conn.commit()
        cur.close()
        conn.close()
        print("[init_db] Database initialized successfully.")
    except Exception as e:
        print(f"[init_db] WARNING: {e}")

# ---------------------------------------------------------------------------
# Auth / Helpers
# ---------------------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
    return cur.fetchone()

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
    cur = db.cursor()
    cur.execute(
        "INSERT INTO notifications (user_id, type, content, link, is_read, created_at) VALUES (%s,%s,%s,%s,0,%s)",
        (user_id, ntype, content, link, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()

def unread_count():
    user = current_user()
    if not user:
        return 0
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM notifications WHERE user_id = %s AND is_read = 0", (user["id"],))
    row = cur.fetchone()
    return row["c"] if row else 0

def distinct_departments():
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT DISTINCT department FROM graduate_master WHERE department IS NOT NULL AND department != '' ORDER BY department"
    )
    depts = [r["department"] for r in cur.fetchall()]
    for d in DEPARTMENTS:
        if d not in depts:
            depts.append(d)
    return sorted(depts)

def get_user_groups(db, uid):
    cur = db.cursor()
    cur.execute(
        """SELECT g.id, g.name, g.created_by,
           (SELECT message_text FROM group_messages WHERE group_id = g.id ORDER BY sent_at DESC LIMIT 1) AS last_msg,
           (SELECT sent_at FROM group_messages WHERE group_id = g.id ORDER BY sent_at DESC LIMIT 1) AS last_at,
           (SELECT COUNT(*) FROM group_messages gm2 WHERE gm2.group_id = g.id AND gm2.sender_id != %s
              AND gm2.sent_at > COALESCE((SELECT last_read_at FROM group_members WHERE group_id = g.id AND user_id = %s), '0000-01-01')) AS unread
           FROM chat_groups g JOIN group_members gm ON gm.group_id = g.id
           WHERE gm.user_id = %s ORDER BY COALESCE(last_at, '0000-01-01') DESC""",
        (uid, uid, uid),
    )
    return cur.fetchall()

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
        "REACTIONS": REACTIONS,
    }

# ---------------------------------------------------------------------------
# Public Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT a.*, u.full_name AS author FROM announcements a LEFT JOIN users u ON u.id = a.created_by "
        "WHERE a.is_published = 1 ORDER BY a.created_at DESC LIMIT 10"
    )
    announcements = cur.fetchall()
    cur.execute("SELECT COUNT(*) AS c FROM graduate_master"); grad_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE status='approved' AND is_admin=0"); alumni_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM homecoming_events"); event_count = cur.fetchone()["c"]
    stats = {"graduates": grad_count, "alumni": alumni_count, "events": event_count}
    return render_template("index.html", announcements=announcements, stats=stats)

@app.route("/directory")
def directory():
    db = get_db()
    cur = db.cursor()
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "").strip()
    year_from = request.args.get("year_from", "").strip()
    year_to = request.args.get("year_to", "").strip()
    sql = "SELECT * FROM graduate_master WHERE 1=1"
    params = []
    if q:
        sql += " AND (full_name ILIKE %s OR student_number ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    if dept:
        sql += " AND department = %s"; params.append(dept)
    if year_from.isdigit():
        sql += " AND year_graduated >= %s"; params.append(int(year_from))
    if year_to.isdigit():
        sql += " AND year_graduated <= %s"; params.append(int(year_to))
    sql += " ORDER BY year_graduated DESC, full_name ASC LIMIT 500"
    cur.execute(sql, params)
    graduates = cur.fetchall()
    return render_template("directory.html", graduates=graduates, depts=distinct_departments(),
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
        bio = request.form.get("bio", "").strip()  # [NEW v3]
        errors = []
        if not full_name or not student_number or not email or not password:
            errors.append("Full name, student number, email, and password are required.")
        if password and len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if year_graduated and not year_graduated.isdigit():
            errors.append("Year graduated must be a number.")
        # [NEW v3] validate profile picture if uploaded
        profile_pic = _process_profile_pic(request.files.get("profile_pic"))
        if request.files.get("profile_pic") and request.files.get("profile_pic").filename and not profile_pic:
            errors.append("Profile picture must be JPG/PNG/GIF/WEBP, max 5MB.")
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            errors.append("An account with this email already exists.")
        if errors:
            for e in errors: flash(e, "error")
            return render_template("register.html", depts=distinct_departments(), form=request.form)
        cur.execute(
            """SELECT 1 FROM graduate_master WHERE student_number = %s
               AND lower(full_name) = lower(%s) AND year_graduated = %s""",
            (student_number, full_name, int(year_graduated) if year_graduated.isdigit() else -1),
        )
        match = cur.fetchone()
        if match:
            status, verification = "pending_verified", "verified"
            msg = "Your information has been verified. Waiting for admin approval."
        else:
            status, verification = "pending_unverified", "unverified"
            msg = "Your registration will be manually reviewed by an administrator."
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # [NEW v3] INSERT now includes profile_pic + bio
        cur.execute(
            """INSERT INTO users (full_name, student_number, department, year_graduated,
               email, contact, password_hash, status, verification_status, is_admin,
               bio, profile_pic, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)""",
            (full_name, student_number, department,
             int(year_graduated) if year_graduated.isdigit() else None,
             email, contact, generate_password_hash(password), status, verification,
             bio, profile_pic or "", now),
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
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")
        if user["status"] == "pending_verified":
            flash("Your account is verified and awaiting admin approval.", "warning"); return render_template("login.html")
        if user["status"] == "pending_unverified":
            flash("Your registration is under manual review.", "warning"); return render_template("login.html")
        if user["status"] == "rejected":
            flash("Your registration was rejected.", "error"); return render_template("login.html")
        if user["status"] == "deactivated":
            flash("Your account has been deactivated.", "error"); return render_template("login.html")
        session["user_id"] = user["id"]
        session["is_admin"] = bool(user["is_admin"])
        cur.execute("UPDATE users SET last_login = %s WHERE id = %s",
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
# Feed — Media + Reactions
# ---------------------------------------------------------------------------
@app.route("/feed", methods=["GET", "POST"])
@login_required
def feed():
    user = current_user()
    db = get_db()
    cur = db.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        media_file = request.files.get("media")
        media_rec = None
        if media_file and media_file.filename:
            ext = media_file.filename.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_MEDIA:
                flash("Unsupported file. Use JPG/PNG/GIF/WEBP photo or MP4/WEBM video.", "error")
                return redirect(url_for("feed"))
            raw = media_file.read()
            if len(raw) > 50 * 1024 * 1024:
                flash("File too large. Maximum is 50 MB.", "error")
                return redirect(url_for("feed"))
            mime, mtype = ALLOWED_MEDIA[ext]
            media_rec = (mtype, f"data:{mime};base64,{base64.b64encode(raw).decode()}")
        if not content and not media_rec:
            flash("A post needs text, a photo, or a video.", "error")
            return redirect(url_for("feed"))
        # NA-AYOS: RETURNING id (hindi cur.lastrowid!)
        cur.execute(
            "INSERT INTO posts (user_id, content, created_at) VALUES (%s,%s,%s) RETURNING id",
            (user["id"], content, now),
        )
        post_id = cur.fetchone()["id"]
        if media_rec:
            cur.execute(
                "INSERT INTO post_media (post_id, media_type, media_data, created_at) VALUES (%s,%s,%s,%s)",
                (post_id, media_rec[0], media_rec[1], now),
            )
        db.commit()
        return redirect(url_for("feed"))

    cur.execute(
        """SELECT p.*, u.full_name, u.department, u.year_graduated
           FROM posts p JOIN users u ON u.id = p.user_id ORDER BY p.created_at DESC LIMIT 100"""
    )
    posts = cur.fetchall()
    cur.execute("SELECT post_id, reaction_type, user_id FROM post_reactions")
    r_counts, r_mine = {}, {}
    for r in cur.fetchall():
        pid = r["post_id"]
        r_counts.setdefault(pid, {})
        r_counts[pid][r["reaction_type"]] = r_counts[pid].get(r["reaction_type"], 0) + 1
        if r["user_id"] == user["id"]:
            r_mine[pid] = r["reaction_type"]
    cur.execute("SELECT post_id, media_type, media_data FROM post_media")
    media_map = {m["post_id"]: (m["media_type"], m["media_data"]) for m in cur.fetchall()}
    post_data = []
    for p in posts:
        cur.execute(
            """SELECT c.*, u.full_name FROM comments c JOIN users u ON u.id = c.user_id
               WHERE c.post_id = %s ORDER BY c.created_at ASC""", (p["id"],)
        )
        comments = cur.fetchall()
        top = [c for c in comments if c["parent_id"] is None]
        replies = {}
        for c in comments:
            if c["parent_id"]:
                replies.setdefault(c["parent_id"], []).append(c)
        post_data.append((p, top, replies, media_map.get(p["id"]),
                          r_counts.get(p["id"], {}), r_mine.get(p["id"])))
    return render_template("feed.html", post_data=post_data)

@app.route("/post/<int:post_id>/react", methods=["POST"])
@login_required
def react(post_id):
    user = current_user()
    rtype = request.form.get("reaction_type", "like")
    if rtype not in REACTIONS: rtype = "like"
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, reaction_type FROM post_reactions WHERE post_id = %s AND user_id = %s", (post_id, user["id"]))
    existing = cur.fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if existing and existing["reaction_type"] == rtype:
        cur.execute("DELETE FROM post_reactions WHERE id = %s", (existing["id"],))
    else:
        if existing:
            cur.execute("UPDATE post_reactions SET reaction_type = %s, created_at = %s WHERE id = %s", (rtype, now, existing["id"]))
        else:
            cur.execute("INSERT INTO post_reactions (post_id, user_id, reaction_type, created_at) VALUES (%s,%s,%s,%s)",
                        (post_id, user["id"], rtype, now))
        cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
        post = cur.fetchone()
        if post and post["user_id"] != user["id"]:
            notify(post["user_id"], "reaction", f"{user['full_name']} reacted {REACTIONS[rtype]} to your post.", url_for("feed"))
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
        cur = db.cursor()
        cur.execute("INSERT INTO comments (post_id, user_id, parent_id, content, created_at) VALUES (%s,%s,%s,%s,%s)",
                    (post_id, user["id"], parent_id, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
        post = cur.fetchone()
        if post and post["user_id"] != user["id"]:
            notify(post["user_id"], "comment", f"{user['full_name']} commented on your post.", url_for("feed"))
        db.commit()
    return redirect(url_for("feed"))

@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    user = current_user()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
    post = cur.fetchone()
    if post and (post["user_id"] == user["id"] or user["is_admin"]):
        cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
        db.commit()
    return redirect(url_for("feed"))

@app.route("/homecoming")
@login_required
def my_homecoming():
    user = current_user()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM homecoming_events WHERE target_year IS NULL OR target_year = %s ORDER BY event_date ASC",
                (user["year_graduated"],))
    return render_template("homecoming.html", events=cur.fetchall())

@app.route("/alumni")
@login_required
def alumni_directory():
    db = get_db()
    cur = db.cursor()
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "").strip()
    sql = "SELECT * FROM users WHERE status='approved' AND is_admin=0"
    params = []
    if q:
        sql += " AND (full_name ILIKE %s OR student_number ILIKE %s)"; params += [f"%{q}%", f"%{q}%"]
    if dept:
        sql += " AND department = %s"; params.append(dept)
    sql += " ORDER BY full_name ASC LIMIT 500"
    cur.execute(sql, params)
    alumni = cur.fetchall()
    depts = sorted({a["department"] for a in alumni if a["department"]})
    return render_template("alumni_directory.html", alumni=alumni, depts=depts, q=q, dept=dept)

# ---------------------------------------------------------------------------
# Messages — 1-on-1 + Group
# ---------------------------------------------------------------------------
def _direct_conversations(db, uid):
    cur = db.cursor()
    cur.execute("SELECT sender_id, receiver_id, content, created_at FROM messages WHERE sender_id = %s OR receiver_id = %s ORDER BY created_at DESC", (uid, uid))
    last = {}
    for r in cur.fetchall():
        other = r["receiver_id"] if r["sender_id"] == uid else r["sender_id"]
        if other not in last: last[other] = r
    convos = []
    for oid, r in last.items():
        cur.execute("SELECT full_name FROM users WHERE id = %s", (oid,))
        urow = cur.fetchone()
        convos.append({"other_id": oid, "other_name": urow["full_name"] if urow else "Unknown",
                       "last_msg": r["content"], "last_at": r["created_at"]})
    return convos

@app.route("/messages", defaults={"other_id": None}, methods=["GET", "POST"])
@app.route("/messages/<int:other_id>", methods=["GET", "POST"])
@login_required
def messages(other_id):
    user = current_user()
    db = get_db()
    cur = db.cursor()
    if request.method == "POST" and other_id:
        content = request.form.get("content", "").strip()
        if content:
            cur.execute("INSERT INTO messages (sender_id, receiver_id, content, created_at) VALUES (%s,%s,%s,%s)",
                        (user["id"], other_id, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            notify(other_id, "message", f"New message from {user['full_name']}.", url_for("messages", other_id=user["id"]))
            db.commit()
        return redirect(url_for("messages", other_id=other_id))
    convos = _direct_conversations(db, user["id"])
    groups = get_user_groups(db, user["id"])
    thread, other = [], None
    if other_id:
        cur.execute("SELECT * FROM users WHERE id = %s", (other_id,)); other = cur.fetchone()
        cur.execute("""SELECT sender_id, content, created_at FROM messages
                       WHERE (sender_id=%s AND receiver_id=%s) OR (sender_id=%s AND receiver_id=%s) ORDER BY created_at ASC""",
                    (user["id"], other_id, other_id, user["id"]))
        thread = cur.fetchall()
        cur.execute("UPDATE messages SET is_read=1 WHERE sender_id=%s AND receiver_id=%s", (other_id, user["id"]))
        db.commit()
    return render_template("messages.html", convos=convos, groups=groups, thread=thread,
                           other=other, other_id=other_id, group=None, members=None, alumni=None, is_creator=False)

@app.route("/messages/group/new", methods=["GET", "POST"])
@login_required
def group_new():
    user = current_user()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, full_name, department, year_graduated FROM users WHERE status='approved' AND is_admin=0 AND id != %s ORDER BY full_name", (user["id"],))
    alumni = cur.fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        member_ids = request.form.getlist("members")
        if not name:
            flash("Group name is required.", "error"); return redirect(url_for("group_new"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # NA-AYOS: RETURNING id
        cur.execute("INSERT INTO chat_groups (name, created_by, created_at) VALUES (%s,%s,%s) RETURNING id",
                    (name, user["id"], now))
        gid = cur.fetchone()["id"]
        cur.execute("INSERT INTO group_members (group_id, user_id, joined_at, last_read_at) VALUES (%s,%s,%s,%s)",
                    (gid, user["id"], now, now))
        for mid in member_ids:
            try: mid = int(mid)
            except ValueError: continue
            cur.execute("INSERT INTO group_members (group_id, user_id, joined_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (gid, mid, now))
            notify(mid, "group", f"{user['full_name']} added you to the group '{name}'.", url_for("group_thread", gid=gid))
        db.commit()
        flash("Group created.", "success")
        return redirect(url_for("group_thread", gid=gid))
    return render_template("create_group.html", alumni=alumni)

@app.route("/messages/group/<int:gid>", methods=["GET", "POST"])
@login_required
def group_thread(gid):
    user = current_user()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM group_members WHERE group_id = %s AND user_id = %s", (gid, user["id"]))
    if not cur.fetchone(): abort(403)
    cur.execute("SELECT * FROM chat_groups WHERE id = %s", (gid,)); group = cur.fetchone()
    if not group: abort(404)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if request.method == "POST":
        text = request.form.get("content", "").strip()
        if text:
            cur.execute("INSERT INTO group_messages (group_id, sender_id, message_text, sent_at) VALUES (%s,%s,%s,%s)",
                        (gid, user["id"], text, now))
            cur.execute("SELECT user_id FROM group_members WHERE group_id = %s AND user_id != %s", (gid, user["id"]))
            for m in cur.fetchall():
                notify(m["user_id"], "group", f"New message in '{group['name']}' from {user['full_name']}.", url_for("group_thread", gid=gid))
            db.commit()
        return redirect(url_for("group_thread", gid=gid))
    cur.execute("UPDATE group_members SET last_read_at = %s WHERE group_id = %s AND user_id = %s", (now, gid, user["id"]))
    db.commit()
    cur.execute("""SELECT gm.sender_id, gm.message_text AS content, gm.sent_at AS created_at, u.full_name AS sender_name
                   FROM group_messages gm JOIN users u ON u.id = gm.sender_id WHERE gm.group_id = %s ORDER BY gm.sent_at ASC""", (gid,))
    thread = cur.fetchall()
    cur.execute("""SELECT gm.user_id, u.full_name, u.email FROM group_members gm JOIN users u ON u.id = gm.user_id
                   WHERE gm.group_id = %s ORDER BY u.full_name""", (gid,))
    members = cur.fetchall()
    cur.execute("""SELECT id, full_name FROM users WHERE status='approved' AND is_admin=0
                   AND id NOT IN (SELECT user_id FROM group_members WHERE group_id = %s) ORDER BY full_name""", (gid,))
    alumni = cur.fetchall()
    convos = _direct_conversations(db, user["id"])
    groups = get_user_groups(db, user["id"])
    return render_template("messages.html", convos=convos, groups=groups, thread=thread,
                           other=None, other_id=None, group=group, members=members,
                           alumni=alumni, is_creator=(group["created_by"] == user["id"]))

def _group_creator_only(cur, gid, uid):
    cur.execute("SELECT created_by FROM chat_groups WHERE id = %s", (gid,))
    g = cur.fetchone()
    return g and g["created_by"] == uid

@app.route("/messages/group/<int:gid>/add", methods=["POST"])
@login_required
def group_add_member(gid):
    user = current_user(); db = get_db(); cur = db.cursor()
    if not _group_creator_only(cur, gid, user["id"]): abort(403)
    new_id = request.form.get("user_id")
    if new_id and new_id.isdigit():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO group_members (group_id, user_id, joined_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (gid, int(new_id), now))
        cur.execute("SELECT name FROM chat_groups WHERE id = %s", (gid,))
        g = cur.fetchone()
        notify(int(new_id), "group", f"You were added to the group '{g['name']}'.", url_for("group_thread", gid=gid))
        db.commit(); flash("Member added.", "success")
    return redirect(url_for("group_thread", gid=gid))

@app.route("/messages/group/<int:gid>/remove/<int:uid>", methods=["POST"])
@login_required
def group_remove_member(gid, uid):
    user = current_user(); db = get_db(); cur = db.cursor()
    if not _group_creator_only(cur, gid, user["id"]): abort(403)
    cur.execute("SELECT created_by FROM chat_groups WHERE id = %s", (gid,))
    if cur.fetchone()["created_by"] == uid:
        flash("The group creator cannot be removed.", "error")
    else:
        cur.execute("DELETE FROM group_members WHERE group_id = %s AND user_id = %s", (gid, uid))
        db.commit(); flash("Member removed.", "info")
    return redirect(url_for("group_thread", gid=gid))

@app.route("/messages/group/<int:gid>/rename", methods=["POST"])
@login_required
def group_rename(gid):
    user = current_user(); db = get_db(); cur = db.cursor()
    if not _group_creator_only(cur, gid, user["id"]): abort(403)
    new_name = request.form.get("name", "").strip()
    if new_name:
        cur.execute("UPDATE chat_groups SET name = %s WHERE id = %s", (new_name, gid))
        db.commit(); flash("Group renamed.", "success")
    return redirect(url_for("group_thread", gid=gid))

@app.route("/messages/group/<int:gid>/delete", methods=["POST"])
@login_required
def group_delete(gid):
    user = current_user(); db = get_db(); cur = db.cursor()
    if not _group_creator_only(cur, gid, user["id"]): abort(403)
    cur.execute("DELETE FROM chat_groups WHERE id = %s", (gid,))
    db.commit(); flash("Group deleted.", "info")
    return redirect(url_for("messages"))

@app.route("/notifications")
@login_required
def notifications():
    user = current_user(); db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 100", (user["id"],))
    items = cur.fetchall()
    cur.execute("UPDATE notifications SET is_read=1 WHERE user_id = %s", (user["id"],))
    db.commit()
    return render_template("notifications.html", items=items)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user(); db = get_db(); cur = db.cursor()
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()
        employment = request.form.get("employment", "").strip()
        location = request.form.get("location", "").strip()
        contact = request.form.get("contact", "").strip()
        # [NEW v3] profile picture: priority = uploaded file, else keep existing
        uploaded_pic = _process_profile_pic(request.files.get("profile_pic_file"))
        if uploaded_pic:
            profile_pic = uploaded_pic
            flash("Profile picture updated.", "success")
        else:
            # keep existing if no new upload; allow clearing via "remove_pic" checkbox
            if request.form.get("remove_pic"):
                profile_pic = ""
            else:
                profile_pic = user["profile_pic"] or ""
        cur.execute("UPDATE users SET bio=%s, employment=%s, location=%s, contact=%s, profile_pic=%s WHERE id=%s",
                    (bio, employment, location, contact, profile_pic, user["id"]))
        db.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")

# ---------------------------------------------------------------------------
# Admin Panel
# ---------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE status LIKE 'pending_%'"); pending = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE status='approved' AND is_admin=0"); approved = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM graduate_master"); graduates = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM posts"); posts = cur.fetchone()["c"]
    stats = {"pending": pending, "approved": approved, "graduates": graduates, "posts": posts}
    cur.execute("SELECT * FROM users WHERE status LIKE 'pending_%' ORDER BY created_at DESC LIMIT 10")
    recent = cur.fetchall()
    return render_template("admin/dashboard.html", stats=stats, recent=recent)

@app.route("/admin/graduates", methods=["GET", "POST"])
@admin_required
def admin_graduates():
    db = get_db(); cur = db.cursor()
    if request.method == "POST" and request.form.get("action") == "add":
        full_name = request.form.get("full_name","").strip()
        sn = request.form.get("student_number","").strip()
        dept = request.form.get("department","").strip()
        yr = request.form.get("year_graduated","").strip()
        if full_name and sn:
            try:
                cur.execute("""INSERT INTO graduate_master (full_name, student_number, department, year_graduated, date_added, added_by_admin)
                               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (student_number) DO NOTHING""",
                            (full_name, sn, dept, int(yr) if yr.isdigit() else None,
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_user()["full_name"]))
                db.commit(); flash("Graduate added.", "success")
            except Exception as e:
                flash(f"Error: {e}", "error")
        else:
            flash("Full name and student number required.", "error")
        return redirect(url_for("admin_graduates"))
    q = request.args.get("q","").strip()
    sql = "SELECT * FROM graduate_master WHERE 1=1"; params = []
    if q:
        sql += " AND (full_name ILIKE %s OR student_number ILIKE %s)"; params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY year_graduated DESC, full_name ASC LIMIT 500"
    cur.execute(sql, params); graduates = cur.fetchall()
    return render_template("admin/graduates.html", graduates=graduates, q=q, depts=distinct_departments())

@app.route("/admin/graduates/upload", methods=["POST"])
@admin_required
def admin_graduates_upload():
    db = get_db(); cur = db.cursor()
    f = request.files.get("file")
    if not f or not f.filename.endswith(".csv"):
        flash("Please upload a CSV file.", "error"); return redirect(url_for("admin_graduates"))
    try:
        reader = csv.DictReader(io.StringIO(f.read().decode("utf-8-sig")))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); added = 0
        for row in reader:
            name = (row.get("full_name") or row.get("Full Name") or "").strip()
            sn = (row.get("student_number") or row.get("Student Number") or "").strip()
            dept = (row.get("department") or row.get("Department") or "").strip()
            yr = (row.get("year_graduated") or row.get("Year Graduated") or "").strip()
            if name and sn:
                cur.execute("""INSERT INTO graduate_master (full_name, student_number, department, year_graduated, date_added, added_by_admin)
                               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (student_number) DO NOTHING""",
                            (name, sn, dept, int(yr) if str(yr).isdigit() else None, now, current_user()["full_name"]))
                added += 1
        db.commit(); flash(f"Bulk upload complete: {added} record(s) processed.", "success")
    except Exception as e:
        flash(f"Upload failed: {e}", "error")
    return redirect(url_for("admin_graduates"))

@app.route("/admin/graduates/<int:gid>/edit", methods=["POST"])
@admin_required
def admin_graduate_edit(gid):
    db = get_db(); cur = db.cursor()
    cur.execute("UPDATE graduate_master SET full_name=%s, student_number=%s, department=%s, year_graduated=%s WHERE id=%s",
                (request.form.get("full_name","").strip(), request.form.get("student_number","").strip(),
                 request.form.get("department","").strip(),
                 int(request.form["year_graduated"]) if request.form.get("year_graduated","").strip().isdigit() else None, gid))
    db.commit(); flash("Graduate record updated.", "success")
    return redirect(url_for("admin_graduates"))

@app.route("/admin/graduates/<int:gid>/delete", methods=["POST"])
@admin_required
def admin_graduate_delete(gid):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM graduate_master WHERE id = %s", (gid,))
    db.commit(); flash("Graduate record deleted.", "info")
    return redirect(url_for("admin_graduates"))

@app.route("/admin/approvals")
@admin_required
def admin_approvals():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE status LIKE 'pending_%' ORDER BY created_at ASC")
    return render_template("admin/approvals.html", pending=cur.fetchall())

@app.route("/admin/approvals/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_approval_action(uid, action):
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (uid,)); user = cur.fetchone()
    if not user: abort(404)
    if action == "approve":
        cur.execute("UPDATE users SET status='approved' WHERE id=%s", (uid,))
        notify(uid, "approval", "Your alumni account has been approved. Welcome to SLSU-JGE!", url_for("feed"))
        flash(f"Approved {user['full_name']}.", "success")
    elif action == "reject":
        cur.execute("UPDATE users SET status='rejected' WHERE id=%s", (uid,))
        notify(uid, "approval", "Your registration was not approved.", "")
        flash(f"Rejected {user['full_name']}.", "info")
    elif action == "verify":
        cur.execute("UPDATE users SET status='pending_verified', verification_status='verified' WHERE id=%s", (uid,))
        flash(f"Manually verified {user['full_name']}.", "success")
    db.commit()
    return redirect(url_for("admin_approvals"))

@app.route("/admin/users")
@admin_required
def admin_users():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE is_admin=0 ORDER BY created_at DESC")
    return render_template("admin/users.html", users=cur.fetchall())

@app.route("/admin/users/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_user_action(uid, action):
    me = current_user()
    if uid == me["id"]:
        flash("You cannot modify your own account.", "error"); return redirect(url_for("admin_users"))
    db = get_db(); cur = db.cursor()
    if action == "deactivate":
        cur.execute("UPDATE users SET status='deactivated' WHERE id=%s", (uid,)); flash("Account deactivated.", "info")
    elif action == "activate":
        cur.execute("UPDATE users SET status='approved' WHERE id=%s", (uid,)); flash("Account activated.", "success")
    db.commit()
    return redirect(url_for("admin_users"))

@app.route("/admin/homecoming", methods=["GET", "POST"])
@admin_required
def admin_homecoming():
    db = get_db(); cur = db.cursor()
    if request.method == "POST":
        title = request.form.get("title","").strip()
        target_year = request.form.get("target_year","").strip()
        event_date = request.form.get("event_date","").strip()
        venue = request.form.get("venue","").strip()
        deadline = request.form.get("registration_deadline","").strip()
        description = request.form.get("description","").strip()
        if title:
            cur.execute("""INSERT INTO homecoming_events (title, target_year, event_date, venue, registration_deadline, description, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (title, int(target_year) if target_year.isdigit() else None,
                         event_date or None, venue, deadline or None, description,
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            db.commit(); flash("Homecoming event added.", "success")
        return redirect(url_for("admin_homecoming"))
    cur.execute("SELECT * FROM homecoming_events ORDER BY event_date DESC")
    return render_template("admin/homecoming.html", events=cur.fetchall())

@app.route("/admin/homecoming/<int:eid>/delete", methods=["POST"])
@admin_required
def admin_homecoming_delete(eid):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM homecoming_events WHERE id = %s", (eid,))
    db.commit(); flash("Event deleted.", "info")
    return redirect(url_for("admin_homecoming"))

@app.route("/admin/announcements", methods=["GET", "POST"])
@admin_required
def admin_announcements():
    db = get_db(); cur = db.cursor(); me = current_user()
    if request.method == "POST":
        title = request.form.get("title","").strip(); content = request.form.get("content","").strip()
        if title and content:
            cur.execute("INSERT INTO announcements (title, content, created_by, is_published, created_at) VALUES (%s,%s,%s,1,%s)",
                        (title, content, me["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            db.commit()
            cur.execute("SELECT id FROM users WHERE status='approved' AND is_admin=0")
            for a in cur.fetchall():
                notify(a["id"], "announcement", f"New announcement: {title}", url_for("index"))
            flash("Announcement published and alumni notified.", "success")
        return redirect(url_for("admin_announcements"))
    cur.execute("SELECT a.*, u.full_name AS author FROM announcements a LEFT JOIN users u ON u.id = a.created_by ORDER BY a.created_at DESC")
    return render_template("admin/announcements.html", items=cur.fetchall())

@app.route("/admin/announcements/<int:aid>/delete", methods=["POST"])
@admin_required
def admin_announcement_delete(aid):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM announcements WHERE id = %s", (aid,))
    db.commit(); flash("Announcement deleted.", "info")
    return redirect(url_for("admin_announcements"))

@app.route("/api/verify")
def api_verify():
    name = request.args.get("name","").strip(); sn = request.args.get("student_number","").strip()
    year = request.args.get("year","").strip()
    db = get_db(); cur = db.cursor()
    cur.execute("""SELECT 1 FROM graduate_master WHERE student_number=%s AND lower(full_name)=lower(%s) AND year_graduated=%s""",
                (sn, name, int(year) if year.isdigit() else -1))
    return jsonify({"verified": bool(cur.fetchone())})

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You do not have permission to access this page."), 403

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404

# Initialize DB on startup (safe — hindi nag-crash kahit may error)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
