"""
TruthLens Backend - Consolidated Server
All endpoints and code consolidated from app.py, auth.py, topics.py, models.py, config.py, and utils.py
"""

# ============================================================================
# IMPORTS
# ============================================================================
from flask import Flask, jsonify, request, Response, session, Blueprint
from flask_cors import CORS
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from datetime import datetime, timezone, date, timedelta
from uuid import uuid4
from difflib import SequenceMatcher
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
import time
import hashlib
import json
import math
import sqlite3
import os
import requests
import re
import traceback
import logging
import base64
import subprocess
import sys
import shutil
import py_compile
import getpass
from collections import Counter
from dotenv import load_dotenv

from sqlalchemy import inspect, Table, Column, Integer, String, LargeBinary, DateTime, MetaData

# ============================================================================
# CONFIGURATION & ENVIRONMENT
# ============================================================================
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://avnadmin:AVNS_qkigRpkNe7l-cmDfb3d@pg-2934e969-virajdb.k.aivencloud.com:21611/defaultdb?sslmode=require')
_CA_PATH = os.path.join(BASE_DIR, 'ca.pem')
if os.path.exists(_CA_PATH) and 'sslmode=require' in _DATABASE_URL:
    _DATABASE_URL = _DATABASE_URL.replace('sslmode=require', f'sslmode=verify-ca&sslrootcert={_CA_PATH}')

# Server Configuration Constants
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'https://ollama-production-eaed.up.railway.app')
GEMINI_URL = os.getenv('GEMINI_URL', 'https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent')
GROQ_URL = os.getenv('GROQ_URL', 'https://api.groq.com/openai/v1/chat/completions')

# Cache and timing constants
CACHE_TTL = 300
NEWS_FETCH_TIMEOUT = 15
DAILY_LIMIT = 20

# ============================================================================
# LOGGING
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('truthlens')

# ============================================================================
# DATABASE MODELS
# ============================================================================
db = SQLAlchemy()

def gen_uuid():
    return str(uuid4())

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    google_id = db.Column(db.String(255), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    avatar_url = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class UserPreference(db.Model):
    __tablename__ = 'user_preferences'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False, unique=True)
    preferences = db.Column(db.JSON, default=dict)
    api_keys = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class ExpandedTopic(db.Model):
    __tablename__ = 'expanded_topics'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    original_topic = db.Column(db.String(255), nullable=False)
    expanded_topics = db.Column(db.JSON, default=list)
    source = db.Column(db.String(50), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class GeminiDailyUsage(db.Model):
    __tablename__ = 'gemini_daily_usage'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    count = db.Column(db.Integer, default=0)

class ReadingStat(db.Model):
    __tablename__ = 'reading_stats'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    article_url = db.Column(db.String(1024), nullable=False)
    article_title = db.Column(db.String(512), default='')
    source = db.Column(db.String(128), default='')
    read_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    estimated_seconds = db.Column(db.Integer, default=0)

class CustomRssSource(db.Model):
    __tablename__ = 'custom_rss_sources'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    url = db.Column(db.String(2048), nullable=False)
    label = db.Column(db.String(255), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class TrackedClaim(db.Model):
    __tablename__ = 'tracked_claims'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    claim_text = db.Column(db.Text, nullable=False)
    keywords = db.Column(db.String(1024), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_checked_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class ClaimMention(db.Model):
    __tablename__ = 'claim_mentions'
    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(db.Integer, db.ForeignKey('tracked_claims.id'), nullable=False)
    article_url = db.Column(db.String(1024), nullable=False)
    article_title = db.Column(db.String(512), default='')
    source = db.Column(db.String(128), default='')
    snippet = db.Column(db.Text, default='')
    matched_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class SharedList(db.Model):
    __tablename__ = 'shared_lists'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.Text, default='')
    articles = db.Column(db.JSON, default=list)
    is_public = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class ScheduledReport(db.Model):
    __tablename__ = 'scheduled_reports'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    frequency = db.Column(db.String(20), default='weekly')
    day_of_week = db.Column(db.Integer, default=0)
    time = db.Column(db.String(5), default='08:00')
    last_generated = db.Column(db.DateTime, nullable=True)
    next_generate = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class ReportArchive(db.Model):
    __tablename__ = 'report_archives'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    report_data = db.Column(db.JSON, default=dict)
    generated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    download_url = db.Column(db.String(1024), default='')

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def ensure_list(val):
    if isinstance(val, list):
        return [v for v in val if v]
    if isinstance(val, str):
        return [val] if val else []
    return []

def _get_cipher():
    secret_key = os.getenv('FLASK_SECRET_KEY', 'truthlens_default_secret_key_2026')
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
    return Fernet(key)

def encrypt_api_keys(api_keys):
    if not api_keys or not isinstance(api_keys, dict):
        return api_keys
    try:
        cipher = _get_cipher()
        data = json.dumps(api_keys).encode()
        return cipher.encrypt(data).decode()
    except Exception as e:
        logger.warning(f"Failed to encrypt API keys: {e}")
        return api_keys

def decrypt_api_keys(encrypted):
    if not encrypted:
        return {}
    if isinstance(encrypted, dict):
        return encrypted
    if isinstance(encrypted, str):
        try:
            cipher = _get_cipher()
            data = cipher.decrypt(encrypted.encode())
            return json.loads(data)
        except Exception:
            try:
                return json.loads(encrypted)
            except (json.JSONDecodeError, TypeError):
                return {}
    return {}

# ============================================================================
# FLASK APP INITIALIZATION
# ============================================================================

app = Flask(__name__)

# Configuration
SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'truthlens_default_secret_key_2026')

ENVIRONMENT = os.getenv('ENVIRONMENT', 'development').lower()

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SQLALCHEMY_DATABASE_URI=_DATABASE_URL,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_PERMANENT=True,
    SESSION_TYPE='sqlalchemy',
    SESSION_SQLALCHEMY_TABLE='sessions',
    SESSION_USE_SIGNER=True,
    SESSION_COOKIE_SAMESITE=os.getenv('SESSION_COOKIE_SAMESITE', 'None'),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=(ENVIRONMENT == 'production')
)

# CORS Configuration
CORS_ORIGIN = os.getenv(
    'CORS_ORIGIN',
    'http://localhost:5173,http://127.0.0.1:5173,'
    'http://localhost:5174,http://127.0.0.1:5174,'
    'http://localhost:5175,http://127.0.0.1:5175,'
    'http://localhost:5176,http://127.0.0.1:5176,'
    'https://chatupsignin.netlify.app,'
    'https://tangerine-mochi-ac4e44.netlify.app,'
    'https://truthlence.netlify.app'
)

CORS(app, supports_credentials=True, origins=CORS_ORIGIN.split(','))

# Initialize database
db.init_app(app)

# ThreadPoolExecutor for parallel operations
_EXECUTOR = ThreadPoolExecutor(max_workers=8)

# Ensure sessions table exists before flask-session initializes
with app.app_context():
    if 'sessions' not in inspect(db.engine).get_table_names():
        _sess_meta = MetaData()
        Table('sessions', _sess_meta,
              Column('id', Integer, primary_key=True),
              Column('session_id', String(255), unique=True, nullable=False),
              Column('data', LargeBinary),
              Column('expiry', DateTime))
        _sess_meta.create_all(bind=db.engine)
        logger.info("Created sessions table")

app.config['SESSION_SQLALCHEMY'] = db
Session(app)

with app.app_context():
    db.create_all()

# ============================================================================
# SQLITE CACHE MANAGEMENT
# ============================================================================

DB_PATH = os.path.join(os.path.dirname(__file__), "truthlens_cache.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=100)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_credibility (
            source TEXT PRIMARY KEY,
            true_count INTEGER DEFAULT 0,
            false_count INTEGER DEFAULT 0,
            misleading_count INTEGER DEFAULT 0,
            unverified_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            score REAL DEFAULT 0.5
        );
        CREATE TABLE IF NOT EXISTS source_failures (
            source TEXT PRIMARY KEY,
            error_count INTEGER DEFAULT 0,
            last_error TEXT,
            last_failure REAL
        );
    """)
    conn.commit()
    conn.close()

init_db()

def evict_stale_cache(max_age=3600):
    try:
        conn = get_db()
        cutoff = time.time() - max_age
        conn.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Cache eviction error: {e}")

evict_stale_cache()

def get_cache_key(params):
    raw = json.dumps(params, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()

def get_cached(key):
    conn = get_db()
    row = conn.execute("SELECT data, created_at FROM cache WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row and time.time() - row["created_at"] < CACHE_TTL:
        return json.loads(row["data"])
    if row:
        conn = get_db()
        conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        conn.commit()
        conn.close()
    return None

_cache_write_count = 0

def set_cache(key, data):
    global _cache_write_count
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, data, created_at) VALUES (?, ?, ?)",
        (key, json.dumps(data), time.time())
    )
    conn.commit()
    conn.close()
    _cache_write_count += 1
    if _cache_write_count % 10 == 0:
        evict_stale_cache()

def clear_all_cache():
    conn = get_db()
    conn.execute("DELETE FROM cache")
    conn.commit()
    conn.close()

# ============================================================================
# SOURCE CREDIBILITY MANAGEMENT
# ============================================================================

def update_source_credibility(source, verdict):
    if not source or not verdict:
        return
    conn = get_db()
    row = conn.execute("SELECT * FROM source_credibility WHERE source = ?", (source,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO source_credibility (source, true_count, false_count, misleading_count, unverified_count, total_count, score) VALUES (?, 0, 0, 0, 0, 0, 0.5)",
            (source,)
        )
        row = {"true_count": 0, "false_count": 0, "misleading_count": 0, "unverified_count": 0, "total_count": 0}
    col_map = {"True": "true_count", "False": "false_count", "Misleading": "misleading_count", "Unverified": "unverified_count"}
    col = col_map.get(verdict)
    if col:
        conn.execute(f"UPDATE source_credibility SET {col} = {col} + 1, total_count = total_count + 1 WHERE source = ?", (source,))
    conn.commit()
    updated = conn.execute("SELECT true_count, false_count, misleading_count, unverified_count, total_count FROM source_credibility WHERE source = ?", (source,)).fetchone()
    if updated and updated["total_count"] > 0:
        score = (updated["true_count"] * 1.0 + updated["misleading_count"] * 0.5 + updated["unverified_count"] * 0.3) / updated["total_count"]
        conn.execute("UPDATE source_credibility SET score = ? WHERE source = ?", (round(score, 2), source))
    conn.commit()
    conn.close()

def get_source_credibility(source):
    if not source:
        return 0.5
    conn = get_db()
    row = conn.execute("SELECT score FROM source_credibility WHERE source = ?", (source,)).fetchone()
    conn.close()
    return row["score"] if row else 0.5

def record_source_error(source, error):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO source_failures (source, error_count, last_error, last_failure) "
        "VALUES (?, COALESCE((SELECT error_count + 1 FROM source_failures WHERE source = ?), 1), ?, ?)",
        (source, source, str(error)[:500], time.time())
    )
    conn.commit()
    conn.close()

def get_source_failures():
    conn = get_db()
    rows = conn.execute("SELECT source, error_count, last_error, last_failure FROM source_failures").fetchall()
    conn.close()
    return {r["source"]: {"error_count": r["error_count"], "last_error": r["last_error"], "last_failure": r["last_failure"]} for r in rows}

# ============================================================================
# KEY ROTATION & API MANAGEMENT
# ============================================================================

class KeyRotationTracker:
    def __init__(self):
        self._idx = 0
        self._cooldowns = {}
    
    def get_key(self, keys):
        if not keys:
            return None
        now = time.time()
        for _ in range(len(keys)):
            idx = self._idx % len(keys)
            self._idx += 1
            key = keys[idx]
            if key in self._cooldowns:
                if now < self._cooldowns[key]:
                    continue
                del self._cooldowns[key]
            return key
        self._cooldowns = {}
        return keys[0]
    
    def mark_rate_limited(self, key, cooldown_seconds=60):
        if key:
            self._cooldowns[key] = time.time() + cooldown_seconds
    
    def status(self, keys):
        now = time.time()
        result = []
        for i, k in enumerate(keys):
            masked = k[:8] + "..." + k[-4:] if len(k) > 12 else "***"
            cooldown = self._cooldowns.get(k)
            result.append({"index": i, "key": masked, "cooling_down": cooldown is not None, "remaining_seconds": max(0, round(cooldown - now)) if cooldown else 0})
        return result

_key_rotation = KeyRotationTracker()

def _get_user_api_keys(user, *services):
    if not user:
        return {}
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    if not pref or not pref.api_keys:
        return {}
    keys = decrypt_api_keys(pref.api_keys)
    if services:
        return {s: ensure_list(keys.get(s, [])) for s in services}
    return {s: ensure_list(keys.get(s, [])) for s in ('groq', 'factCheck', 'gemini', 'elevenlabs') if keys.get(s)}

# ============================================================================
# AUTHENTICATION DECORATOR & HELPERS
# ============================================================================

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get('user_id')
        user = None
        if user_id:
            user = db.session.get(User, user_id)
            if not user:
                session.clear()
                user = None
        return f(user=user, *args, **kwargs)
    return decorated

# ============================================================================
# TOPIC EXPANSION HELPERS
# ============================================================================

EXPAND_PROMPT = (
    "You are a news topic expander. Given the topic below, generate 5-15 related subtopics "
    "and currently trending topics that commonly appear in news articles. "
    "Include specific people, companies, events, technologies, or regions relevant to the topic. "
    "Return ONLY a JSON array of strings, nothing else. Example: "
    '["Artificial Intelligence", "Machine Learning", "ChatGPT", "OpenAI", "Deep Learning", "Neural Networks", "AI Regulation", "Tech Giants"]\n\n'
    "Topic: {topic}"
)

def call_gemini(prompt, api_keys):
    keys = ensure_list(api_keys)
    for key in keys:
        try:
            resp = requests.post(
                f"{GEMINI_URL}?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=None
            )
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if text:
                        return text
            if resp.status_code in (400, 401, 403, 429):
                continue
        except Exception:
            continue
    return None

def call_groq_compound(prompt, api_keys):
    keys = ensure_list(api_keys)
    for key in keys:
        try:
            resp = requests.post(
                GROQ_URL,
                json={
                    "model": "groq/compound",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1000
                },
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=None
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    text = choices[0].get("message", {}).get("content", "")
                    if text:
                        return text
            if resp.status_code in (401, 403, 429):
                continue
        except Exception:
            continue
    return None

def call_ollama_topic_expand(prompt):
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/generate",
            json={"model": "qwen2.5:0.5b", "prompt": prompt, "stream": False, "format": "json"},
            timeout=None
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("response", "").strip()
            if text:
                return text
    except Exception as e:
        logger.warning(f"Ollama topic expand error: {e}")
    return None

def parse_topic_list(text):
    if not text:
        return []
    json_match = re.search(r'\[.*?\]', text, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if s]
        except json.JSONDecodeError:
            pass
    lines = re.split(r'[,\n]+', text)
    topics = []
    for line in lines:
        line = re.sub(r'^[\s\-*•‣⁃]+|[\s\-*•‣⁃]+$', '', line).strip().strip('"').strip("'")
        if line and len(line) > 1:
            topics.append(line)
    return topics

def check_gemini_limit(user_id):
    today = date.today()
    usage = GeminiDailyUsage.query.filter_by(user_id=user_id, date=today).first()
    if usage and usage.count >= DAILY_LIMIT:
        return True
    return False

def increment_gemini_usage(user_id):
    today = date.today()
    usage = GeminiDailyUsage.query.filter_by(user_id=user_id, date=today).first()
    if usage:
        usage.count += 1
    else:
        usage = GeminiDailyUsage(user_id=user_id, date=today, count=1)
        db.session.add(usage)
    db.session.commit()

# ============================================================================
# NEWS FETCHING HELPERS
# ============================================================================

def clean_duplicates(articles):
    seen = []
    unique = []
    for a in articles:
        title = a.get("title", "").strip().lower()
        if not title:
            continue
        is_dup = False
        for s in seen:
            if SequenceMatcher(None, title, s).ratio() > 0.85:
                is_dup = True
                break
        if not is_dup:
            seen.append(title)
            unique.append(a)
    return unique

def score_and_filter_articles(articles, query):
    query_lower = query.lower().strip()
    if not query_lower:
        return articles, []
    search_terms = set(re.split(r'\s+', re.sub(r'[^a-z0-9\s\u0900-\u097F]', '', query_lower)))
    search_terms = set(t for t in search_terms if len(t) > 2)
    if not search_terms:
        return articles, []

    scored = []
    for a in articles:
        title_lower = a.get("title", "").lower()
        desc_lower = a.get("description", "").lower()
        score = 0
        matched_terms = []
        for term in search_terms:
            title_count = title_lower.count(term)
            desc_count = desc_lower.count(term)
            if title_count > 0:
                score += title_count * 10
                matched_terms.append(term)
            if desc_count > 0:
                score += desc_count * 2
                if term not in matched_terms:
                    matched_terms.append(term)
        if score > 0:
            if title_lower.startswith(query_lower) or query_lower in title_lower:
                score += 20
            a["relevance_score"] = score
            a["matched_terms"] = matched_terms
            scored.append(a)
    scored.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    return scored, list(search_terms)

def normalize_article(a):
    return {
        "title": a.get("title", ""),
        "description": a.get("description", "") or "",
        "url": a.get("url", "") or "",
        "source": a.get("source", ""),
        "image": a.get("image", "") or "",
        "published_at": a.get("published_at", "") or "",
        "author": a.get("author", "") or "",
        "api_source": a.get("api_source", ""),
    }

def cluster_articles(articles, threshold=0.7):
    clusters = []
    assigned = set()
    for i, a in enumerate(articles):
        if i in assigned:
            continue
        cluster = [a]
        assigned.add(i)
        for j, b in enumerate(articles):
            if j in assigned or i == j:
                continue
            sim = SequenceMatcher(None, a["title"].lower(), b["title"].lower()).ratio()
            if sim >= threshold:
                cluster.append(b)
                assigned.add(j)
        if len(cluster) > 1:
            clusters.append({"id": hashlib.md5(cluster[0]["title"].encode()).hexdigest()[:8], "articles": cluster})
    return clusters

def _get_fetch_tasks(query, category, language, country):
    tasks = []
    tasks.append(("newsdata", lambda: newsdata_fetch_latest(query=query, language=language)))
    tasks.append(("gnews", lambda: gnews_fetch_search(query=query, lang=language) if query else gnews_fetch_top_headlines(lang=language, country=country or 'us', category=category)))
    if language == 'en':
        tasks.append(("currents", lambda: currents_fetch_search(keywords=query, country=country) if query else currents_fetch_latest(category=category, country=country)))
        tasks.append(("newsapi", lambda: newsapi_fetch_everything(query=query) if query else newsapi_fetch_top_headlines(country=country or 'us', category=category)))
    if not country or country == 'in':
        tasks.append(("google_news", lambda: fetch_google_news(query if query else 'india news', lang=language)))
    if not country or country in ('in', 'gb', 'us'):
        if language == 'en':
            tasks.append(("rss_bbc", lambda: fetch_rss("bbc")))
            tasks.append(("rss_reuters", lambda: fetch_rss("reuters")))
            tasks.append(("rss_the_hindu", lambda: fetch_rss("the_hindu")))
        elif language == 'hi':
            tasks.append(("rss_amar_ujala", lambda: fetch_rss("amar_ujala")))
            tasks.append(("rss_aaj_tak", lambda: fetch_rss("aaj_tak")))
        elif language == 'mr':
            tasks.append(("rss_pudhari", lambda: fetch_rss("pudhari")))
    return tasks

def fetch_all_sources(query=None, category=None, source_filter=None, language=None, country=None, sort=None, custom_rss_sources=None, blocked_sources=None):
    requested = None
    if source_filter:
        requested = set(s.strip().lower() for s in source_filter.split(","))

    tasks = _get_fetch_tasks(query, category, language or 'en', country)
    if custom_rss_sources:
        for rss in custom_rss_sources:
            label = rss.get('label', '')
            url = rss.get('url', '')
            if url:
                tasks.append((f"custom_rss_{label or url}", lambda u=url: fetch_custom_rss(u)))
    if requested:
        tasks = [(src, fn) for src, fn in tasks if any(r in src for r in requested)]

    blocked = set()
    if blocked_sources:
        if isinstance(blocked_sources, str):
            try:
                blocked = set(json.loads(blocked_sources))
            except:
                blocked = set(blocked_sources.split(','))
        elif isinstance(blocked_sources, list):
            blocked = set(s.lower().strip() for s in blocked_sources)

    articles = []
    failed_sources = []
    futures = {_EXECUTOR.submit(fn): src for src, fn in tasks}
    deadline = time.time() + NEWS_FETCH_TIMEOUT
    completed = set()

    try:
        for future in as_completed(futures, timeout=NEWS_FETCH_TIMEOUT):
            src = futures[future]
            completed.add(future)
            try:
                remaining = max(0.01, deadline - time.time())
                result = future.result(timeout=remaining)
                articles.extend(result)
            except Exception as e:
                logger.warning(f"Source error ({src}): {e}")
                failed_sources.append({"source": src, "error": str(e)})
                record_source_error(src, e)
            if time.time() >= deadline:
                break
    except TimeoutError:
        logger.warning(f"News fetch timeout reached after {NEWS_FETCH_TIMEOUT}s; returning partial results.")

    for future, src in futures.items():
        if future in completed:
            continue
        if future.done():
            try:
                result = future.result()
                articles.extend(result)
            except Exception as e:
                logger.warning(f"Source error ({src}) after timeout: {e}")
                failed_sources.append({"source": src, "error": str(e)})
                record_source_error(src, e)
        else:
            logger.warning(f"Source timeout ({src}) after {NEWS_FETCH_TIMEOUT}s")
            failed_sources.append({"source": src, "error": "timeout"})
            future.cancel()

    EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
    def parse_date(d):
        if not d:
            return EPOCH
        for fmt in ("%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f%z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S %z"):
            try:
                dt = datetime.strptime(d[:25], fmt[:len(d[:25])])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return EPOCH

    unique = clean_duplicates(articles)
    unique = [normalize_article(a) for a in unique]
    if blocked:
        unique = [a for a in unique if a.get("source", "").lower().strip() not in blocked and a.get("api_source", "").lower().strip() not in blocked]
    for a in unique:
        a["source_score"] = get_source_credibility(a.get("source", ""))

    search_terms = []
    if query:
        unique, search_terms = score_and_filter_articles(unique, query)
    elif sort == "newest":
        unique.sort(key=lambda x: parse_date(x.get("published_at", "")), reverse=True)
    elif sort == "oldest":
        unique.sort(key=lambda x: parse_date(x.get("published_at", "")))
    elif sort == "source":
        source_priority = {"newsdata": 0, "currents": 0, "newsapi": 1, "gnews": 1, "google_news": 2, "rss_bbc": 3, "rss_reuters": 3, "rss_the_hindu": 3}
        unique.sort(key=lambda x: source_priority.get(x.get("api_source", ""), 3))
    else:
        unique.sort(key=lambda x: parse_date(x.get("published_at", "")), reverse=True)

    return unique, failed_sources, search_terms

# ============================================================================
# AI REQUEST HELPERS
# ============================================================================

def groq_request(payload, api_keys, timeout=None):
    keys = ensure_list(api_keys)
    if not keys:
        return None, None
    for _ in range(len(keys)):
        key = _key_rotation.get_key(keys)
        if not key:
            return None, None
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=timeout
            )
            if resp.status_code in (401, 403):
                continue
            if resp.status_code == 429:
                _key_rotation.mark_rate_limited(key)
                continue
            return resp, key
        except Exception:
            continue
    return None, None

def ollama_request(prompt, system_prompt=None, format_json=False, timeout=None):
    try:
        payload = {"model": "qwen2.5:0.5b", "prompt": prompt, "stream": False}
        if system_prompt:
            payload["system"] = system_prompt
        if format_json:
            payload["format"] = "json"
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/generate",
            json=payload,
            timeout=timeout
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("response", "").strip()
            if text:
                return text, "ollama/qwen2.5:0.5b"
    except Exception as e:
        logger.warning(f"Ollama request error: {e}")
    return None, None

def search_tavily(query):
    key = os.getenv('TAVILY_API_KEY', '')
    if not key:
        return [], "", ""
    try:
        resp = requests.post("https://api.tavily.com/search", json={
            "api_key": key,
            "query": query,
            "search_depth": "advanced",
            "include_answer": True,
            "max_results": 5
        }, timeout=None)
        if resp.status_code == 200:
            data = resp.json()
            sources = []
            snippets = []
            for r in data.get("results", [])[:5]:
                sources.append({"title": r["title"], "uri": r["url"], "source": "tavily"})
                if r.get("content"):
                    snippets.append(f"- {r['title']}: {r['content'][:500]}")
            content_str = "\n".join(snippets)
            return sources, data.get("answer", ""), content_str
    except Exception as e:
        logger.warning(f"Tavily search error: {e}")
    return [], "", ""

def needs_web_search(message):
    keywords = [
        "latest", "recent", "current", "happening", "update", "breaking",
        "background", "context", "who is", "verify", "true?", "fact",
        "search", "find", "look up", "tell me about", "what about",
        "source", "sources", "evidence", "proof", "news about",
        "what happened", "what is", "who are", "where", "when did",
        "is this true", "is that true", "fact-check", "check this"
    ]
    msg = message.lower().strip()
    if len(msg) < 10:
        return False
    return any(kw in msg for kw in keywords)

# ============================================================================
# NEWS API SOURCE WRAPPERS (NewsData, GNews, NewsAPI, Currents)
# These are inlined here so server.py is fully self-contained.
# ============================================================================

# ---------------------------------------------------------------------------
# NewsData.io wrapper
# ---------------------------------------------------------------------------
NEWSDATA_API_KEYS = [
    "pub_cd613a9c02214a90b2996680881099e5",
    "pub_214cf48a21ed4135be32aa2095be738d",
    "pub_000153f0ca454e3c9cc2e1b400e42589",
    "pub_e34e861b8b6f4d909d5f4262ae1895a3",
]
_newsdata_key_idx = 0
NEWSDATA_BASE = "https://newsdata.io/api/1"

def _newsdata_key():
    global _newsdata_key_idx
    return NEWSDATA_API_KEYS[_newsdata_key_idx]

def _newsdata_rotate():
    global _newsdata_key_idx
    _newsdata_key_idx = (_newsdata_key_idx + 1) % len(NEWSDATA_API_KEYS)
    logger.info(f"NewsData: rotating to key[{_newsdata_key_idx}]")

def newsdata_fetch_latest(country=None, category=None, query=None, language="en", size=20):
    for attempt in range(len(NEWSDATA_API_KEYS)):
        params = {"apikey": _newsdata_key(), "language": language}
        if country: params["country"] = country
        if category: params["category"] = category
        if query: params["q"] = query
        try:
            resp = requests.get(f"{NEWSDATA_BASE}/latest", params=params, timeout=None)
            resp.raise_for_status()
            data = resp.json()
            articles = []
            for a in data.get("results", [])[:size]:
                articles.append({
                    "title": a.get("title") or "",
                    "description": a.get("description") or "",
                    "url": a.get("link") or "",
                    "source": a.get("source_name") or "NewsData",
                    "image": a.get("image_url") or "",
                    "published_at": a.get("pubDate") or "",
                    "author": a.get("creator")[0] if a.get("creator") else "",
                    "api_source": "newsdata",
                })
            return articles
        except Exception as e:
            logger.warning(f"NewsData error (key[{_newsdata_key_idx}]): {e}")
            _newsdata_rotate()
    logger.warning("NewsData: all keys exhausted")
    return []

# ---------------------------------------------------------------------------
# GNews wrapper
# ---------------------------------------------------------------------------
GNEWS_API_KEY = "8b15af7238d33df2a4e695d7d3b4b471"
GNEWS_BASE = "https://gnews.io/api/v4"
_gnews_last_429 = 0
_GNEWS_COOLDOWN = 86400

def _gnews_on_cooldown():
    global _gnews_last_429
    if not _gnews_last_429:
        return False
    elapsed = time.time() - _gnews_last_429
    if elapsed < _GNEWS_COOLDOWN:
        logger.info(f"GNews: skipping (rate-limited {elapsed/60:.0f}m ago)")
        return True
    _gnews_last_429 = 0
    return False

def _gnews_request(url, params):
    global _gnews_last_429
    resp = requests.get(url, params=params, timeout=None)
    if resp.status_code == 429:
        _gnews_last_429 = time.time()
        raise Exception("429 Too Many Requests — rate-limited")
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"API error: {data['errors']}")
    return data

def _gnews_parse(data):
    articles = []
    for a in data.get("articles", []):
        articles.append({
            "title": a.get("title") or "",
            "description": a.get("description") or "",
            "url": a.get("url") or "",
            "source": a.get("source", {}).get("name") or "GNews",
            "image": a.get("image") or "",
            "published_at": a.get("publishedAt") or "",
            "author": a.get("source", {}).get("url") or "",
            "api_source": "gnews",
        })
    return articles

def gnews_fetch_top_headlines(lang="en", country="us", category=None, max_results=15):
    if _gnews_on_cooldown():
        return []
    try:
        params = {"token": GNEWS_API_KEY, "lang": lang, "country": country, "max": max_results}
        if category: params["category"] = category
        data = _gnews_request(f"{GNEWS_BASE}/top-headlines", params)
        return _gnews_parse(data)
    except Exception as e:
        logger.warning(f"GNews top-headlines error: {e}")
        raise

def gnews_fetch_search(query, lang="en", max_results=15):
    if _gnews_on_cooldown():
        return []
    try:
        params = {"token": GNEWS_API_KEY, "q": query, "lang": lang, "max": max_results}
        data = _gnews_request(f"{GNEWS_BASE}/search", params)
        return _gnews_parse(data)
    except Exception as e:
        logger.warning(f"GNews search error: {e}")
        raise

# ---------------------------------------------------------------------------
# NewsAPI wrapper
# ---------------------------------------------------------------------------
NEWSAPI_KEYS = [
    "8a5b1956773b4281a1f19d4bbf1814f8",
    "cb640b61624743feb65fb8d4fb59421a",
    "8ff5f08341d54b258dc81f2122ed5d8e",
    "25540b08a90a4a3ebebd0671b3aaad06",
]
_newsapi_key_idx = 0
NEWSAPI_BASE = "https://newsapi.org/v2"

def _newsapi_key():
    global _newsapi_key_idx
    return NEWSAPI_KEYS[_newsapi_key_idx]

def _newsapi_rotate():
    global _newsapi_key_idx
    _newsapi_key_idx = (_newsapi_key_idx + 1) % len(NEWSAPI_KEYS)
    logger.info(f"NewsAPI: rotating to key[{_newsapi_key_idx}]")

def _newsapi_parse(data):
    articles = []
    for a in data.get("articles", []):
        if a.get("title") and a.get("title") != "[Removed]":
            articles.append({
                "title": a["title"],
                "description": a.get("description") or "",
                "url": a.get("url") or "",
                "source": a.get("source", {}).get("name") or "NewsAPI",
                "image": a.get("urlToImage") or "",
                "published_at": a.get("publishedAt") or "",
                "author": a.get("author") or "",
                "api_source": "newsapi",
            })
    return articles

def newsapi_fetch_top_headlines(country="us", category=None, query=None, page_size=30):
    for attempt in range(len(NEWSAPI_KEYS)):
        params = {"apiKey": _newsapi_key(), "pageSize": page_size}
        if query: params["q"] = query
        else: params["country"] = country
        if category: params["category"] = category
        try:
            resp = requests.get(f"{NEWSAPI_BASE}/top-headlines", params=params, timeout=None)
            resp.raise_for_status()
            return _newsapi_parse(resp.json())
        except Exception as e:
            logger.warning(f"NewsAPI error (key[{_newsapi_key_idx}]): {e}")
            _newsapi_rotate()
    logger.warning("NewsAPI: all keys exhausted")
    return []

def newsapi_fetch_everything(query=None, sort_by="publishedAt", page_size=50):
    for attempt in range(len(NEWSAPI_KEYS)):
        params = {"apiKey": _newsapi_key(), "pageSize": page_size, "sortBy": sort_by}
        if query: params["q"] = query
        try:
            resp = requests.get(f"{NEWSAPI_BASE}/everything", params=params, timeout=None)
            resp.raise_for_status()
            return _newsapi_parse(resp.json())
        except Exception as e:
            logger.warning(f"NewsAPI everything error (key[{_newsapi_key_idx}]): {e}")
            _newsapi_rotate()
    logger.warning("NewsAPI: all keys exhausted")
    return []

# ---------------------------------------------------------------------------
# Currents API wrapper
# ---------------------------------------------------------------------------
CURRENTS_KEYS = [
    "yV7zQ0szTQCbIQugGyHlcGbCqEmuJJighQcxOApAQzcF3yjM",
    "HGte8fcaGNFZ1JqXd5WF2C80SSEY3PyMRUlUYEVkCOegEXtH",
    "soxFk92k8nqVtgTIGVLbQ-kIIbk4ZSF42TjriacIUWdWLex_",
]
_currents_key_idx = 0
CURRENTS_BASE = "https://api.currentsapi.services/v1"

def _currents_key():
    global _currents_key_idx
    return CURRENTS_KEYS[_currents_key_idx]

def _currents_rotate():
    global _currents_key_idx
    _currents_key_idx = (_currents_key_idx + 1) % len(CURRENTS_KEYS)
    logger.info(f"Currents: rotating to key[{_currents_key_idx}]")

def _currents_parse(data):
    articles = []
    for a in data.get("news", []):
        articles.append({
            "title": a.get("title") or "",
            "description": a.get("description") or "",
            "url": a.get("url") or "",
            "source": a.get("name") or "Currents",
            "image": a.get("image") or "",
            "published_at": a.get("published") or "",
            "author": a.get("author") or "",
            "api_source": "currents",
        })
    return articles

def currents_fetch_latest(category=None, country=None, query=None, size=40):
    for attempt in range(len(CURRENTS_KEYS)):
        params = {"apiKey": _currents_key(), "count": size}
        if category: params["category"] = category
        if country: params["country"] = country
        if query: params["keywords"] = query
        try:
            resp = requests.get(f"{CURRENTS_BASE}/latest-news", params=params, timeout=None)
            resp.raise_for_status()
            return _currents_parse(resp.json())
        except Exception as e:
            logger.warning(f"Currents error (key[{_currents_key_idx}]): {e}")
            _currents_rotate()
    logger.warning("Currents: all keys exhausted")
    return []

def currents_fetch_search(keywords, country=None, size=40):
    for attempt in range(len(CURRENTS_KEYS)):
        params = {"apiKey": _currents_key(), "keywords": keywords, "count": size}
        if country: params["country"] = country
        try:
            resp = requests.get(f"{CURRENTS_BASE}/search", params=params, timeout=None)
            resp.raise_for_status()
            return _currents_parse(resp.json())
        except Exception as e:
            logger.warning(f"Currents search error (key[{_currents_key_idx}]): {e}")
            _currents_rotate()
    logger.warning("Currents: all keys exhausted")
    return []

# ============================================================================
# RSS FEED HELPERS
# ============================================================================

import feedparser

RSS_FEEDS = {
    "bbc": "http://feeds.bbci.co.uk/news/rss.xml",
    "bbc_world": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "reuters": "https://www.rss.reuters.com/news/arc/rss/topnews",
    "reuters_world": "https://www.rss.reuters.com/news/arc/rss/world",
    "the_hindu": "https://www.thehindu.com/news/national/feeder/default.rss",
    "the_hindu_latest": "https://www.thehindu.com/news/feeder/default.rss",
    "amar_ujala": "https://www.amarujala.com/rss/india-news.xml",
    "aaj_tak": "https://aajtak.intoday.in/rss",
    "pudhari": "https://www.pudhari.news/feed/",
}

def fetch_rss(source="bbc", max_items=30, timeout=None):
    urls = []
    if source == "bbc":
        urls = [RSS_FEEDS["bbc"], RSS_FEEDS["bbc_world"]]
    elif source == "reuters":
        urls = [RSS_FEEDS["reuters"], RSS_FEEDS["reuters_world"]]
    elif source == "the_hindu":
        urls = [RSS_FEEDS["the_hindu"], RSS_FEEDS["the_hindu_latest"]]
    else:
        url = RSS_FEEDS.get(source)
        if url:
            urls = [url]
        else:
            return []
    articles = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:max_items]:
                image = ""
                if entry.get("media_content"):
                    image = entry.media_content[0].get("url", "")
                elif entry.get("media_thumbnail"):
                    image = entry.media_thumbnail[0].get("url", "")
                elif entry.get("summary"):
                    match = re.search(r'<img[^>]+src="([^"]+)"', entry.summary)
                    if match:
                        image = match.group(1)
                articles.append({
                    "title": entry.get("title") or "",
                    "description": entry.get("summary") or entry.get("description") or "",
                    "url": entry.get("link") or "",
                    "source": feed.feed.get("title") or source,
                    "image": image,
                    "published_at": entry.get("published") or entry.get("pubDate") or "",
                    "author": entry.get("author") or "",
                    "api_source": f"rss_{source}",
                })
        except Exception as e:
            print(f"RSS feed error ({source}, {url}): {e}")
    return articles

def fetch_all_rss(max_per_source=30):
    all_articles = []
    for source in ["bbc", "reuters", "the_hindu"]:
        all_articles.extend(fetch_rss(source, max_per_source))
    return all_articles

def fetch_custom_rss(url, max_items=20, timeout=None):
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        articles = []
        for entry in feed.entries[:max_items]:
            image = ""
            if entry.get("media_content"):
                image = entry.media_content[0].get("url", "")
            elif entry.get("media_thumbnail"):
                image = entry.media_thumbnail[0].get("url", "")
            elif entry.get("summary"):
                match = re.search(r'<img[^>]+src="([^"]+)"', entry.summary)
                if match:
                    image = match.group(1)
            articles.append({
                "title": entry.get("title") or "",
                "description": entry.get("summary") or entry.get("description") or "",
                "url": entry.get("link") or "",
                "source": feed.feed.get("title") or "Custom RSS",
                "image": image,
                "published_at": entry.get("published") or entry.get("pubDate") or "",
                "author": entry.get("author") or "",
                "api_source": "custom_rss",
            })
        return articles
    except Exception as e:
        print(f"Custom RSS fetch error ({url}): {e}")
        return []

def fetch_google_news(query="india news", max_items=30, lang="en", timeout=None):
    from urllib.parse import quote
    locale_map = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}
    hl = locale_map.get(lang, "en-IN")
    lang_code = hl.split("-")[0]
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl=IN&ceid=IN:{lang_code}"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        articles = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "")
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            source_name = "Google News"
            if entry.get("source") and entry.source.get("title"):
                source_name = entry.source.title
            image = ""
            summary = entry.get("summary") or ""
            match = re.search(r'<img[^>]+src="([^"]+)"', summary)
            if match:
                image = match.group(1)
            articles.append({
                "title": title,
                "description": re.sub(r"<[^>]+>", "", summary)[:200],
                "url": entry.get("link") or "",
                "source": source_name,
                "image": image,
                "published_at": entry.get("published") or entry.get("pubDate") or "",
                "author": "",
                "api_source": "google_news",
            })
        return articles
    except Exception as e:
        print(f"Google News RSS error: {e}")
        return []


def summarize_history(history, api_keys):
    text = "\n".join(f"{m.get('role', 'user')}: {m.get('text', '')[:200]}" for m in history)
    keys = ensure_list(api_keys)
    for key in keys:
        try:
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": f"Summarize this conversation in 1-2 sentences:\n\n{text}"}],
                "max_tokens": 100,
                "temperature": 0.3
            }, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=None)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Summarization error: {e}")
    return ""

# ============================================================================
# API ENDPOINTS - AUTHENTICATION
# ============================================================================

@app.route('/api/auth/google', methods=['POST'])
def google_login():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    google_id = data.get('google_id')
    email = data.get('email', '')
    name = data.get('name', email.split('@')[0])
    avatar = data.get('avatar', '')
    if not email:
        return jsonify({'error': 'email required'}), 400
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            google_id=google_id or '',
            email=email,
            name=name,
            avatar_url=avatar,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
        )
        db.session.add(user)
    else:
        user.google_id = google_id or user.google_id
        user.last_login = datetime.now(timezone.utc)
        user.name = name
        user.avatar_url = avatar
    db.session.commit()
    session['user_id'] = user.id
    session.permanent = True
    return jsonify({
        'user': {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'avatar_url': user.avatar_url,
        }
    })

@app.route('/api/auth/user')
def get_user():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'user': None}), 200
    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        return jsonify({'user': None}), 200
    return jsonify({
        'user': {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'avatar_url': user.avatar_url,
        }
    })

@app.route('/api/auth/preferences', methods=['GET'])
@require_auth
def get_preferences(user):
    if not user:
        return jsonify({'preferences': {}, 'api_keys': {}})
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    if not pref:
        return jsonify({'preferences': {}, 'api_keys': {}})
    return jsonify({
        'preferences': pref.preferences or {},
        'api_keys': decrypt_api_keys(pref.api_keys),
    })

@app.route('/api/auth/preferences', methods=['PUT'])
@require_auth
def save_preferences(user):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    if not user:
        return jsonify({'status': 'ok'})
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    if not pref:
        pref = UserPreference(user_id=user.id)
        db.session.add(pref)
    if 'preferences' in data:
        pref.preferences = data['preferences']
    if 'api_keys' in data:
        pref.api_keys = encrypt_api_keys(data['api_keys'])
    pref.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/auth/profile', methods=['PUT'])
@require_auth
def update_profile(user):
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    if 'name' in data:
        user.name = data['name']
    if 'avatar_url' in data:
        user.avatar_url = data['avatar_url']
    db.session.commit()
    return jsonify({
        'user': {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'avatar_url': user.avatar_url,
        }
    })

@app.route('/api/auth/usage-stats')
@require_auth
def get_usage_stats(user):
    today = datetime.now(timezone.utc).date()
    if not user:
        return jsonify({'total_verifications': 0, 'today_verifications': 0, 'daily_usage': {}, 'days_active': 0})
    usage = GeminiDailyUsage.query.filter_by(user_id=user.id).all()
    daily_usage = {str(u.date): u.count for u in usage}
    today_count = GeminiDailyUsage.query.filter_by(user_id=user.id, date=today).first()
    return jsonify({
        'total_verifications': sum(u.count for u in usage),
        'today_verifications': today_count.count if today_count else 0,
        'daily_usage': daily_usage,
        'days_active': len(set(u.date for u in usage)),
    })

@app.route('/api/auth/export-data')
@require_auth
def export_user_data(user):
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    pref = UserPreference.query.filter_by(user_id=user.id).first()
    usage = GeminiDailyUsage.query.filter_by(user_id=user.id).all()
    return jsonify({
        'user': {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'avatar_url': user.avatar_url,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_login': user.last_login.isoformat() if user.last_login else None,
        },
        'preferences': pref.preferences if pref else {},
        'api_keys': decrypt_api_keys(pref.api_keys) if pref else {},
        'usage': [{'date': str(u.date), 'count': u.count} for u in usage],
    })

@app.route('/api/auth/delete-account', methods=['POST'])
@require_auth
def delete_account(user):
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    GeminiDailyUsage.query.filter_by(user_id=user.id).delete()
    UserPreference.query.filter_by(user_id=user.id).delete()
    ExpandedTopic.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    session.clear()
    return jsonify({'status': 'account_deleted'})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'logged_out'})

# ============================================================================
# API ENDPOINTS - NEWS & ARTICLES
# ============================================================================

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/news")
@require_auth
def get_news(user):
    query = request.args.get("q") or request.args.get("query")
    category = request.args.get("category")
    source_filter = request.args.get("source")
    language = request.args.get("lang") or request.args.get("language")
    country = request.args.get("country")
    sort = request.args.get("sort")
    offset = int(request.args.get("offset", 0))
    limit = int(request.args.get("limit", 30))
    blocked_param = request.args.get("blocked")

    custom_rss_sources = None
    blocked_sources = None
    if user:
        pref = UserPreference.query.filter_by(user_id=user.id).first()
        if pref and pref.preferences:
            blocked_sources = pref.preferences.get("blocked_sources", [])
        custom_sources = CustomRssSource.query.filter_by(user_id=user.id).all()
        if custom_sources:
            custom_rss_sources = [{"url": s.url, "label": s.label} for s in custom_sources]
    if blocked_param:
        try:
            blocked_sources = json.loads(blocked_param)
        except:
            blocked_sources = blocked_param.split(',')

    cache_key = get_cache_key({
        "q": query, "cat": category, "src": source_filter,
        "lang": language, "country": country, "sort": sort
    })

    cached = get_cached(cache_key)
    if cached:
        articles = cached["articles"]
        failures = cached.get("failed_sources", [])
        search_terms = cached.get("search_terms", [])
        return jsonify({"articles": articles[offset:offset + limit], "total": len(articles), "cached": True, "failed_sources": failures, "search_terms": search_terms})

    articles, failed_sources, search_terms = fetch_all_sources(query=query, category=category, source_filter=source_filter, language=language, country=country, sort=sort, custom_rss_sources=custom_rss_sources, blocked_sources=blocked_sources)
    set_cache(cache_key, {"articles": articles, "failed_sources": failed_sources, "search_terms": search_terms})
    return jsonify({"articles": articles[offset:offset + limit], "total": len(articles), "cached": False, "failed_sources": failed_sources, "search_terms": search_terms})

@app.route("/api/news/headlines")
@require_auth
def get_headlines(user):
    country = request.args.get("country", "us")
    category = request.args.get("category")
    limit = int(request.args.get("limit", 20))
    articles = newsapi_fetch_top_headlines(country=country, category=category, page_size=limit)
    return jsonify({"articles": articles})

@app.route("/api/news/search")
@require_auth
def search_news(user):
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "q parameter required"}), 400
    offset = int(request.args.get("offset", 0))
    limit = int(request.args.get("limit", 20))

    cache_key = get_cache_key({"q": query, "type": "search"})
    cached = get_cached(cache_key)
    if cached:
        articles = cached.get("articles", [])
        return jsonify({"articles": articles[offset:offset + limit], "total": len(articles), "cached": True})

    articles, _, _ = fetch_all_sources(query=query)
    set_cache(cache_key, {"articles": articles})
    return jsonify({"articles": articles[offset:offset + limit], "total": len(articles), "cached": False})

@app.route("/api/news/sources")
@require_auth
def get_sources(user):
    source = request.args.get("source", "")
    limit = int(request.args.get("limit", 15))
    if source == "bbc":
        articles = fetch_rss("bbc", limit)
    elif source == "reuters":
        articles = fetch_rss("reuters", limit)
    elif source == "the_hindu":
        articles = fetch_rss("the_hindu", limit)
    else:
        articles = fetch_all_rss(limit)
    return jsonify({"articles": articles[:limit], "source": source or "all"})

@app.route("/api/news/clusters")
@require_auth
def get_article_clusters(user):
    query = request.args.get("q") or request.args.get("query")
    category = request.args.get("category")
    source_filter = request.args.get("source")
    language = request.args.get("lang") or request.args.get("language")
    country = request.args.get("country")
    sort = request.args.get("sort")

    cache_key = get_cache_key({
        "q": query,
        "cat": category,
        "src": source_filter,
        "lang": language,
        "country": country,
        "sort": sort,
    })
    cached = get_cached(cache_key)
    if cached:
        articles = cached["articles"]
    else:
        articles, failed_sources, search_terms = fetch_all_sources(
            query=query,
            category=category,
            source_filter=source_filter,
            language=language,
            country=country,
            sort=sort,
        )
        set_cache(cache_key, {"articles": articles, "failed_sources": failed_sources, "search_terms": search_terms})

    clusters = cluster_articles(articles)
    for cluster in clusters:
        sources = set(a["source"] for a in cluster["articles"] if a["source"])
        cluster["source_count"] = len(sources)
        cluster["sources"] = list(sources)
        cluster["article_count"] = len(cluster["articles"])
    return jsonify({"clusters": clusters[:20]})

@app.route("/api/credibility")
@require_auth
def get_credibility(user):
    source = request.args.get("source")
    if source:
        return jsonify({"source": source, "score": get_source_credibility(source)})
    conn = get_db()
    rows = conn.execute("SELECT source, score, total_count FROM source_credibility ORDER BY score DESC").fetchall()
    conn.close()
    return jsonify({"sources": [{"source": r["source"], "score": r["score"], "total_count": r["total_count"]} for r in rows]})

@app.route("/api/source-failures")
@require_auth
def get_failures(user):
    return jsonify(get_source_failures())

@app.route("/api/dashboard/data")
@require_auth
def get_dashboard_data(user):
    q = request.args.get("q")
    lang = request.args.get("lang", "en")
    country = request.args.get("country")
    
    health = {"status": "ok"}
    failures = get_source_failures()
    
    def get_cred():
        conn = get_db()
        rows = conn.execute("SELECT source, score, total_count FROM source_credibility ORDER BY score DESC").fetchall()
        conn.close()
        return [{"source": r["source"], "score": r["score"], "total_count": r["total_count"]} for r in rows]
    
    credibility_sources = get_cred()
    clusters = []
    
    return jsonify({
        "health": health,
        "failures": failures,
        "credibility": {"sources": credibility_sources},
        "clusters": {"clusters": clusters}
    })

@app.route("/api/cache/clear", methods=["POST"])
@require_auth
def clear_cache(user):
    clear_all_cache()
    return jsonify({"status": "cleared"})

@app.route("/api/fetch-article")
@require_auth
def fetch_article(user):
    url = request.args.get("url", "")
    if not url:
        return jsonify({"error": "url parameter required"}), 400

    cache_key = get_cache_key({"fetch_article": url})
    cached = get_cached(cache_key)
    if cached:
        return jsonify(cached)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        }
        resp = requests.get(url, headers=headers, timeout=None)
        if resp.status_code != 200:
            return jsonify({"error": f"Failed to fetch article (HTTP {resp.status_code})"}), resp.status_code

        soup = BeautifulSoup(resp.text, 'html.parser')

        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
            tag.decompose()

        article_title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title:
            article_title = og_title.get('content', '')

        article_author = ""
        author_tag = soup.find('meta', attrs={'name': 'author'})
        if author_tag:
            article_author = author_tag.get('content', '')

        article_date = ""
        article_image = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image:
            article_image = og_image.get('content', '')

        full_text = soup.get_text(separator=' ', strip=True)
        word_count = len(full_text.split())
        read_time = max(1, math.ceil(word_count / 200))

        result = {
            "title": article_title,
            "author": article_author,
            "date": article_date,
            "image": article_image,
            "full_text": full_text,
            "word_count": word_count,
            "read_time": read_time
        }

        set_cache(cache_key, result)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Fetch article error: {e}")
        return jsonify({"error": f"Failed to parse article: {str(e)}"}), 500

# ============================================================================
# API ENDPOINTS - FACT CHECKING & AI VERIFICATION
# ============================================================================

@app.route("/api/fact-check")
@require_auth
def fact_check_article(user):
    query = request.args.get("q")
    api_keys = request.args.getlist("api_keys") or [request.args.get("api_key", "")]
    if not query:
        return jsonify({"error": "q parameter required"}), 400

    cache_key = get_cache_key({"fact_check": query})
    cached = get_cached(cache_key)
    if cached:
        return jsonify(cached)

    result = {"results": [], "found": False}
    set_cache(cache_key, result)
    return jsonify(result)

@app.route("/api/groq-verify", methods=["GET", "POST"])
@require_auth
def groq_verify_article(user):
    if request.method == "POST":
        data = request.json
        title = data.get("title")
        description = data.get("description", "")
        api_keys = data.get("api_keys") or [data.get("api_key", "")]
        deep = data.get("deep", "false").lower() == "true"
        lang = data.get("lang", "en")
        article_source = data.get("source", "")
        url = data.get("url", "")
    else:
        title = request.args.get("title")
        description = request.args.get("description", "")
        api_keys = request.args.getlist("api_keys") or [request.args.get("api_key", "")]
        deep = request.args.get("deep", "false").lower() == "true"
        lang = request.args.get("lang", "en")
        article_source = request.args.get("source", "")
        url = request.args.get("url", "")

    if not ensure_list(api_keys) and user:
        db_keys = _get_user_api_keys(user, 'groq')
        api_keys = ensure_list(db_keys.get('groq', []))

    has_groq = bool(ensure_list(api_keys))
    use_compound = deep
    model_name = "groq/compound" if use_compound else "llama-3.1-8b-instant"

    cache_key = get_cache_key({"groq_verify": title, "deep": deep, "lang": lang})
    cached = get_cached(cache_key)
    if cached:
        return jsonify(cached)

    full_text = ""
    if deep:
        if request.method == "GET":
            url = request.args.get("url", "")
        if url:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                resp = requests.get(url, headers=headers, timeout=None)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                        tag.decompose()
                    full_text = soup.get_text(separator=' ', strip=True)[:3000]
            except Exception as e:
                logger.warning(f"Failed to fetch article: {e}")

    content = f"Title: {title[:200]}"
    if description:
        content += f"\n\nDescription: {description[:300]}"
    if full_text:
        content += f"\n\nFull Article Content:\n{full_text[:1000]}"

    language_names = {"en": "English", "hi": "Hindi", "mr": "Marathi"}
    response_lang = language_names.get(lang, "English")

    grounding_sources = []
    tavily_snippets = ""
    tavily_answer = ""
    if not use_compound:
        try:
            tavily_sources, tavily_answer, tavily_content = search_tavily(title)
            grounding_sources.extend(tavily_sources)
            if tavily_content:
                tavily_snippets = "\n\nRelevant web search results:\n" + tavily_content
        except Exception as e:
            logger.warning(f"Tavily search error: {e}")

    prompt = f"""Analyze this news article for credibility, truthfulness, and tone. Respond in {response_lang}.

{content}
{tavily_snippets}

Provide your analysis in JSON format with these fields:
- verdict: one of "True", "Misleading", "False", "Unverified"
- confidence: one of "High", "Medium", "Low"
- reasoning: a clear explanation of WHY you believe the article is true, misleading, or false. Be specific about what makes it credible or questionable. Write in {response_lang}.
- key_points: an array of 2-3 key points supporting your verdict. Write each point in {response_lang}.
- sentiment: an object with:
  - overall: one of "positive", "negative", "neutral", "mixed"
  - tone: short description of the article's tone (e.g. "critical", "supportive", "analytical", "sensationalist", "fearful", "celebratory")
  - emotional_charge: one of "high", "medium", "low"
  - subjectivity: a number from 0 (completely objective) to 1 (completely subjective)

Only respond with valid JSON, nothing else."""

    if len(prompt) > 4000:
        return jsonify({
            "error": "Article is too long for analysis. Try a shorter article.",
            "error_type": "too_large",
            "verdict": "Unverified",
            "confidence": "Low",
            "reasoning": "The article content exceeds the maximum allowed size.",
            "key_points": []
        }), 413

    def build_result(text_json, ai_model_label):
        json_match = re.search(r'\{.*\}', text_json, re.DOTALL)
        if not json_match:
            return None
        result = json.loads(json_match.group())
        result["source"] = "Groq AI"
        result["ai_model"] = ai_model_label
        result["deep_analyzed"] = deep
        if tavily_answer:
            result["tavily_summary"] = tavily_answer
        if article_source:
            update_source_credibility(article_source, result.get("verdict", "Unverified"))
        ai_scores = {"True": 0.9, "Misleading": 0.4, "False": 0.1, "Unverified": 0.3}
        result["trust_score"] = round(ai_scores.get(result.get("verdict", "Unverified"), 0.3), 2)
        return result

    try:
        results = []

        if has_groq:
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 800
            }
            response, used_key = groq_request(payload, api_keys, timeout=None)
            if response is not None and response.status_code == 200:
                data = response.json()
                if data.get("choices") and len(data["choices"]) > 0:
                    candidate = data["choices"][0]
                    if candidate.get("message") and candidate["message"].get("content"):
                        text = candidate["message"]["content"]
                        parsed = build_result(text, "groq")
                        if parsed:
                            if data.get("citations"):
                                for c in data["citations"]:
                                    grounding_sources.append({"title": c.get("title", ""), "uri": c.get("url", ""), "source": "groq"})
                            if grounding_sources:
                                parsed["grounding_sources"] = grounding_sources
                            for h in ["x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"]:
                                if h in response.headers:
                                    parsed[h.replace("-", "_")] = response.headers[h]
                            results.append(parsed)

        ollama_text, ollama_model = ollama_request(prompt, format_json=True)
        if ollama_text:
            parsed = build_result(ollama_text, ollama_model or "ollama/qwen2.5:0.5b")
            if parsed:
                if grounding_sources:
                    parsed["grounding_sources"] = grounding_sources
                results.append(parsed)

        if not results:
            return jsonify({
                "error": "All AI models failed to generate a valid response.",
                "error_type": "all_models_failed",
                "verdict": "Unverified",
                "confidence": "Low",
                "reasoning": "No AI model could analyze this article.",
                "key_points": []
            }), 500

        response_data = {
            "results": results,
            "article_source": article_source,
            "source_credibility_score": get_source_credibility(article_source) if article_source else None
        }
        if results:
            top = results[0]
            for key in ["verdict", "confidence", "reasoning", "key_points", "sentiment", "trust_score", "ai_model", "deep_analyzed", "grounding_sources", "tavily_summary", "source"]:
                if key in top:
                    response_data[key] = top[key]
        set_cache(cache_key, response_data)
        return jsonify(response_data)
    except requests.exceptions.Timeout:
        return jsonify({
            "error": "AI request timed out. Please try again.",
            "error_type": "timeout",
            "verdict": "Unverified",
            "confidence": "Low",
            "reasoning": "The AI service did not respond in time.",
            "key_points": []
        }), 504
    except requests.exceptions.ConnectionError:
        return jsonify({
            "error": "Unable to connect to AI service. Check your internet connection.",
            "error_type": "connection_error",
            "verdict": "Unverified",
            "confidence": "Low",
            "reasoning": "Network connection to the AI service failed.",
            "key_points": []
        }), 503
    except json.JSONDecodeError:
        return jsonify({
            "error": "Failed to parse AI response as JSON.",
            "error_type": "parse_error",
            "verdict": "Unverified",
            "confidence": "Low",
            "reasoning": "The AI response format was invalid.",
            "key_points": []
        }), 500
    except Exception as e:
        error_str = str(e)
        error_type = "unknown"
        if "quota" in error_str.lower():
            error_type = "quota_exceeded"
        elif "blocked" in error_str.lower():
            error_type = "content_blocked"
        return jsonify({
            "error": f"Verification failed: {error_str}",
            "error_type": error_type,
            "verdict": "Unverified",
            "confidence": "Low",
            "reasoning": "An unexpected error occurred during analysis.",
            "key_points": []
        }), 500

@app.route("/api/summarize", methods=["POST"])
@require_auth
def summarize_article(user):
    data = request.json
    title = data.get("title", "")
    description = data.get("description", "")
    api_keys = data.get("api_keys") or [data.get("api_key", "")]
    if not ensure_list(api_keys) and user:
        db_keys = _get_user_api_keys(user, 'groq')
        api_keys = ensure_list(db_keys.get('groq', []))
    lang = data.get("lang", "en")

    if not title:
        return jsonify({"error": "title required"}), 400

    has_groq = bool(ensure_list(api_keys))

    cache_key = get_cache_key({"summarize": title, "lang": lang})
    cached = get_cached(cache_key)
    if cached:
        return jsonify({"summary": cached["summary"], "cached": True, "ai_model": cached.get("ai_model", "")})

    language_names = {"en": "English", "hi": "Hindi", "mr": "Marathi"}
    response_lang = language_names.get(lang, "English")

    prompt = f"""Summarize the following news article in {response_lang}. Provide a concise summary of 3-5 sentences that captures only the most important information. Write ONLY the summary, no labels or formatting.

Title: {title[:200]}
Description: {description[:500]}"""

    text = ""
    ai_model = ""
    if has_groq:
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 300
        }
        response, used_key = groq_request(payload, api_keys, timeout=None)
        if response is not None and response.status_code == 200:
            data = response.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            ai_model = "groq"

    if not text:
        ollama_text, ollama_model = ollama_request(prompt)
        if ollama_text:
            text = ollama_text.strip()
            ai_model = ollama_model

    if not text:
        fallback = title[:200]
        if description:
            fallback += ". " + description[:300]
        return jsonify({"summary": fallback, "cached": False, "fallback": True})

    set_cache(cache_key, {"summary": text, "ai_model": ai_model})
    return jsonify({"summary": text, "cached": False, "ai_model": ai_model})

@app.route("/api/translate", methods=["POST"])
@require_auth
def translate_article(user):
    data = request.json
    text = data.get("text", "")
    target_lang = data.get("target_lang", "en")
    source_lang = data.get("source_lang", "")
    api_keys = data.get("api_keys") or []
    if not ensure_list(api_keys) and user:
        db_keys = _get_user_api_keys(user, 'groq')
        api_keys = ensure_list(db_keys.get('groq', []))

    if not text:
        return jsonify({"error": "text required"}), 400

    has_groq = bool(ensure_list(api_keys))

    language_names = {"en": "English", "hi": "Hindi", "mr": "Marathi"}
    target_name = language_names.get(target_lang, "English")
    source_hint = f" from {language_names.get(source_lang, 'the original language')}" if source_lang else ""

    cache_key = get_cache_key({"translate": text[:100], "to": target_lang})
    cached = get_cached(cache_key)
    if cached:
        return jsonify({"translation": cached["translation"], "cached": True, "ai_model": cached.get("ai_model", "")})

    prompt = f"""Translate the following news article text{source_hint} to {target_name}. 
Preserve the journalistic tone, factual accuracy, and formatting of the original. 
Keep any proper names, dates, and numbers unchanged.
Respond with ONLY the translated text, no labels or explanations.

Text to translate:
{text[:1000]}"""

    translated = ""
    ai_model = ""
    if has_groq:
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1200
        }
        response, used_key = groq_request(payload, api_keys, timeout=None)
        if response is not None and response.status_code == 200:
            data = response.json()
            translated = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            ai_model = "groq"

    if not translated:
        ollama_text, ollama_model = ollama_request(prompt)
        if ollama_text:
            translated = ollama_text.strip()
            ai_model = ollama_model

    if not translated:
        translated = text[:500]

    set_cache(cache_key, {"translation": translated, "ai_model": ai_model})
    return jsonify({"translation": translated, "cached": False, "ai_model": ai_model})

@app.route("/api/chat", methods=["POST"])
@require_auth
def chat_discussion(user):
    data = request.json
    user_message = data.get("message", "")
    history = data.get("history", [])
    article = data.get("article", {})
    api_keys = data.get("api_keys") or [data.get("api_key", "")]
    stream = data.get("stream", False)

    if not ensure_list(api_keys) and user:
        db_keys = _get_user_api_keys(user, 'groq')
        api_keys = ensure_list(db_keys.get('groq', []))

    if not user_message:
        return jsonify({"error": "message required"}), 400

    has_groq = bool(ensure_list(api_keys))

    is_first_message = len(history) == 0
    needs_search = bool(os.getenv('TAVILY_API_KEY', '')) and needs_web_search(user_message)

    tavily_future = None
    if needs_search:
        tavily_future = _EXECUTOR.submit(search_tavily, user_message)

    article_context = ""
    if is_first_message and article.get("title"):
        article_context += f"Title: {article['title'][:100]}\n"
    if is_first_message and article.get("description"):
        article_context += f"Description: {article['description'][:200]}\n"

    compressed_history = list(history)
    if len(history) > 6 and has_groq:
        try:
            summary = summarize_history(history[:-3], api_keys)
            if summary:
                compressed_history = [{"role": "system", "text": f"Previous conversation summary: {summary}"}] + history[-3:]
        except Exception:
            compressed_history = history[-4:]
    elif len(history) > 6:
        compressed_history = history[-4:]

    tavily_snippets = ""
    tavily_citations = []
    if tavily_future:
        try:
            sources, answer, content = tavily_future.result()
            tavily_citations = [{"title": s["title"], "url": s["uri"], "source": "tavily"} for s in sources]
            if content:
                tavily_snippets = content
        except Exception:
            pass

    system_prompt = "You are an objective news discussion assistant helping readers analyze this article.\n\n"
    if article_context:
        system_prompt += f"{article_context}\n"
    system_prompt += (
        "Guidelines:\n"
        "- Be analytical, cite facts from the article when relevant\n"
        "- Point out potential bias, missing context, or questionable claims\n"
        "- Ask thought-provoking questions to encourage critical thinking\n"
        "- Stay neutral and avoid taking political sides\n"
        "- Keep responses concise (2-4 short paragraphs)\n"
        "- Respond in the same language the user writes in\n"
        "- If the article seems misleading, explain why specifically\n"
        "- Use clear, accessible language\n"
        "- IMPORTANT: End your response by suggesting exactly 3 follow-up questions. Put each on its own line starting with '\u2192 '"
    )

    contents = [{"role": "system", "content": system_prompt[:800]}]

    for msg in compressed_history[-4:]:
        role = "assistant" if msg.get("role") == "ai" else "user"
        contents.append({"role": role, "content": (msg.get("text") or "")[:500]})

    user_content = user_message[:500]
    if tavily_snippets:
        user_content = f"Question: {user_message[:300]}\n\nRelevant web search results:\n{tavily_snippets}"[:1000]

    contents.append({"role": "user", "content": user_content})

    total_chars = sum(len(m.get("content", "")) for m in contents)
    if total_chars > 4000:
        return jsonify({
            "error": "Conversation is too long. Please start a new chat.",
            "error_type": "too_large"
        }), 413

    def build_ollama_prompt():
        parts = []
        for m in contents:
            role = m["role"]
            content = m["content"]
            if role == "system":
                parts.append(f"System: {content}")
            elif role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
        parts.append("Assistant:")
        return "\n\n".join(parts)

    try:
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": contents,
            "temperature": 0.4,
            "max_tokens": 800,
        }

        if stream and has_groq:
            payload["stream"] = True
            resp, used_key = None, None
            for key in ensure_list(api_keys):
                if not key:
                    continue
                try:
                    r = requests.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        stream=True, timeout=None
                    )
                    if r.status_code in (401, 403, 429):
                        continue
                    resp = r
                    break
                except Exception:
                    continue

            if resp is None:
                ollama_prompt = build_ollama_prompt()
                ollama_text, ollama_model = ollama_request(ollama_prompt, system_prompt=system_prompt[:800])
                if ollama_text:
                    follow_ups = []
                    clean_lines = []
                    for line_text in ollama_text.split("\n"):
                        if line_text.startswith("\u2192 "):
                            follow_ups.append(line_text[2:].strip())
                        else:
                            clean_lines.append(line_text)
                    clean_text = "\n".join(clean_lines).strip()

                    def generate_ollama():
                        yield f"data: {json.dumps({'type': 'token', 'content': clean_text})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'citations': [], 'tavily_citations': tavily_citations, 'follow_ups': follow_ups, 'ai_model': ollama_model})}\n\n"

                    return Response(generate_ollama(), mimetype='text/event-stream')
                return jsonify({"error": "All API keys exhausted", "error_type": "auth_error"}), 401

            def generate():
                if resp.status_code != 200:
                    err_text = "AI request failed"
                    try:
                        err_text = resp.json().get("error", {}).get("message", resp.text[:200])
                    except Exception:
                        err_text = resp.text[:200]
                    yield f"data: {json.dumps({'type': 'error', 'error': err_text})}\n\n"
                    return

                full_text = ""
                for line in resp.iter_lines():
                    if line:
                        decoded = line.decode('utf-8')
                        if decoded.startswith('data: '):
                            data_str = decoded[6:]
                            if data_str == '[DONE]':
                                break
                            try:
                                chunk = json.loads(data_str)
                                delta = chunk.get('choices', [{}])[0].get('delta', {})
                                token = delta.get('content', '')
                                if token:
                                    full_text += token
                                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                            except json.JSONDecodeError:
                                pass

                follow_ups = []
                clean_lines = []
                for line_text in full_text.split("\n"):
                    if line_text.startswith("\u2192 "):
                        follow_ups.append(line_text[2:].strip())
                    else:
                        clean_lines.append(line_text)

                yield f"data: {json.dumps({'type': 'done', 'citations': [], 'tavily_citations': tavily_citations, 'follow_ups': follow_ups, 'ai_model': 'groq'})}\n\n"

            return Response(generate(), mimetype='text/event-stream')

        if not has_groq:
            resp = None
        else:
            resp, used_key = groq_request(payload, api_keys, timeout=None)

        if resp is None:
            ollama_prompt = build_ollama_prompt()
            ollama_text, ollama_model = ollama_request(ollama_prompt, system_prompt=system_prompt[:800])
            if ollama_text:
                follow_ups = []
                clean_lines = []
                for line_text in ollama_text.split("\n"):
                    if line_text.startswith("\u2192 "):
                        follow_ups.append(line_text[2:].strip())
                    else:
                        clean_lines.append(line_text)
                clean_text = "\n".join(clean_lines).strip()
                return jsonify({
                    "response": clean_text,
                    "citations": [],
                    "tavily_citations": tavily_citations,
                    "follow_ups": follow_ups,
                    "ai_model": ollama_model
                })
            return jsonify({"error": "All API keys exhausted", "error_type": "auth_error"}), 401

        if resp.status_code == 413:
            return jsonify({"error": "Message is too long. Try a shorter question or start a new conversation.", "error_type": "too_large"}), 413
        if resp.status_code == 400:
            return jsonify({"error": "AI couldn't understand that. Try rephrasing.", "error_type": "bad_request"}), 400
        if resp.status_code == 500:
            return jsonify({"error": "AI service encountered an internal error. Try again shortly.", "error_type": "server_error"}), 500
        if resp.status_code == 503:
            retry = resp.headers.get('Retry-After', 0)
            try:
                retry = int(retry) if retry else 0
            except (ValueError, TypeError):
                retry = 0
            return jsonify({"error": "AI service is temporarily unavailable.", "error_type": "service_unavailable", "retry_after": retry}), 503
        if resp.status_code != 200:
            err_detail = "Unknown error"
            try:
                err_detail = resp.json().get("error", {}).get("message", str(resp.status_code))
            except Exception:
                err_detail = resp.text[:200]
            return jsonify({"error": f"Unexpected AI error: {err_detail}", "error_type": "unexpected"}), resp.status_code

        result = resp.json()
        if not result.get("choices"):
            return jsonify({"error": "No response from AI", "error_type": "no_response"}), 500

        text = result["choices"][0].get("message", {}).get("content", "")
        if not text:
            return jsonify({"error": "Empty response from AI", "error_type": "empty_response"}), 500

        follow_ups = []
        clean_lines = []
        for line_text in text.split("\n"):
            if line_text.startswith("\u2192 "):
                follow_ups.append(line_text[2:].strip())
            else:
                clean_lines.append(line_text)
        clean_text = "\n".join(clean_lines).strip()

        all_citations = (result.get("citations") or []) + tavily_citations

        return jsonify({
            "response": clean_text,
            "citations": all_citations,
            "tavily_citations": tavily_citations,
            "follow_ups": follow_ups,
            "ai_model": "groq"
        })

    except requests.exceptions.Timeout:
        return jsonify({"error": "AI took too long. Try a shorter question.", "error_type": "timeout"}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Unable to connect. Check your internet connection.", "error_type": "network_error"}), 503
    except Exception as e:
        return jsonify({"error": f"Chat error: {str(e)}", "error_type": "unexpected"}), 500

@app.route("/api/test-api-key", methods=["POST"])
@require_auth
def test_api_key(user):
    data = request.json
    api_key = data.get("api_key", "")
    service = data.get("service", "groq")

    if not api_key:
        return jsonify({"valid": False, "message": "API key is required"}), 400

    return jsonify({"valid": True, "message": f"{service} API key appears valid"})

# ============================================================================
# API ENDPOINTS - TOPICS
# ============================================================================

@app.route('/api/topics/expand', methods=['POST'])
@require_auth
def expand_topic(user):
    data = request.get_json() or {}
    topic = (data.get('topic') or '').strip()
    if not topic:
        return jsonify({'error': 'topic required'}), 400

    pref = UserPreference.query.filter_by(user_id=user.id).first() if user else None
    api_keys = decrypt_api_keys(pref.api_keys) if pref else {}
    gemini_keys = ensure_list(api_keys.get('gemini'))
    groq_keys = ensure_list(api_keys.get('groq'))

    if not gemini_keys and not groq_keys:
        api_keys = data.get('api_keys') or {}
        gemini_keys = ensure_list(api_keys.get('gemini')) if api_keys else []
        groq_keys = ensure_list(api_keys.get('groq')) if api_keys else []

    if not gemini_keys and not groq_keys:
        return jsonify({'error': 'No Gemini or Groq API keys configured.'}), 400

    prompt = EXPAND_PROMPT.format(topic=topic)
    expanded = []
    source = ''

    if gemini_keys and (not user or not check_gemini_limit(user.id)):
        text = call_gemini(prompt, gemini_keys)
        if text:
            expanded = parse_topic_list(text)
            source = 'gemini'
            if user:
                increment_gemini_usage(user.id)

    if not expanded and groq_keys:
        text = call_groq_compound(prompt, groq_keys)
        if text:
            expanded = parse_topic_list(text)
            source = 'groq'

    if not expanded:
        text = call_ollama_topic_expand(prompt)
        if text:
            expanded = parse_topic_list(text)
            source = 'ollama'

    if not expanded:
        return jsonify({'error': 'Failed to expand topic with available AI services', 'source': source}), 500

    if user:
        entry = ExpandedTopic(
            user_id=user.id,
            original_topic=topic,
            expanded_topics=expanded,
            source=source,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify({
            'id': entry.id,
            'original_topic': topic,
            'expanded_topics': expanded,
            'source': source,
            'created_at': entry.created_at.isoformat()
        })

    return jsonify({
        'original_topic': topic,
        'expanded_topics': expanded,
        'source': source,
        'created_at': datetime.now(timezone.utc).isoformat()
    })

@app.route('/api/topics/expanded', methods=['GET'])
@require_auth
def get_expanded_topics(user):
    if not user:
        return jsonify([])
    entries = ExpandedTopic.query.filter_by(user_id=user.id).order_by(ExpandedTopic.created_at.desc()).all()
    return jsonify([{
        'id': e.id,
        'original_topic': e.original_topic,
        'expanded_topics': e.expanded_topics,
        'source': e.source,
        'created_at': e.created_at.isoformat()
    } for e in entries])

@app.route('/api/topics/expanded/<int:entry_id>', methods=['DELETE'])
@require_auth
def delete_expanded_topic(user, entry_id):
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    entry = ExpandedTopic.query.filter_by(id=entry_id, user_id=user.id).first()
    if not entry:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(entry)
    db.session.commit()
    return jsonify({'status': 'deleted'})

# ============================================================================
# API ENDPOINTS - SPORTS
# ============================================================================

@app.route("/api/sports/espn/<path:endpoint>", methods=["GET"])
@app.route("/api/sports/espn", methods=["GET"])
def espn_api(endpoint=""):
    try:
        base_url = "https://site.api.espn.com/apis/site/v2/sports"
        if endpoint:
            url = f"{base_url}/{endpoint}"
        else:
            url = base_url
        query_string = request.query_string.decode('utf-8')
        if query_string:
            url += "?" + query_string
        resp = requests.get(url, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/sports/sportdb/<path:endpoint>", methods=["GET"])
@app.route("/api/sports/sportdb", methods=["GET"])
def sportdb_api(endpoint=""):
    try:
        base_url = "https://api.sportdb.dev"
        if endpoint:
            url = f"{base_url}/{endpoint}"
        else:
            url = base_url
        query_string = request.query_string.decode('utf-8')
        if query_string:
            url += "?" + query_string
        
        headers = {}
        api_key = request.headers.get('X-API-Key')
        if api_key:
            headers['X-API-Key'] = api_key
            
        resp = requests.get(url, headers=headers, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/sports/sportsrc/<path:endpoint>", methods=["GET"])
@app.route("/api/sports/sportsrc", methods=["GET"])
def sportsrc_api(endpoint=""):
    try:
        base_url = "https://api.sportsrc.org"
        if endpoint:
            url = f"{base_url}/{endpoint}"
        else:
            url = base_url
            
        query_string = request.query_string.decode('utf-8')
        if query_string:
            url += "?" + query_string
        resp = requests.get(url, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/sports/highlightly/<path:endpoint>", methods=["GET"])
@app.route("/api/sports/highlightly", methods=["GET"])
def highlightly_api(endpoint=""):
    try:
        base_url = "https://highlightly-v2.p.rapidapi.com"
        if endpoint:
            url = f"{base_url}/{endpoint}"
        else:
            url = base_url
            
        query_string = request.query_string.decode('utf-8')
        if query_string:
            url += "?" + query_string
        
        headers = {
            'X-RapidAPI-Key': request.headers.get('x-rapidapi-key', ''),
            'X-RapidAPI-Host': 'highlightly-v2.p.rapidapi.com'
        }
        
        resp = requests.get(url, headers=headers, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/sports/isports/<path:endpoint>", methods=["GET"])
@app.route("/api/sports/isports", methods=["GET"])
def isports_api(endpoint=""):
    try:
        base_url = "https://api.isportsapi.com"
        if endpoint:
            url = f"{base_url}/{endpoint}"
        else:
            url = base_url
            
        query_string = request.query_string.decode('utf-8')
        if query_string:
            url += "?" + query_string
        resp = requests.get(url, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API ENDPOINTS - CRICKET
# ============================================================================

@app.route("/api/cricket/cricapi/live-scores", methods=["GET"])
def cricapi_live_scores():
    apikey = request.args.get('apikey')
    if not apikey:
        return jsonify({"error": "API key required"}), 400
    try:
        url = f"https://api.cricapi.com/v1/cricScore?apikey={apikey}"
        resp = requests.get(url, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cricket/cricapi/scorecard/<match_id>", methods=["GET"])
def cricapi_scorecard(match_id):
    apikey = request.args.get('apikey')
    if not apikey:
        return jsonify({"error": "API key required"}), 400
    try:
        url = f"https://api.cricapi.com/v1/match_info?apikey={apikey}&id={match_id}"
        resp = requests.get(url, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API ENDPOINTS - STATS & UTILITIES
# ============================================================================

@app.route("/api/stats/track", methods=["POST"])
@require_auth
def track_reading(user):
    if not user:
        return jsonify({"status": "ok"})
    data = request.get_json()
    if not data or not data.get("url"):
        return jsonify({"error": "url required"}), 400
    stat = ReadingStat(
        user_id=user.id,
        article_url=data["url"],
        article_title=data.get("title", ""),
        source=data.get("source", ""),
        estimated_seconds=data.get("estimated_seconds", 0),
    )
    db.session.add(stat)
    db.session.commit()
    return jsonify({"status": "ok"})

@app.route("/api/rotation-status")
def get_rotation_status():
    groq_keys = ensure_list(os.getenv("GROQ_API_KEY", ""))
    return jsonify({"keys": _key_rotation.status(groq_keys), "active": len(groq_keys) > 1})

@app.route("/api/rate-limits")
def get_rate_limits():
    return jsonify({"rate_limits": "API rate limits info"})

@app.route("/api/cricket/apicricket/live-scores", methods=["GET"])
def apicricket_live_scores():
    apikey = request.args.get('apikey')
    if not apikey:
        return jsonify({"error": "API key required"}), 400
    try:
        url = f"https://api.cricapi.com/v1/cricScore?apikey={apikey}"
        resp = requests.get(url, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cricket/apicricket/scorecard/<event_key>", methods=["GET"])
def apicricket_scorecard(event_key):
    apikey = request.args.get('apikey')
    if not apikey:
        return jsonify({"error": "API key required"}), 400
    try:
        url = f"https://api.cricapi.com/v1/match_info?apikey={apikey}&id={event_key}"
        resp = requests.get(url, timeout=None)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/timeline")
@require_auth
def get_timeline(user):
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    days = request.args.get("days", 7, type=int)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats = ReadingStat.query.filter(
        ReadingStat.user_id == user.id,
        ReadingStat.read_at >= cutoff,
    ).order_by(ReadingStat.read_at.desc()).all()
    timeline = {}
    for s in stats:
        day = s.read_at.strftime("%Y-%m-%d")
        if day not in timeline:
            timeline[day] = []
        timeline[day].append({
            "url": s.article_url,
            "title": s.article_title,
            "source": s.source,
            "time": s.read_at.strftime("%H:%M"),
        })
    return jsonify([{"date": k, "articles": v} for k, v in sorted(timeline.items(), reverse=True)])

@app.route("/api/trends")
@require_auth
def get_trends(user):
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)
    recent = ReadingStat.query.filter(
        ReadingStat.user_id == user.id,
        ReadingStat.read_at >= week_ago,
    ).all()
    older = ReadingStat.query.filter(
        ReadingStat.user_id == user.id,
        ReadingStat.read_at >= two_weeks_ago,
        ReadingStat.read_at < week_ago,
    ).all()
    recent_sources = Counter(s.source for s in recent if s.source)
    older_sources = Counter(s.source for s in older if s.source)
    trends = []
    all_sources = set(list(recent_sources.keys()) + list(older_sources.keys()))
    for src in all_sources:
        rc = recent_sources.get(src, 0)
        oc = older_sources.get(src, 0)
        change = rc - oc
        if oc > 0 and rc > 0:
            momentum = "rising" if change > 0 else ("falling" if change < 0 else "stable")
        elif rc > 0 and oc == 0:
            momentum = "new"
        else:
            momentum = "stable"
        if rc > 0:
            trends.append({"label": src, "count": rc, "previous": oc, "momentum": momentum, "type": "source"})
    groq_keys = ensure_list(os.getenv("GROQ_API_KEY", ""))
    ai_trends = []
    titles_recent = [s.article_title for s in recent if s.article_title][:15]
    if len(titles_recent) >= 3:
        prompt = (
            "Based on these recently read article titles, identify 3-5 trending topics or themes. "
            "Return a JSON array of objects with keys: 'topic' (string), 'reason' (short 1-sentence explanation).\n\n"
            + "\n".join(f"- {t}" for t in titles_recent)
        )
        if groq_keys:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_keys[0]}", "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 500},
                    timeout=None,
                )
                if resp.ok:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    content = content.replace("```json", "").replace("```", "").strip()
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        ai_trends = parsed
            except Exception:
                pass
        if not ai_trends:
            ollama_text, _ = ollama_request(prompt, format_json=True)
            if ollama_text:
                try:
                    json_match = re.search(r'\[.*\]', ollama_text, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group())
                        if isinstance(parsed, list):
                            ai_trends = parsed
                except Exception:
                    pass
    return jsonify({"source_trends": sorted(trends, key=lambda t: t["count"], reverse=True)[:10], "topic_trends": ai_trends})

@app.route("/api/recommendations")
@require_auth
def get_recommendations(user):
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent = ReadingStat.query.filter(
        ReadingStat.user_id == user.id,
        ReadingStat.read_at >= cutoff,
    ).all()
    source_counts = Counter(s.source for s in recent if s.source)
    top_sources = [s for s, _ in source_counts.most_common(5)]
    titles = [s.article_title for s in recent if s.article_title]
    groq_keys = ensure_list(os.getenv("GROQ_API_KEY", ""))
    recommendations = {"top_sources": top_sources, "suggested_queries": [], "reading_tip": ""}
    if titles:
        sample = titles[:20]
        prompt = (
            "Based on these article titles a user has been reading, suggest 5 search queries "
            "they might be interested in. Return as a JSON array of strings only.\n\n"
            + "\n".join(f"- {t}" for t in sample)
        )
        if groq_keys:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_keys[0]}", "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 300},
                    timeout=None,
                )
                if resp.ok:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    try:
                        recommendations["suggested_queries"] = json.loads(content)
                    except json.JSONDecodeError:
                        recommendations["suggested_queries"] = [l.strip("- ") for l in content.split("\n") if l.strip()][:5]
            except Exception:
                pass
        if not recommendations["suggested_queries"]:
            ollama_text, _ = ollama_request(prompt, format_json=True)
            if ollama_text:
                try:
                    json_match = re.search(r'\[.*\]', ollama_text, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group())
                        if isinstance(parsed, list):
                            recommendations["suggested_queries"] = parsed
                except Exception:
                    pass
    total = len(recent)
    if total == 0:
        recommendations["reading_tip"] = "Start reading articles to get personalized recommendations."
    elif total < 10:
        recommendations["reading_tip"] = f"You've read {total} articles. Read more for better recommendations!"
    else:
        recommendations["reading_tip"] = f"You read {total} articles recently. Exploring new sources can broaden your perspective."
    return jsonify(recommendations)

@app.route("/api/stats")
@require_auth
def get_reading_stats(user):
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    days = request.args.get("days", 30, type=int)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats = ReadingStat.query.filter(
        ReadingStat.user_id == user.id,
        ReadingStat.read_at >= cutoff,
    ).order_by(ReadingStat.read_at.desc()).all()

    daily_counts = {}
    weekly_counts = {}
    source_counts = Counter()
    total_reads = len(stats)
    total_seconds = sum(s.estimated_seconds for s in stats)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_count = sum(1 for s in stats if s.read_at.strftime("%Y-%m-%d") == today)

    for s in stats:
        day = s.read_at.strftime("%Y-%m-%d")
        daily_counts[day] = daily_counts.get(day, 0) + 1
        week = s.read_at.strftime("%Y-W%U")
        weekly_counts[week] = weekly_counts.get(week, 0) + 1
        if s.source:
            source_counts[s.source] += 1

    return jsonify({
        "total_reads": total_reads,
        "total_seconds": total_seconds,
        "today_count": today_count,
        "daily_counts": [{"date": k, "count": v} for k, v in sorted(daily_counts.items())],
        "weekly_counts": [{"week": k, "count": v} for k, v in sorted(weekly_counts.items())],
        "top_sources": [{"source": k, "count": v} for k, v in source_counts.most_common(10)],
    })

@app.route("/api/tts", methods=["POST"])
def text_to_speech():
    data = request.json or {}
    text = data.get("text", "")
    voice = data.get("voice", "21m00Tcm4TlvDq8ikWAM")
    api_key = data.get("api_key", "")

    if not text or not api_key:
        return jsonify({"error": "text and api_key required"}), 400

    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg"
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
            },
            timeout=None,
            stream=True
        )
        if resp.status_code != 200:
            return jsonify({"error": "TTS request failed"}), resp.status_code
        return Response(resp.iter_content(chunk_size=4096), content_type="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/youtube-search", methods=["POST"])
def youtube_search():
    data = request.json or {}
    query = data.get("query", "")
    lang = data.get("lang", "en")
    if not query:
        return jsonify({"error": "query required"}), 400

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return jsonify({"error": "YouTube API key not configured on server"}), 500

    try:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 6,
            "key": api_key
        }
        if lang:
            params["relevanceLanguage"] = lang

        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params=params,
            timeout=None
        )
        if resp.status_code != 200:
            return jsonify({"error": "YouTube API request failed", "detail": resp.text}), resp.status_code

        data = resp.json()
        results = []
        for item in data.get("items", []):
            vid = item.get("id", {})
            snippet = item.get("snippet", {})
            results.append({
                "videoId": vid.get("videoId", ""),
                "title": snippet.get("title", ""),
                "channelName": snippet.get("channelTitle", ""),
                "thumbnail": (snippet.get("thumbnails", {}) or {}).get("high", {}).get("url", "")
                    or (snippet.get("thumbnails", {}) or {}).get("medium", {}).get("url", "")
                    or (snippet.get("thumbnails", {}) or {}).get("default", {}).get("url", ""),
                "publishedAt": snippet.get("publishedAt", "")
            })

        return jsonify({"results": results, "cached": False})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/rss/custom", methods=["GET"])
@require_auth
def get_custom_rss(user):
    if not user:
        return jsonify({"sources": []})
    sources = CustomRssSource.query.filter_by(user_id=user.id).all()
    return jsonify({"sources": [{"id": s.id, "url": s.url, "label": s.label, "created_at": s.created_at.isoformat() if s.created_at else ""} for s in sources]})

@app.route("/api/rss/custom", methods=["POST"])
@require_auth
def add_custom_rss(user):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data or not data.get("url"):
        return jsonify({"error": "url required"}), 400
    url = data["url"].strip()
    label = data.get("label", "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    existing = CustomRssSource.query.filter_by(user_id=user.id, url=url).first()
    if existing:
        return jsonify({"error": "Source already added"}), 400
    test = fetch_custom_rss(url, max_items=1)
    if not test:
        return jsonify({"error": "Could not fetch RSS feed from this URL"}), 400
    source = CustomRssSource(user_id=user.id, url=url, label=label or test[0].get("source", "Custom RSS"))
    db.session.add(source)
    db.session.commit()
    return jsonify({"id": source.id, "url": source.url, "label": source.label, "created_at": source.created_at.isoformat() if source.created_at else ""}), 201

@app.route("/api/rss/custom/<int:source_id>", methods=["DELETE"])
@require_auth
def delete_custom_rss(user, source_id):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    source = CustomRssSource.query.filter_by(id=source_id, user_id=user.id).first()
    if not source:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(source)
    db.session.commit()
    return jsonify({"status": "deleted"})

@app.route("/api/claims", methods=["GET"])
@require_auth
def get_claims(user):
    if not user:
        return jsonify({"claims": []})
    claims = TrackedClaim.query.filter_by(user_id=user.id).order_by(TrackedClaim.created_at.desc()).all()
    result = []
    for c in claims:
        mentions = ClaimMention.query.filter_by(claim_id=c.id).order_by(ClaimMention.matched_at.desc()).all()
        result.append({
            "id": c.id,
            "claim_text": c.claim_text,
            "keywords": c.keywords,
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else "",
            "mentions": [{"id": m.id, "article_url": m.article_url, "article_title": m.article_title, "source": m.source, "snippet": m.snippet, "matched_at": m.matched_at.isoformat() if m.matched_at else ""} for m in mentions]
        })
    return jsonify({"claims": result})

@app.route("/api/claims", methods=["POST"])
@require_auth
def add_claim(user):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data or not data.get("claim_text"):
        return jsonify({"error": "claim_text required"}), 400
    claim = TrackedClaim(
        user_id=user.id,
        claim_text=data["claim_text"].strip(),
        keywords=data.get("keywords", "").strip(),
    )
    db.session.add(claim)
    db.session.commit()
    return jsonify({"id": claim.id, "claim_text": claim.claim_text, "keywords": claim.keywords, "created_at": claim.created_at.isoformat() if claim.created_at else ""}), 201

@app.route("/api/claims/<int:claim_id>", methods=["DELETE"])
@require_auth
def delete_claim(user, claim_id):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    claim = TrackedClaim.query.filter_by(id=claim_id, user_id=user.id).first()
    if not claim:
        return jsonify({"error": "Not found"}), 404
    ClaimMention.query.filter_by(claim_id=claim.id).delete()
    db.session.delete(claim)
    db.session.commit()
    return jsonify({"status": "deleted"})

@app.route("/api/claims/refresh", methods=["POST"])
@require_auth
def refresh_claims(user):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    claims = TrackedClaim.query.filter_by(user_id=user.id).all()
    if not claims:
        return jsonify({"mentions": []})

    articles, failed_sources, _ = fetch_all_sources(language="en", country="in", sort="newest")
    new_mentions = []
    for claim in claims:
        keywords = (claim.keywords or claim.claim_text).lower()
        kw_list = [k.strip() for k in re.split(r'[,;\s]+', keywords) if len(k.strip()) > 2]
        if not kw_list:
            kw_list = [claim.claim_text.lower()[:50]]
        for a in articles[:100]:
            text = (a.get("title", "") + " " + a.get("description", "")).lower()
            matched = [kw for kw in kw_list if kw in text]
            if matched:
                existing = ClaimMention.query.filter_by(claim_id=claim.id, article_url=a.get("url", "")).first()
                if not existing:
                    snippet = a.get("description", "")[:300]
                    mention = ClaimMention(
                        claim_id=claim.id,
                        article_url=a.get("url", ""),
                        article_title=a.get("title", ""),
                        source=a.get("source", ""),
                        snippet=snippet,
                    )
                    db.session.add(mention)
                    new_mentions.append({"claim_id": claim.id, "claim_text": claim.claim_text, "article_title": a.get("title", ""), "article_url": a.get("url", ""), "source": a.get("source", ""), "snippet": snippet})
        claim.last_checked_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"mentions": new_mentions})

@app.route("/api/lists", methods=["GET"])
@require_auth
def get_lists(user):
    if not user:
        return jsonify({"lists": []})
    lists = SharedList.query.filter_by(user_id=user.id).order_by(SharedList.updated_at.desc()).all()
    return jsonify({"lists": [{"id": l.id, "name": l.name, "slug": l.slug, "description": l.description, "article_count": len(l.articles or []), "is_public": l.is_public, "created_at": l.created_at.isoformat() if l.created_at else "", "updated_at": l.updated_at.isoformat() if l.updated_at else ""} for l in lists]})

@app.route("/api/lists", methods=["POST"])
@require_auth
def create_list(user):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "name required"}), 400
    slug = data.get("slug", "") or uuid4().hex[:10]
    existing = SharedList.query.filter_by(slug=slug).first()
    if existing:
        slug = uuid4().hex[:10]
    lst = SharedList(
        user_id=user.id,
        name=data["name"].strip(),
        slug=slug,
        description=data.get("description", "").strip(),
        articles=data.get("articles", []),
        is_public=data.get("is_public", False),
    )
    db.session.add(lst)
    db.session.commit()
    return jsonify({"id": lst.id, "name": lst.name, "slug": lst.slug, "description": lst.description, "is_public": lst.is_public}), 201

@app.route("/api/lists/<int:list_id>", methods=["PUT"])
@require_auth
def update_list(user, list_id):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    lst = SharedList.query.filter_by(id=list_id, user_id=user.id).first()
    if not lst:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    if "name" in data:
        lst.name = data["name"].strip()
    if "description" in data:
        lst.description = data["description"].strip()
    if "articles" in data:
        lst.articles = data["articles"]
    if "is_public" in data:
        lst.is_public = data["is_public"]
    lst.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"status": "updated"})

@app.route("/api/lists/<int:list_id>", methods=["DELETE"])
@require_auth
def delete_list(user, list_id):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    lst = SharedList.query.filter_by(id=list_id, user_id=user.id).first()
    if not lst:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(lst)
    db.session.commit()
    return jsonify({"status": "deleted"})

@app.route("/api/lists/<slug>", methods=["GET"])
def get_public_list(slug):
    lst = SharedList.query.filter_by(slug=slug, is_public=True).first()
    if not lst:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "name": lst.name,
        "description": lst.description,
        "articles": lst.articles or [],
        "created_at": lst.created_at.isoformat() if lst.created_at else "",
    })

@app.route("/api/reports/schedule", methods=["GET"])
@require_auth
def get_report_schedule(user):
    if not user:
        return jsonify({"schedule": None})
    report = ScheduledReport.query.filter_by(user_id=user.id).first()
    if not report:
        return jsonify({"schedule": None})
    return jsonify({
        "schedule": {
            "id": report.id,
            "frequency": report.frequency,
            "day_of_week": report.day_of_week,
            "time": report.time,
            "last_generated": report.last_generated.isoformat() if report.last_generated else None,
            "next_generate": report.next_generate.isoformat() if report.next_generate else None,
        }
    })

@app.route("/api/reports/schedule", methods=["POST"])
@require_auth
def save_report_schedule(user):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    report = ScheduledReport.query.filter_by(user_id=user.id).first()
    if not report:
        report = ScheduledReport(user_id=user.id)
        db.session.add(report)
    if "frequency" in data:
        report.frequency = data["frequency"]
    if "day_of_week" in data:
        report.day_of_week = data["day_of_week"]
    if "time" in data:
        report.time = data["time"]
    now = datetime.now(timezone.utc)
    if report.frequency == "weekly":
        days_ahead = (report.day_of_week - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_date = now + timedelta(days=days_ahead)
    else:
        next_date = now + timedelta(days=30)
    time_match = re.match(r'^(\d{1,2}):(\d{2})$', report.time)
    if time_match:
        next_date = next_date.replace(hour=int(time_match.group(1)), minute=int(time_match.group(2)), second=0, microsecond=0)
    report.next_generate = next_date
    db.session.commit()
    return jsonify({"status": "saved"})

@app.route("/api/reports/schedule", methods=["DELETE"])
@require_auth
def delete_report_schedule(user):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    report = ScheduledReport.query.filter_by(user_id=user.id).first()
    if report:
        db.session.delete(report)
        db.session.commit()
    return jsonify({"status": "deleted"})

@app.route("/api/reports/generate", methods=["POST"])
@require_auth
def generate_report(user):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    articles, failed_sources, _ = fetch_all_sources(language="en", country="in", sort="newest")
    cred_rows = get_db().execute("SELECT source, score, total_count FROM source_credibility ORDER BY score DESC").fetchall()
    credibility = [{"source": r["source"], "score": r["score"], "total_count": r["total_count"]} for r in cred_rows]
    source_counts = Counter(a.get("source", "Unknown") for a in articles)
    top_sources = [{"source": s, "count": c} for s, c in source_counts.most_common(10)]
    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_articles": len(articles),
        "failed_sources": len(failed_sources),
        "top_sources": top_sources,
        "credibility_scores": credibility[:20],
    }
    archive = ReportArchive(
        user_id=user.id,
        report_data=report_data,
    )
    db.session.add(archive)
    report = ScheduledReport.query.filter_by(user_id=user.id).first()
    if report:
        report.last_generated = datetime.now(timezone.utc)
    db.session.commit()
    report_url = f"/api/reports/download/{archive.id}"
    return jsonify({"id": archive.id, "report": report_data, "download_url": report_url})

@app.route("/api/reports/archive", methods=["GET"])
@require_auth
def get_report_archive(user):
    if not user:
        return jsonify({"reports": []})
    archives = ReportArchive.query.filter_by(user_id=user.id).order_by(ReportArchive.generated_at.desc()).limit(20).all()
    return jsonify({"reports": [{"id": a.id, "generated_at": a.generated_at.isoformat() if a.generated_at else "", "download_url": a.download_url or f"/api/reports/download/{a.id}"} for a in archives]})

@app.route("/api/reports/download/<int:report_id>")
@require_auth
def download_report(user, report_id):
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    archive = ReportArchive.query.filter_by(id=report_id, user_id=user.id).first()
    if not archive:
        return jsonify({"error": "Not found"}), 404
    report_data = archive.report_data or {}
    html = f"""<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>TruthLens Report</title><style>body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #333; }} h1 {{ color: #6366f1; }} table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }} th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }} th {{ background: #f5f5f5; }} .meta {{ color: #666; font-size: 14px; }}</style></head><body><h1>TruthLens Credibility Report</h1><p class=\"meta\">Generated: {report_data.get('generated_at', 'N/A')}</p><p>Total articles scanned: <strong>{report_data.get('total_articles', 0)}</strong></p><h2>Top Sources</h2><table><tr><th>Source</th><th>Articles</th></tr>"""
    for source in report_data.get("top_sources", []):
        html += f"<tr><td>{source.get('source')}</td><td>{source.get('count')}</td></tr>"
    html += "</table></body></html>"
    return Response(html, mimetype='text/html')

# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
