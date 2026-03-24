
from flask import Flask, request, redirect, send_from_directory
import sqlite3
import hashlib
import secrets
import os

app = Flask(__name__)

DB = "minituber.db"
VIDEO_FOLDER = "videos"
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

os.makedirs(VIDEO_FOLDER, exist_ok=True)

# ---------- DB ----------
def get_db():
    return sqlite3.connect(DB, timeout=10, check_same_thread=False)


def init_db():
    with get_db() as db:
        c = db.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            password TEXT,
            approved INTEGER DEFAULT 0,
            activation_key TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            video TEXT
        )
        """)


# ---------- UTIL ----------
def sanitize(text):
    return text.replace("<", "").replace(">", "")


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def generate_key():
    return secrets.token_hex(16)


# ---------- USERS ----------
def create_user(url, password):
    key = generate_key()

    with get_db() as db:
        c = db.cursor()
        c.execute(
            "INSERT INTO users (url, password, approved, activation_key) VALUES (?, ?, 0, ?)",
            (url, hash_password(password), key)
        )
        user_id = c.lastrowid

    link = f"http://127.0.0.1:5000/activate/{user_id}/{key}"

    with open("approve.txt", "a") as f:
        f.write(f"{url}|||{link}\n")


def check_user(url, password):
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT id, password, approved FROM users WHERE url=?", (url,))
        row = c.fetchone()

    if row:
        if row[1] != hash_password(password):
            return "wrong_pass", None
        if row[2] == 0:
            return "not_approved", None
        return "ok", row[0]

    return "not_exist", None


def get_all_users():
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT id, url FROM users WHERE approved=1")
        return c.fetchall()


# ---------- VIDEO ----------
def save_video(file):
    if file:
        name = file.filename.lower()

        if not (name.endswith(".mp4") or name.endswith(".webm")):
            return None

        data = file.read()

        if len(data) > MAX_FILE_SIZE:
            return None

        with get_db() as db:
            c = db.cursor()
            c.execute("SELECT MAX(id) FROM posts")
            max_id = c.fetchone()[0]
            vid_id = (max_id or 0) + 1

        ext = name.split(".")[-1]
        filename = f"{vid_id}.{ext}"

        path = os.path.join(VIDEO_FOLDER, filename)
        with open(path, "wb") as f:
            f.write(data)

        return filename

    return None


@app.route("/videos/<filename>")
def get_video(filename):
    return send_from_directory(VIDEO_FOLDER, filename)


# ---------- POSTS ----------
def save_post(user_id, message, video):
    with get_db() as db:
        c = db.cursor()
        c.execute(
            "INSERT INTO posts (user_id, message, video) VALUES (?, ?, ?)",
            (user_id, message, video)
        )


def load_posts(user_id, page, per_page=5):
    offset = (page - 1) * per_page

    with get_db() as db:
        c = db.cursor()
        c.execute(
            "SELECT message, video FROM posts WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, per_page, offset)
        )
        return c.fetchall()


def count_posts(user_id):
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT COUNT(*) FROM posts WHERE user_id=?", (user_id,))
        return c.fetchone()[0]


# ---------- ROUTES ----------

# 🏠 HOME
@app.route("/")
def home():
    users = get_all_users()

    html = """
    <body style="background:black;color:white;font-family:Arial;">
    <h1>MiniTuber</h1>
    <a href="/register">➕ Registar</a>
    <h2>Canais</h2>
    """

    for uid, url in users:
        html += f'<a href="/user/{uid}">{url}</a><br>'

    html += "</body>"
    return html


# 📝 REGISTER
@app.route("/register", methods=["GET", "POST"])
def register():
    msg = ""

    if request.method == "POST":
        url = sanitize(request.form.get("url", ""))
        password = request.form.get("password", "")

        if url and password:
            try:
                create_user(url, password)
                msg = "Registado! Aguarda aprovação."
            except:
                msg = "Já existe"

    return f"""
    <body style="background:black;color:white;">
    <a href="/">⬅</a>
    <h2>Registar</h2>
    <form method="POST">
        <input name="url"><br>
        <input type="password" name="password"><br>
        <button>Registar</button>
    </form>
    <p>{msg}</p>
    </body>
    """


# 🔗 ACTIVATE
@app.route("/activate/<int:user_id>/<key>")
def activate(user_id, key):
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT activation_key FROM users WHERE id=?", (user_id,))
        row = c.fetchone()

        if row and row[0] == key:
            c.execute("UPDATE users SET approved=1 WHERE id=?", (user_id,))
            db.commit()
            return "Conta ativada!"

    return "Link inválido"


# 👤 USER PAGE (canal)
@app.route("/user/<int:user_id>", methods=["GET", "POST"])
def user_page(user_id):
    page = request.args.get("page", 1, type=int)
    error = ""

    if request.method == "POST":
        url = sanitize(request.form.get("url", ""))
        msg = sanitize(request.form.get("message", ""))
        password = request.form.get("password", "")
        file = request.files.get("video")

        if url and msg and password:
            res, uid = check_user(url, password)

            if res == "ok":
                if uid != user_id:
                    error = "❌ Só podes postar no teu canal!"
                else:
                    vid = save_video(file)
                    save_post(user_id, msg, vid)
                    return redirect(f"/user/{user_id}?page={page}")

            elif res == "wrong_pass":
                error = "Password errada"
            elif res == "not_approved":
                error = "Conta não ativada"
            else:
                error = "User não existe"

    posts = load_posts(user_id, page)
    total = count_posts(user_id)
    total_pages = (total + 4) // 5 if total else 1

    html = f"""
    <body style="background:black;color:white;">
    <a href="/">⬅ Voltar</a>

    <h2>Canal #{user_id}</h2>

    <form method="POST" enctype="multipart/form-data">
        <input name="url"><br>
        <input type="password" name="password"><br>
        <textarea name="message"></textarea><br>
        <input type="file" name="video"><br>
        <button>Upload</button>
    </form>

    <p>{error}</p>
    <hr>
    """

    for msg, vid in posts:
        html += f"<p>{msg}</p>"
        if vid:
            html += f"""
            <video width="400" controls>
                <source src="/videos/{vid}">
            </video><br>
            <a href="/videos/{vid}" download>⬇ Download</a>
            """
        html += "<hr>"

    html += f"Página {page}/{total_pages}<br>"

    if page > 1:
        html += f'<a href="/user/{user_id}?page={page-1}">⬅</a> '
    if page < total_pages:
        html += f'<a href="/user/{user_id}?page={page+1}">➡</a>'

    html += "</body>"
    return html


# ---------- START ----------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, use_reloader=False)