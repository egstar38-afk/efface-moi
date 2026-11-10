"""Base des entreprises : téléchargée depuis le projet open source datarequests.org
(dépôt GitHub datenanfragen/data, licence CC0), plus les entreprises ajoutées à la main."""
import io
import json
import re
import unicodedata
import urllib.request
import zipfile
from datetime import datetime

from . import store

DATA_ZIP = "https://github.com/datenanfragen/data/archive/refs/heads/master.zip"
COMPANIES_FILE = store.DATA_DIR / "companies.json"
KEEP = ("slug", "name", "email", "web", "webform", "address", "categories", "relevant-countries",
        "runs", "required-elements", "suggested-transport-medium", "quality", "needs-id-document")

# Catégories de la base qui correspondent au travail d'Incogni.
BROKER_CATEGORIES = {"addresses", "ads", "credit agency", "collection agency"}

PRESETS = {
    "brokers_fr": "Courtiers en données, publicité et fichiers de solvabilité — France",
    "brokers_all": "Courtiers en données, publicité et fichiers de solvabilité — toute l'Europe",
    "fr": "Toutes les entreprises référencées pour la France",
}

_cache = {"mtime": None, "data": None}


def update(progress=lambda *a: None):
    """Télécharge la base et la range dans data/companies.json. Rien n'est extrait sur le disque."""
    progress(0, 0, "Téléchargement de la base datarequests.org…")
    req = urllib.request.Request(DATA_ZIP, headers={"User-Agent": "efface-moi"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read(80 * 1024 * 1024)
    companies = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [i for i in z.infolist() if re.fullmatch(r"[^/]+/companies/[^/]+\.json", i.filename)]
        for n, info in enumerate(names):
            if info.file_size > 1024 * 1024:
                continue
            try:
                rec = json.loads(z.read(info).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(rec, dict) and rec.get("slug") and rec.get("name"):
                companies.append({k: rec[k] for k in KEEP if k in rec})
            if n % 500 == 0:
                progress(n, len(names), "Lecture des fiches…")
    if len(companies) < 100:
        raise RuntimeError("Base téléchargée incomplète (%d fiches), ancienne base conservée." % len(companies))
    companies.sort(key=lambda c: c["name"].lower())
    store.DATA_DIR.mkdir(exist_ok=True)
    payload = {"updated": datetime.now().isoformat(timespec="seconds"), "companies": companies}
    COMPANIES_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _cache["mtime"] = None
    return len(companies)


def _load():
    if not COMPANIES_FILE.exists():
        return {"updated": None, "companies": []}
    mtime = COMPANIES_FILE.stat().st_mtime
    if _cache["mtime"] != mtime:
        _cache["data"] = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
        _cache["mtime"] = mtime
    return _cache["data"]


def info():
    d = _load()
    return {"updated": d["updated"], "count": len(d["companies"])}


def all_companies():
    """Entreprises ajoutées à la main d'abord : elles priment sur la base en cas de doublon."""
    custom = store.get("custom_companies", [])
    seen = {c["slug"] for c in custom}
    return custom + [c for c in _load()["companies"] if c["slug"] not in seen]


def get(slug):
    for c in all_companies():
        if c["slug"] == slug:
            return c
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def is_broker(c):
    return bool(BROKER_CATEGORIES & set(c.get("categories") or []))


def in_country(c, country):
    rc = c.get("relevant-countries") or ["all"]
    return country in rc or "all" in rc


def summary(c):
    return {
        "slug": c["slug"], "name": c["name"], "email": c.get("email") or "",
        "webform": c.get("webform") or "", "web": c.get("web") or "",
        "categories": c.get("categories") or [], "countries": c.get("relevant-countries") or ["all"],
        "custom": bool(c.get("custom")), "needs_id": bool(c.get("needs-id-document")),
    }


def search(q="", preset="", limit=300):
    q = norm(q)
    out = []
    for c in all_companies():
        if preset == "brokers_fr" and not (is_broker(c) and in_country(c, "fr")):
            continue
        if preset == "brokers_all" and not is_broker(c):
            continue
        if preset == "fr" and not in_country(c, "fr"):
            continue
        if preset == "custom" and not c.get("custom"):
            continue
        if q:
            hay = norm(" ".join([c["name"], c["slug"], c.get("web") or ""] + list(c.get("runs") or [])))
            if q not in hay:
                continue
        out.append(summary(c))
    total = len(out)
    return {"total": total, "companies": out[:limit]}


def add_custom(name, email, web="", webform="", address=""):
    name, email = name.strip(), email.strip()
    if not name or (not email and not webform.strip()):
        raise ValueError("Il faut un nom, et une adresse e-mail ou un lien de formulaire.")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Adresse e-mail invalide.")
    slug = "perso-" + (norm(name).replace(" ", "-") or "entreprise")
    custom = [c for c in store.get("custom_companies", []) if c["slug"] != slug]
    custom.append({"slug": slug, "name": name, "email": email, "web": web.strip(),
                   "webform": webform.strip(), "address": address.strip(),
                   "categories": [], "relevant-countries": ["fr"], "custom": True})
    store.set("custom_companies", custom)
    return slug


def remove_custom(slug):
    store.set("custom_companies", [c for c in store.get("custom_companies", []) if c["slug"] != slug])


TWO_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.jp", "com.br", "co.nz", "com.mx",
             "co.in", "com.tr", "com.cn", "co.za", "com.sg", "com.hk", "co.kr", "com.ar", "gouv.fr"}


def base_domain(host):
    """'news.exemple.co.uk' -> 'exemple.co.uk'. Approximation suffisante pour comparer des expéditeurs."""
    host = (host or "").strip().lower().rstrip(".")
    host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split("@")[-1].split(":")[0]
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_index():
    """domaine -> entreprise, d'après le site web et l'adresse e-mail de chaque fiche."""
    idx = {}
    for c in reversed(all_companies()):
        for src in (c.get("email"), c.get("web")):
            d = base_domain(src)
            if d and d not in FREEMAIL:
                idx[d] = c
    return idx


FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.fr", "live.com", "live.fr",
            "yahoo.com", "yahoo.fr", "orange.fr", "wanadoo.fr", "free.fr", "laposte.net", "sfr.fr", "icloud.com",
            "me.com", "gmx.com", "gmx.fr", "gmx.de", "web.de", "proton.me", "protonmail.com", "aol.com",
            "bbox.fr", "neuf.fr", "msn.com", "t-online.de", "posteo.de", "mailbox.org"}
