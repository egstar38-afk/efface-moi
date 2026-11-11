"""Stockage local (SQLite) : réglages, demandes, historique. Tout reste dans le dossier data/."""
import base64
import contextlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.3.1"
FROZEN = bool(getattr(sys, "frozen", False))      # vrai dans la version .exe
ROOT = Path(__file__).resolve().parent.parent     # dossier du code (dans l'exe : dossier interne en lecture seule)


def _default_data():
    """Version .exe : dossier EffaceMoi dans LOCALAPPDATA, pour que déplacer l'exe ne perde rien.
    Sinon : dossier data/ à côté du code."""
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EffaceMoi"
    return ROOT / "data"


DATA_DIR = Path(os.environ.get("EFFACE_DATA") or _default_data())
DB_FILE = DATA_DIR / "efface.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL, name TEXT NOT NULL, email TEXT, webform TEXT, lang TEXT,
    status TEXT NOT NULL, recurring INTEGER NOT NULL DEFAULT 0,
    subject TEXT, body TEXT, message_id TEXT, reminder_message_id TEXT, complaint TEXT,
    created_at TEXT, sent_at TEXT, due_at TEXT, reminded_at TEXT, due2_at TEXT, closed_at TEXT,
    reply_at TEXT, reply_from TEXT, reply_subject TEXT, ack_at TEXT, error TEXT, processing_at TEXT,
    urls TEXT, suggestion TEXT, reply_excerpt TEXT, reply_how TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE, domain TEXT, title TEXT, snippet TEXT,
    source TEXT, query TEXT, evidence TEXT, status TEXT NOT NULL, request_id INTEGER, note TEXT,
    found_at TEXT, status_at TEXT, due_at TEXT, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER, at TEXT NOT NULL, type TEXT NOT NULL, detail TEXT
);
"""


def now():
    return datetime.now().replace(microsecond=0)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


_migrated = False


def _migrate(con):
    """Ajoute aux bases créées par une version plus ancienne les colonnes apparues depuis."""
    global _migrated
    if _migrated:
        return
    have = {row[1] for row in con.execute("PRAGMA table_info(requests)")}
    for column in ("processing_at", "urls", "suggestion", "reply_excerpt", "reply_how"):
        if column not in have:
            con.execute("ALTER TABLE requests ADD COLUMN %s TEXT" % column)
    _migrated = True


@contextlib.contextmanager
def db():
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":      # serveur Linux : dossier lisible par ce seul utilisateur
            os.chmod(DATA_DIR, 0o700)
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        _migrate(con)
        yield con
        con.commit()
    finally:
        con.close()


def get(key, default=None):
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set(key, value):
    with db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, ensure_ascii=False)))


def rows(sql, args=()):
    with db() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def execute(sql, args=()):
    with db() as con:
        return con.execute(sql, args).lastrowid


def update_request(rid, **fields):
    cols = ", ".join("%s=?" % k for k in fields)
    execute("UPDATE requests SET %s WHERE id=?" % cols, (*fields.values(), rid))


def event(rid, type_, detail=""):
    execute("INSERT INTO events(request_id, at, type, detail) VALUES(?,?,?,?)", (rid, iso(), type_, detail))


# --- Secrets (mot de passe de messagerie, clé de moteur de recherche) : chiffrés par Windows (DPAPI),
# lisibles uniquement par ce compte Windows. Sur un autre système : simple encodage, dossier réservé au compte. ---

def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("Chiffrement Windows (DPAPI) impossible.")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(dst.pbData, ctypes.c_void_p))


def set_secret(key, value):
    if not value:
        set(key, "")
    elif sys.platform == "win32":
        set(key, "dpapi:" + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode())
    else:
        set(key, "plain:" + base64.b64encode(value.encode("utf-8")).decode())


def get_secret(key):
    v = get(key, "")
    if not v:
        return ""
    kind, _, payload = v.partition(":")
    if kind == "dpapi" and sys.platform != "win32":
        return ""       # base copiée depuis un PC Windows : le secret est à ressaisir ici
    raw = base64.b64decode(payload)
    return (_dpapi(raw, False) if kind == "dpapi" else raw).decode("utf-8")


def set_password(pw):
    set_secret("mail_password", pw)


def get_password():
    return get_secret("mail_password")
