import os
import re
import mysql.connector
from datetime import datetime, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ---------------------------------------------------
# ENVIRONMENT-BASED CONFIG
# ---------------------------------------------------
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "YOUR_SUPER_SECRET_KEY")

MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", 3306))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASS = os.environ.get("MYSQL_PASS", "")
MYSQL_DB   = os.environ.get("MYSQL_DB", "socialdb")

BAD_WORDS_FILE = "bad_words.txt"
MAX_WORDS      = 50

UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mov", ".avi"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ---------------------------------------------------
# OFFENSIVE WORDS
# ---------------------------------------------------
def load_offensive_words():
    words = set()
    try:
        with open(BAD_WORDS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                w = line.strip().lower()
                if w:
                    words.add(w)
    except Exception as e:
        print(f"Warning: Could not load {BAD_WORDS_FILE}: {e}")
    return words

OFFENSIVE_WORDS = load_offensive_words()

def contains_offensive(text):
    wordlist = re.findall(r"[a-zA-Z0-9]+", text.lower())
    for w in wordlist:
        if w in OFFENSIVE_WORDS:
            return True
    return False

def censor_offensive(text):
    def replace_offensive(m):
        wd = m.group(0)
        if wd.lower() in OFFENSIVE_WORDS:
            return "****"
        return wd
    return re.sub(r"[a-zA-Z0-9]+", replace_offensive, text, flags=re.IGNORECASE)

# ---------------------------------------------------
# DB UTIL
# ---------------------------------------------------
def get_db_connection(database=None):
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=database
    )

def init_db():
    """
    Creates the `socialdb` if not exists, ensures tables exist with ON DELETE CASCADE for comments->posts.
    """
    # Step A: create DB if not exists
    conn = get_db_connection(None)
    cur  = conn.cursor()
    cur.execute("CREATE DATABASE IF NOT EXISTS socialdb")
    conn.commit()
    cur.close()
    conn.close()

    # Step B: create tables
    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor()
    # users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            profile_picture VARCHAR(255),
            bio TEXT
        ) ENGINE=InnoDB
    """)
    # posts
    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            content TEXT,
            media_filename VARCHAR(255),
            created_at DATETIME NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        ) ENGINE=InnoDB
    """)
    # stories
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            media_filename VARCHAR(255),
            created_at DATETIME NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        ) ENGINE=InnoDB
    """)
    # likes
    cur.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            post_id INT NOT NULL,
            user_id INT NOT NULL
        ) ENGINE=InnoDB
    """)
    # saved_posts
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saved_posts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            post_id INT NOT NULL
        ) ENGINE=InnoDB
    """)
    # messages
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sender_id INT NOT NULL,
            recipient_id INT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME NOT NULL
        ) ENGINE=InnoDB
    """)
    # comments with ON DELETE CASCADE
    cur.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            post_id INT NOT NULL,
            user_id INT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id)
        ) ENGINE=InnoDB
    """)
    # follows
    cur.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            follower_id INT NOT NULL,
            followee_id INT NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY(follower_id, followee_id),
            FOREIGN KEY(follower_id) REFERENCES users(id),
            FOREIGN KEY(followee_id) REFERENCES users(id)
        ) ENGINE=InnoDB
    """)
    # notifications
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            message TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        ) ENGINE=InnoDB
    """)

    conn.commit()
    cur.close()
    conn.close()

def ensure_admin_exists():
    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username=%s", ("admin",))
    row  = cur.fetchone()
    if not row:
        hashed_pass = generate_password_hash("123")
        cur.execute("""INSERT INTO users (username, password_hash)
                       VALUES (%s, %s)""", ("admin", hashed_pass))
        conn.commit()
    cur.close()
    conn.close()

def get_current_user_id():
    return session.get("user_id")

def get_user_by_username(username):
    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user

# ---------------------------------------------------
# FLASK APP ROUTES
# ---------------------------------------------------
@app.route("/")
def home():
    if get_current_user_id():
        return redirect(url_for("feed"))
    return redirect(url_for("login"))

@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method=="POST":
        username = request.form["username"]
        password = request.form["password"]

        if contains_offensive(username):
            flash("Offensive/slur in username. Please choose another.", "error")
            return redirect(url_for("signup"))

        pwd_hash = generate_password_hash(password)
        conn = get_db_connection(MYSQL_DB)
        cur  = conn.cursor()
        try:
            cur.execute("""INSERT INTO users (username, password_hash)
                           VALUES (%s,%s)""", (username, pwd_hash))
            conn.commit()
            flash("Sign-up successful! Please log in.", "success")
            return redirect(url_for("login"))
        except mysql.connector.Error as e:
            if e.errno == 1062:
                flash("Username already exists!", "error")
            else:
                flash(f"MySQL Error: {e}", "error")
        finally:
            cur.close()
            conn.close()
    return render_template("signup.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db_connection(MYSQL_DB)
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            flash("Welcome back!", "success")
            return redirect(url_for("feed"))
        else:
            flash("Invalid username or password!", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("login"))

# =============== FEED + POST ===============
@app.route("/feed", methods=["GET","POST"])
def feed():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)

    if request.method == "POST":
        content = request.form.get("content","").strip()
        wc = len(content.split())
        if wc > MAX_WORDS:
            flash(f"Post max {MAX_WORDS} words. You used {wc}.", "error")
            return redirect(url_for("feed"))

        content = censor_offensive(content)

        media_file = request.files.get("media_file")
        media_filename = None
        if media_file and media_file.filename:
            ext = os.path.splitext(media_file.filename)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                fn = secure_filename(media_file.filename)
                media_file.save(os.path.join(app.config["UPLOAD_FOLDER"], fn))
                media_filename = fn

        if content or media_filename:
            now = datetime.now()
            cur.execute("""INSERT INTO posts (user_id,content,media_filename,created_at)
                           VALUES (%s,%s,%s,%s)""",(user_id, content, media_filename, now))
            conn.commit()

    # stories
    cutoff = datetime.now() - timedelta(hours=24)
    cur.execute("""
        SELECT s.id, s.media_filename, s.created_at,
               u.username, u.profile_picture
        FROM stories s
        JOIN users u ON s.user_id = u.id
        WHERE s.created_at >= %s
        ORDER BY s.created_at DESC
    """, (cutoff,))
    stories = cur.fetchall()

    # posts
    cur.execute("""
        SELECT p.id, p.user_id, p.content, p.media_filename, p.created_at,
               u.username, u.profile_picture
        FROM posts p
        JOIN users u ON p.user_id=u.id
        ORDER BY p.created_at DESC
    """)
    raw_posts = cur.fetchall()

    # load each post's like/save data + comments
    posts = []
    for p in raw_posts:
        post_id = p["id"]
        cur.execute("SELECT COUNT(*) as c FROM likes WHERE post_id=%s",(post_id,))
        lr = cur.fetchone()
        like_count = lr["c"] if lr else 0

        cur.execute("SELECT id FROM likes WHERE post_id=%s AND user_id=%s",(post_id, user_id))
        rlike = cur.fetchone()
        user_has_liked = True if rlike else False

        cur.execute("SELECT id FROM saved_posts WHERE post_id=%s AND user_id=%s",(post_id, user_id))
        rsave = cur.fetchone()
        user_has_saved = True if rsave else False

        # get comments
        cur.execute("""
            SELECT c.*, u.username, u.profile_picture
            FROM comments c
            JOIN users u ON c.user_id=u.id
            WHERE c.post_id=%s
            ORDER BY c.created_at ASC
        """,(post_id,))
        comment_rows = cur.fetchall()

        posts.append({
            "id": p["id"],
            "user_id": p["user_id"],
            "content": p["content"],
            "media_filename": p["media_filename"],
            "created_at": p["created_at"],
            "username": p["username"],
            "profile_picture": p["profile_picture"],
            "like_count": like_count,
            "user_has_liked": user_has_liked,
            "user_has_saved": user_has_saved,
            "comments": comment_rows
        })

    cur.close()
    conn.close()
    return render_template("feed.html", stories=stories, posts=posts, current_user_id=user_id)

@app.route("/like_api/<int:post_id>", methods=["POST"])
def like_api(post_id):
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"error":"Not logged in"}), 403

    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM likes WHERE post_id=%s AND user_id=%s",(post_id, user_id))
    row = cur.fetchone()
    if row:
        cur.execute("DELETE FROM likes WHERE id=%s",(row["id"],))
        action = "unliked"
    else:
        cur.execute("INSERT INTO likes (post_id,user_id) VALUES(%s,%s)",(post_id, user_id))
        action = "liked"
    conn.commit()

    cur.execute("SELECT COUNT(*) as c FROM likes WHERE post_id=%s",(post_id,))
    r = cur.fetchone()
    like_count = r["c"] if r else 0

    cur.close()
    conn.close()
    return jsonify({"status": action, "like_count": like_count})

@app.route("/save_api/<int:post_id>", methods=["POST"])
def save_api(post_id):
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"error":"Not logged in"}),403

    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM saved_posts WHERE post_id=%s AND user_id=%s",(post_id, user_id))
    row = cur.fetchone()
    if row:
        cur.execute("DELETE FROM saved_posts WHERE id=%s",(row["id"],))
        action = "unsaved"
    else:
        cur.execute("INSERT INTO saved_posts (post_id,user_id) VALUES(%s,%s)",(post_id, user_id))
        action = "saved"
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": action})

@app.route("/delete_post/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    user_id = get_current_user_id()
    if not user_id:
        flash("Must be logged in to delete posts","error")
        return redirect(url_for("login"))

    conn = get_db_connection(MYSQL_DB)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT user_id FROM posts WHERE id=%s",(post_id,))
    row = cur.fetchone()
    if not row:
        flash("Post not found!","error")
        cur.close()
        conn.close()
        return redirect(url_for("feed"))

    current_username = session.get("username")
    is_admin = (current_username=="admin")

    if is_admin or (row["user_id"]==user_id):
        # comments are removed automatically due to ON DELETE CASCADE
        cur.execute("DELETE FROM likes WHERE post_id=%s",(post_id,))
        cur.execute("DELETE FROM saved_posts WHERE post_id=%s",(post_id,))
        cur.execute("DELETE FROM posts WHERE id=%s",(post_id,))
        conn.commit()
        flash("Post deleted!","success")
    else:
        flash("Cannot delete others' post!","error")

    cur.close()
    conn.close()
    return redirect(url_for("feed"))

@app.route("/upload_story", methods=["POST"])
def upload_story():
    user_id = get_current_user_id()
    if not user_id:
        flash("Must be logged in to upload story","error")
        return redirect(url_for("login"))

    story_file = request.files.get("story_file")
    if story_file and story_file.filename:
        ext = os.path.splitext(story_file.filename)[1].lower()
        if ext in ALLOWED_EXTENSIONS:
            fn = secure_filename(story_file.filename)
            story_file.save(os.path.join(app.config["UPLOAD_FOLDER"], fn))
            conn = get_db_connection(MYSQL_DB)
            cur = conn.cursor()
            now = datetime.now()
            cur.execute("""INSERT INTO stories (user_id,media_filename,created_at)
                           VALUES(%s,%s,%s)""", (user_id, fn, now))
            conn.commit()
            cur.close()
            conn.close()
            flash("Story uploaded!","success")
        else:
            flash("Invalid file extension for story","error")
    else:
        flash("No file selected or invalid filename","error")

    return redirect(url_for("feed"))

# =============== COMMENTS ===============
@app.route("/comment_api/<int:post_id>", methods=["POST"])
def add_comment_api(post_id):
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"error":"Not logged in"}),403

    content = request.form.get("comment_content","").strip()
    if not content:
        return jsonify({"error":"Comment is empty"}),400
    wc = len(content.split())
    if wc > MAX_WORDS:
        return jsonify({"error":f"Comment must be <= {MAX_WORDS} words"}),400

    content = censor_offensive(content)
    conn = get_db_connection(MYSQL_DB)
    cur = conn.cursor(dictionary=True)
    now = datetime.now()
    cur.execute("""INSERT INTO comments (post_id,user_id,content,created_at)
                   VALUES(%s,%s,%s,%s)""",(post_id,user_id,content,now))
    conn.commit()
    cid = cur.lastrowid

    # fetch the inserted row w/ user info
    cur.execute("""
        SELECT c.*, u.username, u.profile_picture
        FROM comments c
        JOIN users u ON c.user_id=u.id
        WHERE c.id=%s
    """,(cid,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    # Return the comment info
    new_comment = {
        "id": row["id"],
        "post_id": row["post_id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "profile_picture": row["profile_picture"],
        "content": row["content"],
        "created_at": str(row["created_at"])
    }
    return jsonify({"comment": new_comment})

@app.route("/delete_comment/<int:comment_id>", methods=["POST"])
def delete_comment(comment_id):
    user_id = get_current_user_id()
    if not user_id:
        flash("Login required to delete comment","error")
        return redirect(url_for("login"))

    conn = get_db_connection(MYSQL_DB)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT user_id,post_id FROM comments WHERE id=%s",(comment_id,))
    row = cur.fetchone()
    if not row:
        flash("Comment not found!","error")
        cur.close()
        conn.close()
        return redirect(url_for("feed"))

    current_username = session.get("username")
    is_admin = (current_username=="admin")

    if is_admin or (row["user_id"]==user_id):
        cur.execute("DELETE FROM comments WHERE id=%s",(comment_id,))
        conn.commit()
        flash("Comment deleted!","success")
    else:
        flash("Cannot delete others' comment!","error")

    cur.close()
    conn.close()
    return redirect(url_for("feed"))

# =============== MESSAGES ===============
@app.route("/messages")
def messages_list():
    """Show conversation partners (other users you've messaged)."""
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)
    # Also fetch partner's profile_picture
    cur.execute("""
        SELECT DISTINCT u.id, u.username, u.profile_picture
        FROM messages m
        JOIN users u ON (u.id=m.sender_id OR u.id=m.recipient_id)
        WHERE (m.sender_id=%s OR m.recipient_id=%s)
          AND u.id<>%s
    """,(user_id, user_id, user_id))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    conversation_partners = rows
    return render_template("messages.html",
                           conversation_partners=conversation_partners)

@app.route("/messages/<username>", methods=["GET","POST"])
def direct_messages(username):
    """Show a conversation with a specific user, plus a form to send new messages."""
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    other_user = get_user_by_username(username)
    if not other_user:
        flash("User does not exist!", "error")
        return redirect(url_for("messages_list"))

    other_id = other_user["id"]

    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)

    # If user sends a new message
    if request.method=="POST":
        content = request.form.get("content","").strip()
        if content:
            wc = len(content.split())
            if wc>MAX_WORDS:
                flash(f"Message up to {MAX_WORDS} words. You used {wc}.","error")
                return redirect(url_for("direct_messages",username=username))
            content = censor_offensive(content)
            now = datetime.now()
            cur.execute("""INSERT INTO messages (sender_id,recipient_id,content,created_at)
                           VALUES (%s, %s, %s, %s)""",(user_id,other_id,content,now))
            conn.commit()

    # Now fetch the conversation, including each sender's profile_picture
    cur.execute("""
        SELECT m.*,
               s.username AS sender_name,
               s.profile_picture AS sender_profile_picture,
               r.username AS recipient_name,
               r.profile_picture AS recipient_profile_picture
        FROM messages m
        JOIN users s ON s.id = m.sender_id
        JOIN users r ON r.id = m.recipient_id
        WHERE (m.sender_id=%s AND m.recipient_id=%s)
           OR (m.sender_id=%s AND m.recipient_id=%s)
        ORDER BY m.created_at ASC
    """,(user_id,other_id,other_id,user_id))
    msgs = cur.fetchall()
    cur.close()
    conn.close()

    messages_list = []
    for msg in msgs:
        messages_list.append({
            "id": msg["id"],
            "content": msg["content"],
            "created_at": str(msg["created_at"]),
            "sender_id": msg["sender_id"],
            "sender_name": msg["sender_name"],
            "sender_profile_picture": msg["sender_profile_picture"],
            "recipient_id": msg["recipient_id"],
            "recipient_name": msg["recipient_name"],
            "recipient_profile_picture": msg["recipient_profile_picture"]
        })

    return render_template("messages.html",
                           conversation=True,
                           other_user=other_user,
                           messages_list=messages_list)

@app.route("/messages_api/<username>")
def messages_api(username):
    """Returns JSON of messages for auto-refresh in the front end."""
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"error":"Not logged in"}),403

    other_user = get_user_by_username(username)
    if not other_user:
        return jsonify({"error":"User not found"}),404

    other_id = other_user["id"]

    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)

    # Same join for sender's pfp
    cur.execute("""
        SELECT m.*,
               s.username AS sender_name,
               s.profile_picture AS sender_profile_picture,
               r.username AS recipient_name,
               r.profile_picture AS recipient_profile_picture
        FROM messages m
        JOIN users s ON s.id = m.sender_id
        JOIN users r ON r.id = m.recipient_id
        WHERE (m.sender_id=%s AND m.recipient_id=%s)
           OR (m.sender_id=%s AND m.recipient_id=%s)
        ORDER BY m.created_at ASC
    """,(user_id,other_id,other_id,user_id))
    msgs = cur.fetchall()
    cur.close()
    conn.close()

    data = []
    for msg in msgs:
        data.append({
            "id": msg["id"],
            "content": msg["content"],
            "created_at": str(msg["created_at"]),
            "sender_id": msg["sender_id"],
            "sender_name": msg["sender_name"],
            "sender_profile_picture": msg["sender_profile_picture"],
            "recipient_id": msg["recipient_id"],
            "recipient_name": msg["recipient_name"]
        })
    return jsonify({"messages": data})

# =============== PROFILE ===============
@app.route("/profile", methods=["GET","POST"])
def profile():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    return _edit_profile_logic(user_id, is_admin=False)

@app.route("/admin_edit/<int:target_user_id>", methods=["GET","POST"])
def admin_edit_profile(target_user_id):
    if session.get("username")!="admin":
        flash("Access denied. Admin only.","error")
        return redirect(url_for("feed"))
    return _edit_profile_logic(target_user_id, is_admin=True)

def _edit_profile_logic(target_user_id, is_admin=False):
    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id=%s",(target_user_id,))
    user = cur.fetchone()
    if not user:
        cur.close()
        conn.close()
        flash("User does not exist!","error")
        return redirect(url_for("feed"))

    if request.method=="POST":
        new_bio = request.form.get("bio","")
        pfp_file = request.files.get("profile_picture")
        if pfp_file and pfp_file.filename:
            ext = os.path.splitext(pfp_file.filename)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                fn = secure_filename(pfp_file.filename)
                pfp_file.save(os.path.join(app.config["UPLOAD_FOLDER"], fn))
                cur.execute("UPDATE users SET profile_picture=%s WHERE id=%s",(fn,target_user_id))

        cur.execute("UPDATE users SET bio=%s WHERE id=%s",(new_bio,target_user_id))
        conn.commit()
        flash("Profile updated!","success")

        # reload user
        cur.execute("SELECT * FROM users WHERE id=%s",(target_user_id,))
        user = cur.fetchone()

    # user posts
    cur.execute("""
        SELECT p.*, u.username, u.profile_picture
        FROM posts p
        JOIN users u ON p.user_id=u.id
        WHERE p.user_id=%s
        ORDER BY p.created_at DESC
    """,(target_user_id,))
    raw_posts = cur.fetchall()

    posts = []
    for p in raw_posts:
        cur.execute("SELECT COUNT(*) as c FROM likes WHERE post_id=%s",(p["id"],))
        lr = cur.fetchone()
        like_count = lr["c"] if lr else 0
        posts.append({
            "id": p["id"],
            "content": p["content"],
            "media_filename": p["media_filename"],
            "created_at": p["created_at"],
            "username": p["username"],
            "profile_picture": p["profile_picture"],
            "like_count": like_count
        })

    # saved
    cur.execute("""
        SELECT p.*, u.username, u.profile_picture
        FROM saved_posts s
        JOIN posts p ON p.id=s.post_id
        JOIN users u ON p.user_id=u.id
        WHERE s.user_id=%s
        ORDER BY p.created_at DESC
    """,(target_user_id,))
    raw_saved = cur.fetchall()

    saved_posts = []
    for sp in raw_saved:
        cur.execute("SELECT COUNT(*) as c FROM likes WHERE post_id=%s",(sp["id"],))
        lr = cur.fetchone()
        lc = lr["c"] if lr else 0
        saved_posts.append({
            "id": sp["id"],
            "content": sp["content"],
            "media_filename": sp["media_filename"],
            "created_at": sp["created_at"],
            "username": sp["username"],
            "profile_picture": sp["profile_picture"],
            "like_count": lc
        })

    user_post_count = len(posts)
    cur.close()
    conn.close()

    return render_template("profile.html",
                           user=user,
                           posts=posts,
                           saved_posts=saved_posts,
                           user_post_count=user_post_count,
                           is_admin_edit=is_admin)

@app.route("/user/<username>")
def user_profile(username):
    conn = get_db_connection(MYSQL_DB)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE username=%s",(username,))
    user = cur.fetchone()
    if not user:
        cur.close()
        conn.close()
        flash("User does not exist!","error")
        return redirect(url_for("feed"))

    cur.execute("""
        SELECT p.*, u.username, u.profile_picture
        FROM posts p
        JOIN users u ON p.user_id=u.id
        WHERE u.username=%s
        ORDER BY p.created_at DESC
    """,(username,))
    raw_posts = cur.fetchall()
    cur.close()
    conn.close()

    posts = []
    for p in raw_posts:
        posts.append({
            "id": p["id"],
            "content": p["content"],
            "media_filename": p["media_filename"],
            "created_at": p["created_at"],
            "username": p["username"],
            "profile_picture": p["profile_picture"]
        })
    user_post_count = len(posts)

    return render_template("user_profile.html",
                           user=user,
                           posts=posts,
                           user_post_count=user_post_count)

@app.route("/uploads/<filename>")
def uploads(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# ---------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------
if __name__=="__main__":
    # Initialize DB & ensure admin user only when running directly
    init_db()
    ensure_admin_exists()
    app.run(host="0.0.0.0", port=5001, debug=True)
