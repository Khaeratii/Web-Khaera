import json
import logging
import os
import re
import sqlite3
import uuid
from urllib import error as urlerror
from urllib import request as urlrequest
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from config import AIRA_SYSTEM_PROMPT, DEEPSEEK_API_KEY, DEEPSEEK_API_URL, DEEPSEEK_MODEL, SECRET_KEY

# ============================================================
# KONFIGURASI PATH (Vercel-friendly)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

# Di Vercel, filesystem read-only kecuali /tmp
# Pakai /tmp untuk SQLite supaya bisa ditulis
IS_VERCEL = os.getenv("VERCEL") == "1"
if IS_VERCEL:
    DATABASE_PATH = "/tmp/users.db"
else:
    DATABASE_PATH = str(BASE_DIR / "users.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
CORS(app, supports_credentials=True)
logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    connection = db()
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        created_at TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'A2',
        level_label TEXT NOT NULL DEFAULT 'Elementary',
        overall_score INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        deleted_at TEXT,
        topic TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS message_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        conversation_id TEXT NOT NULL,
        grammar_issues TEXT NOT NULL DEFAULT '[]',
        naturalness_issues TEXT NOT NULL DEFAULT '[]',
        unique_words INTEGER NOT NULL DEFAULT 0,
        vocabulary_opportunities TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY(message_id) REFERENCES messages(id)
    );
    CREATE TABLE IF NOT EXISTS learner_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        pattern_type TEXT NOT NULL,
        category TEXT NOT NULL,
        example TEXT NOT NULL,
        correction TEXT NOT NULL,
        frequency INTEGER NOT NULL DEFAULT 1,
        UNIQUE(user_id, category, example),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS session_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        analysis_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    connection.commit()
    connection.close()


def current_user(connection):
    user_id = session.get("user_id")
    if not user_id:
        return None
    return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def require_user():
    connection = db()
    user = current_user(connection)
    if user is None:
        connection.close()
        return None, jsonify({"error": "Authentication required"}), 401
    return (connection, user, None)


def active_conversation(connection, conversation_id, user_id):
    return connection.execute(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (conversation_id, user_id),
    ).fetchone()


def word_list(text):
    return re.findall(r"[A-Za-z']+", text.lower())


def analyze_user_message(content):
    words = word_list(content)
    grammar_issues = []
    lower = content.lower()
    if re.search(r"\b(i go|i eat|i see|i have)\b.*\b(yesterday|last week|last night)\b", lower):
        grammar_issues.append({"category": "past_tense", "example": content, "correction": "Use the past tense for finished actions."})
    if re.search(r"\bi am agree\b", lower):
        grammar_issues.append({"category": "verb_pattern", "example": content, "correction": "Say 'I agree', not 'I am agree'."})
    return {
        "grammar_issues": grammar_issues,
        "naturalness_issues": [],
        "unique_words": len(set(words)),
        "vocabulary_opportunities": [],
    }


def demo_reply(history, content):
    lower = content.lower().strip()
    previous_user_message = next(
        (item["content"] for item in reversed(history) if item["role"] == "user"),
        "",
    )
    previous_assistant_message = next(
        (item["content"] for item in reversed(history) if item["role"] == "assistant"),
        "",
    )

    if re.search(r"\b(what('?s| is) (ur|your) name|who are you)\b", lower):
        return "I'm Aira, your English conversation partner. What would you like to talk about today?"
    if re.search(r"\b(hello+|hi+|hey+|good morning|good afternoon)\b", lower):
        return "Hi, I'm Aira. How has your day been so far?"
    if "how are you" in lower:
        return "I'm doing well, and I'm ready to practice with you. How are you feeling today?"
    if re.search(r"\b(what the heck|what happened|confused|doesn't make sense|does not make sense)\b", lower):
        return "It sounds like something felt confusing. What part didn't make sense?"
    if "yesterday" in lower or "last week" in lower or "last night" in lower:
        return "That sounds like a real story. What happened next?"
    if "study" in lower or "campus" in lower or "school" in lower:
        return "That sounds like a busy part of your life. What are you studying right now?"
    if len(lower) <= 2:
        if previous_assistant_message:
            return "I'm listening. Could you say that again in a full sentence?"
        return "Could you say a little more?"
    if previous_user_message:
        return f"Got it, you're talking about '{content}'. What do you think about it?"
    return "That sounds interesting. Tell me a little more about it."


def generate_reply(history, content):
    if not DEEPSEEK_API_KEY:
        return demo_reply(history, content)
    try:
        messages = [{"role": "system", "content": AIRA_SYSTEM_PROMPT}]
        messages.extend(history[-10:])
        messages.append({"role": "user", "content": content})
        payload = json.dumps({"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 180}).encode("utf-8")
        http_request = urlrequest.Request(DEEPSEEK_API_URL, data=payload, headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}, method="POST")
        with urlrequest.urlopen(http_request, timeout=45) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        reply = response_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not reply:
            return "I'm here with you, but DeepSeek returned an empty response. Could you try that again?"
        return reply
    except urlerror.HTTPError as exc:
        if exc.code == 402:
            logger.error("DeepSeek rejected the request because the account has insufficient balance")
            return "DeepSeek is connected, but this API account has no available balance. Please add credits or use a funded DeepSeek key."
        if exc.code in (401, 403):
            logger.error("DeepSeek rejected the API key with HTTP %s", exc.code)
            return "DeepSeek rejected this API key. Please check that the key is active and has API access."
        if exc.code == 429:
            logger.error("DeepSeek rate limit reached")
            return "DeepSeek is temporarily rate-limited. Please wait a moment and try again."
        logger.exception("DeepSeek request failed with HTTP %s", exc.code)
        return "DeepSeek returned an error. Please check the DeepSeek service and try again."
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError):
        logger.exception("DeepSeek request failed")
        return "I'm having trouble reaching DeepSeek right now. Please try again in a moment."


@app.get("/")
def index():
    if (FRONTEND_DIR / "index.html").exists():
        return send_from_directory(FRONTEND_DIR, "index.html")
    return jsonify({"status": "ok", "message": "Aira backend is running. Frontend not found."})


@app.get("/api/status")
def status():
    return jsonify({"ai_provider": "deepseek" if DEEPSEEK_API_KEY else "demo", "model": DEEPSEEK_MODEL})


@app.get("/<path:path>")
def frontend_assets(path):
    if (FRONTEND_DIR / path).exists():
        return send_from_directory(FRONTEND_DIR, path)
    return jsonify({"error": "Not found"}), 404


@app.post("/api/auth/signup")
def signup():
    payload = request.get_json(silent=True) or {}
    name = payload.get("name", "").strip()
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not name or not email or len(password) < 6:
        return jsonify({"error": "Name, email, and a password of at least 6 characters are required"}), 400
    connection = db()
    try:
        cursor = connection.execute(
            "INSERT INTO users (name, email, password, created_at) VALUES (?, ?, ?, ?)",
            (name, email, generate_password_hash(password), now()),
        )
        connection.commit()
        session["user_id"] = cursor.lastrowid
        return jsonify({"user": serialize_user(connection.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone())}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with that email already exists"}), 409
    finally:
        connection.close()


@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    connection = db()
    user = connection.execute("SELECT * FROM users WHERE email = ?", (payload.get("email", "").strip().lower(),)).fetchone()
    if user is None or not check_password_hash(user["password"], payload.get("password", "")):
        connection.close()
        return jsonify({"error": "Invalid email or password"}), 401
    session["user_id"] = user["id"]
    result = serialize_user(user)
    connection.close()
    return jsonify({"user": result})


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def me():
    connection = db()
    user = current_user(connection)
    result = {"user": serialize_user(user)} if user else {"user": None}
    connection.close()
    return jsonify(result)


def serialize_user(user):
    return {"id": user["id"], "name": user["name"], "email": user["email"], "level": user["level"], "level_label": user["level_label"], "overall_score": user["overall_score"]} if user else None


@app.get("/api/conversations")
def list_conversations():
    result = require_user()
    if result[0] is None:
        return result[1]
    connection, user, _ = result
    rows = connection.execute("SELECT * FROM conversations WHERE user_id = ? AND deleted_at IS NULL ORDER BY started_at DESC", (user["id"],)).fetchall()
    connection.close()
    return jsonify({"conversations": [dict(row) for row in rows]})


@app.post("/api/conversations")
def create_conversation():
    result = require_user()
    if result[0] is None:
        return result[1]
    connection, user, _ = result
    payload = request.get_json(silent=True) or {}
    conversation_id = str(uuid.uuid4())
    connection.execute("INSERT INTO conversations (id, user_id, title, started_at, topic) VALUES (?, ?, ?, ?, ?)", (conversation_id, user["id"], payload.get("title", "New practice session"), now(), payload.get("topic", "Everyday English")))
    connection.commit()
    row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    connection.close()
    return jsonify({"conversation": dict(row)}), 201


@app.delete("/api/conversations/<conversation_id>")
def delete_conversation(conversation_id):
    result = require_user()
    if result[0] is None:
        return result[1]
    connection, user, _ = result
    cursor = connection.execute("UPDATE conversations SET deleted_at = ?, ended_at = ? WHERE id = ? AND user_id = ? AND deleted_at IS NULL", (now(), now(), conversation_id, user["id"]))
    connection.commit()
    connection.close()
    return jsonify({"ok": cursor.rowcount == 1})


@app.get("/api/conversations/<conversation_id>/messages")
def get_messages(conversation_id):
    result = require_user()
    if result[0] is None:
        return result[1]
    connection, user, _ = result
    if active_conversation(connection, conversation_id, user["id"]) is None:
        connection.close()
        return jsonify({"error": "Conversation not found"}), 404
    rows = connection.execute("SELECT id, role, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
    connection.close()
    return jsonify({"messages": [dict(row) for row in rows]})


@app.post("/api/chat")
def chat():
    result = require_user()
    if result[0] is None:
        return result[1]
    connection, user, _ = result
    payload = request.get_json(silent=True) or {}
    conversation_id = payload.get("conversation_id")
    content = payload.get("message", "").strip()
    if not conversation_id or not content:
        connection.close()
        return jsonify({"error": "conversation_id and message are required"}), 400
    if active_conversation(connection, conversation_id, user["id"]) is None:
        connection.close()
        return jsonify({"error": "Conversation not found"}), 404
    history_rows = connection.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
    history = [dict(row) for row in history_rows]
    timestamp = now()
    user_cursor = connection.execute("INSERT INTO messages (conversation_id, user_id, role, content, timestamp) VALUES (?, ?, 'user', ?, ?)", (conversation_id, user["id"], content, timestamp))
    analysis = analyze_user_message(content)
    connection.execute("INSERT INTO message_analysis (message_id, conversation_id, grammar_issues, naturalness_issues, unique_words, vocabulary_opportunities) VALUES (?, ?, ?, ?, ?, ?)", (user_cursor.lastrowid, conversation_id, json.dumps(analysis["grammar_issues"]), json.dumps(analysis["naturalness_issues"]), analysis["unique_words"], json.dumps(analysis["vocabulary_opportunities"])))
    for issue in analysis["grammar_issues"]:
        connection.execute("INSERT INTO learner_patterns (user_id, pattern_type, category, example, correction) VALUES (?, 'grammar', ?, ?, ?) ON CONFLICT(user_id, category, example) DO UPDATE SET frequency = frequency + 1", (user["id"], issue["category"], issue["example"], issue["correction"]))
    reply = generate_reply(history, content)
    assistant_cursor = connection.execute("INSERT INTO messages (conversation_id, user_id, role, content, timestamp) VALUES (?, ?, 'assistant', ?, ?)", (conversation_id, user["id"], reply, now()))
    if len(history) == 0:
        title = " ".join(content.split()[:6]) or "Practice session"
        connection.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
    connection.commit()
    connection.close()
    return jsonify({"message": {"id": assistant_cursor.lastrowid, "role": "assistant", "content": reply, "timestamp": now()}, "analysis": analysis})


@app.post("/api/analysis")
def session_review():
    result = require_user()
    if result[0] is None:
        return result[1]
    connection, user, _ = result
    conversation_id = (request.get_json(silent=True) or {}).get("conversation_id")
    conversation = active_conversation(connection, conversation_id, user["id"]) if conversation_id else None
    if conversation is None:
        connection.close()
        return jsonify({"error": "Conversation not found"}), 404
    messages = connection.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
    user_messages = [row["content"] for row in messages if row["role"] == "user"]
    if not user_messages:
        connection.close()
        return jsonify({"error": "Not enough data for a session review"}), 400
    words = sum(len(word_list(item)) for item in user_messages)
    issues = connection.execute("SELECT grammar_issues FROM message_analysis WHERE conversation_id = ?", (conversation_id,)).fetchall()
    issue_count = sum(len(json.loads(row["grammar_issues"])) for row in issues)
    review = f"# Aira's Session Review\n\n## Overall Assessment\nYou completed {len(user_messages)} speaking turns with {words} words. Keep building your confidence through short, regular conversations.\n\n## Grammar\nI noticed {issue_count} grammar pattern{'s' if issue_count != 1 else ''} in this session.\n\n## Natural English\nYour ideas came through clearly. Keep listening for natural phrasing in everyday conversations.\n\n## Vocabulary\nYou used {len(set(word for message in user_messages for word in word_list(message)))} unique words.\n\n## Strengths\nYou stayed engaged and expressed your own ideas.\n\n## Focus Areas\nTry adding one detail to each answer and checking past-tense verbs for finished events.\n\n## Personalized Practice\nTell Aira about something you did yesterday using three sentences."
    connection.execute("INSERT INTO session_analysis (conversation_id, user_id, analysis_text, created_at) VALUES (?, ?, ?, ?)", (conversation_id, user["id"], review, now()))
    connection.commit()
    connection.close()
    return jsonify({"analysis": review})


@app.get("/api/stats")
def stats():
    result = require_user()
    if result[0] is None:
        return result[1]
    connection, user, _ = result
    base = (user["id"],)
    conversations = connection.execute("SELECT COUNT(*) AS count FROM conversations WHERE user_id = ? AND deleted_at IS NULL", base).fetchone()["count"]
    user_messages = connection.execute("SELECT COUNT(*) AS count FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.user_id = ? AND m.role = 'user' AND c.deleted_at IS NULL", base).fetchone()["count"]
    all_words = connection.execute("SELECT m.content FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.user_id = ? AND m.role = 'user' AND c.deleted_at IS NULL", base).fetchall()
    words = [word for row in all_words for word in word_list(row["content"])]
    issues = connection.execute("SELECT ma.grammar_issues FROM message_analysis ma JOIN conversations c ON c.id = ma.conversation_id WHERE c.user_id = ? AND c.deleted_at IS NULL", base).fetchall()
    issue_count = sum(len(json.loads(row["grammar_issues"])) for row in issues)
    patterns = connection.execute("SELECT category, example, correction, frequency FROM learner_patterns WHERE user_id = ? ORDER BY frequency DESC", base).fetchall()
    result = {"total_conversations": conversations, "total_user_turns": user_messages, "total_messages": user_messages * 2, "total_words": len(words), "average_words_per_turn": round(len(words) / user_messages, 1) if user_messages else 0, "grammar_issues": issue_count, "vocabulary_count": len(set(words)), "patterns": [dict(row) for row in patterns]}
    connection.close()
    return jsonify(result)


@app.get("/api/statistics")
def statistics():
    response = stats()
    if not hasattr(response, "get_json"):
        return response
    data = response.get_json()
    total_words = data["total_words"]
    turns = data["total_user_turns"]
    data["skills"] = {"grammar": max(0, 92 - data["grammar_issues"] * 8), "vocabulary": min(95, 45 + data["vocabulary_count"] * 2), "fluency": min(95, 40 + data["average_words_per_turn"] * 3), "naturalness": 78 if turns else 0, "communication": min(95, 35 + turns * 5), "sentence_structure": min(95, 40 + data["average_words_per_turn"] * 2)}
    data["level"] = "A2" if total_words < 250 else "B1" if total_words < 1000 else "B2"
    return jsonify(data)


@app.post("/api/stt")
def stt():
    return jsonify({"error": "Speech recognition is ready for integration; configure microphone and Google Speech credentials."}), 501


@app.post("/api/tts")
def tts():
    return jsonify({"error": "Text-to-speech is ready for integration; install gTTS and configure an audio response pipeline."}), 501


# Init DB saat module di-load (penting untuk Vercel serverless)
try:
    init_db()
except Exception as e:
    logger.exception("Failed to initialize database: %s", e)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))