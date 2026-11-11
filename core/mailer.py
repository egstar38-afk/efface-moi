"""Envoi (SMTP) et lecture des en-têtes (IMAP) avec la boîte mail de l'utilisateur."""
import email
import email.utils
import html as html_lib
import imaplib
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage

from . import store

PROVIDERS = {
    "gmail.com": ("smtp.gmail.com", 465, "ssl", "imap.gmail.com"),
    "googlemail.com": ("smtp.gmail.com", 465, "ssl", "imap.gmail.com"),
    "yahoo.fr": ("smtp.mail.yahoo.com", 465, "ssl", "imap.mail.yahoo.com"),
    "yahoo.com": ("smtp.mail.yahoo.com", 465, "ssl", "imap.mail.yahoo.com"),
    "orange.fr": ("smtp.orange.fr", 465, "ssl", "imap.orange.fr"),
    "wanadoo.fr": ("smtp.orange.fr", 465, "ssl", "imap.orange.fr"),
    "free.fr": ("smtp.free.fr", 465, "ssl", "imap.free.fr"),
    "laposte.net": ("smtp.laposte.net", 465, "ssl", "imap.laposte.net"),
    "sfr.fr": ("smtp.sfr.fr", 465, "ssl", "imap.sfr.fr"),
    "icloud.com": ("smtp.mail.me.com", 587, "starttls", "imap.mail.me.com"),
    "me.com": ("smtp.mail.me.com", 587, "starttls", "imap.mail.me.com"),
    "gmx.fr": ("mail.gmx.com", 465, "ssl", "imap.gmx.com"),
    "gmx.com": ("mail.gmx.com", 465, "ssl", "imap.gmx.com"),
}
DEFAULTS = {"email": "", "username": "", "smtp_host": "", "smtp_port": 465, "smtp_security": "ssl",
            "imap_host": "", "imap_port": 993, "test_mode": True, "daily_cap": 40, "delay_seconds": 8}
OUTBOX = store.DATA_DIR / "outbox"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HEADER_FIELDS = ("FROM SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES AUTO-SUBMITTED X-AUTOREPLY "
                 "X-AUTORESPOND PRECEDENCE LIST-UNSUBSCRIBE")
AUTO_SUBJECT = re.compile(r"^\s*(automatic reply|auto(matic)?[- ]?(reply|response)|réponse automatique|"
                          r"accusé de réception|abwesenheit|out of office|absence)", re.I)


def settings():
    s = dict(DEFAULTS)
    s.update(store.get("mail", {}))
    return s


def save_settings(new):
    s = settings()
    for k in DEFAULTS:
        if k in new:
            s[k] = new[k]
    s["email"] = str(s["email"]).strip()
    domain = s["email"].rsplit("@", 1)[-1].lower()
    if domain in PROVIDERS and not s["smtp_host"]:
        s["smtp_host"], s["smtp_port"], s["smtp_security"], s["imap_host"] = PROVIDERS[domain]
    for k in ("smtp_port", "imap_port", "daily_cap", "delay_seconds"):
        s[k] = int(s[k] or DEFAULTS[k])
    s["daily_cap"] = max(1, min(s["daily_cap"], 200))
    s["delay_seconds"] = max(0, min(s["delay_seconds"], 600))
    s["test_mode"] = bool(s["test_mode"])
    store.set("mail", s)
    return s


def ready(for_imap=False):
    s = settings()
    if for_imap:
        return bool(s["email"] and s["imap_host"] and store.get_password())
    return bool(s["email"] and (s["test_mode"] or (s["smtp_host"] and store.get_password())))


def _local(host):
    return host in ("127.0.0.1", "localhost")


def _smtp(s):
    ctx = ssl.create_default_context()
    if s["smtp_security"] not in ("ssl", "starttls") and not _local(s["smtp_host"]):
        raise RuntimeError("Connexion non chiffrée refusée : choisissez SSL ou STARTTLS.")
    if s["smtp_security"] == "ssl":
        srv = smtplib.SMTP_SSL(s["smtp_host"], s["smtp_port"], timeout=30, context=ctx)
    else:
        srv = smtplib.SMTP(s["smtp_host"], s["smtp_port"], timeout=30)
        if s["smtp_security"] == "starttls":
            srv.starttls(context=ctx)
    if not (s["smtp_security"] == "none" and _local(s["smtp_host"])):
        srv.login(s["username"] or s["email"], store.get_password())
    return srv


def build(to, subject, body, in_reply_to=None):
    s = settings()
    profile = store.get("profile", {})
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((profile.get("name", ""), s["email"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.localtime()
    msg["Message-ID"] = email.utils.make_msgid(domain=s["email"].rsplit("@", 1)[-1] or "localhost")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


def send(to, subject, body, in_reply_to=None):
    """Renvoie (message_id, envoyé_pour_de_vrai). En mode test, écrit un fichier .eml au lieu d'envoyer."""
    s = settings()
    msg = build(to, subject, body, in_reply_to)
    if s["test_mode"]:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9.@_-]+", "_", "%s_%s" % (store.iso().replace(":", ""), to))[:120]
        (OUTBOX / (name + ".eml")).write_bytes(bytes(msg))
        return msg["Message-ID"], False
    srv = _smtp(s)
    try:
        srv.send_message(msg)
    finally:
        try:
            srv.quit()
        except smtplib.SMTPException:
            pass
    return msg["Message-ID"], True


def _imap(s):
    if not _local(s["imap_host"]):
        m = imaplib.IMAP4_SSL(s["imap_host"], s["imap_port"], ssl_context=ssl.create_default_context(), timeout=60)
    else:
        m = imaplib.IMAP4(s["imap_host"], s["imap_port"], timeout=60)
    m.login(s["username"] or s["email"], store.get_password())
    return m


def test_connection():
    """Vérifie les identifiants sans rien envoyer."""
    s = settings()
    out = {}
    for label, fn in (("smtp", lambda: _smtp(s).quit()), ("imap", lambda: _imap(s).logout())):
        try:
            fn()
            out[label] = "ok"
        except Exception as e:  # noqa: BLE001 - on montre l'erreur telle quelle à l'utilisateur
            out[label] = "%s: %s" % (type(e).__name__, _clean(e))
    return out


def _clean(e):
    msg = e.args[-1] if getattr(e, "args", None) else e
    if isinstance(msg, bytes):
        msg = msg.decode("utf-8", "replace")
    return str(msg)[:300]


def _decode(v):
    if not v:
        return ""
    try:
        return str(make_header(decode_header(str(v)))).strip()
    except Exception:  # noqa: BLE001 - en-tête mal formé : on garde le texte brut
        return str(v).strip()


def parse_headers(raw):
    m = email.message_from_bytes(raw)
    name, addr = email.utils.parseaddr(_decode(m.get("From")))
    subject = _decode(m.get("Subject"))
    try:
        date = email.utils.parsedate_to_datetime(m.get("Date")).astimezone().replace(tzinfo=None)
        date = date.isoformat(timespec="seconds")
    except (TypeError, ValueError):
        date = ""
    auto = (m.get("Auto-Submitted", "no").strip().lower() != "no" or m.get("X-Autoreply") is not None
            or m.get("X-Autorespond") is not None
            or "auto" in (m.get("Precedence") or "").lower() or bool(AUTO_SUBJECT.match(subject)))
    refs = " ".join([m.get("In-Reply-To") or "", m.get("References") or ""])
    return {"from": addr.lower(), "from_name": name, "subject": subject, "date": date,
            "message_id": (m.get("Message-ID") or "").strip(), "refs": re.findall(r"<[^<>\s]+>", refs),
            "auto": auto, "unsubscribe": bool(m.get("List-Unsubscribe"))}


def _special_folder(m, flag):
    """Nom du dossier qui joue ce rôle (\\Junk…), quelle que soit la langue de la boîte."""
    try:
        typ, data = m.list()
    except Exception:  # noqa: BLE001
        return None
    for line in data or []:
        s = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
        if flag.lower() in s.lower():
            found = re.search(r'"((?:[^"\\]|\\.)*)"\s*$', s) or re.search(r"(\S+)\s*$", s)
            if found:
                return found.group(1)
    return None


def _select(m, folder):
    typ, _ = m.select('"%s"' % folder.replace('"', '\\"'), readonly=True)
    return typ == "OK"


def fetch_headers(since=None, limit=None, progress=lambda *a: None, junk=False):
    """En-têtes des messages reçus, en lecture seule. junk=True regarde aussi le dossier des indésirables,
    où atterrissent certaines réponses d'expéditeurs inconnus. Chaque en-tête porte son dossier et son numéro."""
    s = settings()
    m = _imap(s)
    try:
        folders = ["INBOX"]
        if junk:
            spam = _special_folder(m, "\\Junk")
            if spam:
                folders.append(spam)
        crit = ["SINCE", "%d-%s-%d" % (since.day, MONTHS[since.month - 1], since.year)] if since else ["ALL"]
        out = []
        for folder in folders:
            try:
                if not _select(m, folder):
                    continue
                typ, data = m.uid("search", None, *crit)
            except imaplib.IMAP4.error:
                continue
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            if limit:
                uids = uids[-limit:]
            for i in range(0, len(uids), 200):
                progress(i, len(uids), "Lecture des en-têtes…")
                typ, parts = m.uid("fetch", b",".join(uids[i:i + 200]),
                                   "(BODY.PEEK[HEADER.FIELDS (%s)])" % HEADER_FIELDS)
                for p in parts or []:
                    if isinstance(p, tuple) and len(p) > 1 and p[1]:
                        uid = re.search(rb"UID (\d+)", p[0] or b"")
                        h = parse_headers(p[1])
                        h.update(folder=folder, uid=uid.group(1).decode() if uid else "")
                        out.append(h)
        return out
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


def message_text(raw):
    """Texte lisible d'un message (éventuellement tronqué) : la partie texte, sinon le HTML débarrassé de ses balises."""
    try:
        msg = email.message_from_bytes(raw)
    except Exception:  # noqa: BLE001
        return ""
    plain, rich = "", ""
    for part in msg.walk():
        if part.is_multipart() or "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html") or (plain if kind == "text/plain" else rich):
            continue
        try:
            data = part.get_payload(decode=True) or b""
            try:
                text = data.decode(part.get_content_charset() or "utf-8", "replace")
            except LookupError:
                text = data.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - partie coupée au milieu ou mal encodée
            continue
        if kind == "text/plain":
            plain = text
        else:
            text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", text)
            text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h[1-6]>", "\n", text)
            rich = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    return (plain if plain.strip() else rich)[:15000]


def fetch_texts(items, progress=lambda *a: None, limit_bytes=80000):
    """Texte de messages précis, désignés par (dossier, numéro). Lecture seule : ils ne sont pas marqués comme lus.
    Sert uniquement pour les réponses à vos demandes."""
    out = {}
    if not items:
        return out
    m = _imap(settings())
    try:
        current = None
        for n, (folder, uid) in enumerate(items):
            progress(n, len(items), "Lecture des réponses…")
            try:
                if folder != current:
                    if not _select(m, folder):
                        continue
                    current = folder
                typ, parts = m.uid("fetch", str(uid), "(BODY.PEEK[]<0.%d>)" % limit_bytes)
            except imaplib.IMAP4.error:
                continue
            for p in parts or []:
                if isinstance(p, tuple) and len(p) > 1 and p[1]:
                    out[(folder, str(uid))] = message_text(p[1])
        return out
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass
