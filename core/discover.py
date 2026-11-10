"""Détection : quelles entreprises ont vos données (d'après votre boîte mail), où votre pseudo apparaît."""
import collections
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import companies, mailer, store

SITES_URL = "https://raw.githubusercontent.com/sherlock-project/sherlock/master/sherlock_project/resources/data.json"
SITES_FILE = store.DATA_DIR / "sites.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0"
BLOCKED = ("just a moment...", "attention required! | cloudflare", "cf-challenge", "captcha-delivery.com",
           "access denied", "perimeterx")


# Adresses d'envoi automatique : personne ne lit ce qu'on y répond.
NO_REPLY = re.compile(r"no[-_.]?reply|ne[-_.]?pas[-_.]?repondre|do[-_.]?not[-_.]?reply|mailer|bounce|notif|"
                      r"newsletter|news@|alert|marketing|promo|campaign|automat|robot|system", re.I)


def best_address(addresses):
    """Parmi les adresses vues pour un expéditeur, préfère une adresse lue par quelqu'un, puis la plus fréquente.
    Renvoie (adresse, est_une_adresse_automatique)."""
    if not addresses:
        return "", False
    ranked = sorted(addresses.items(), key=lambda kv: (bool(NO_REPLY.search(kv[0])), -kv[1], kv[0]))
    addr = ranked[0][0]
    return addr, bool(NO_REPLY.search(addr))


def summarize_senders(headers, own=""):
    """Fonction pure : regroupe des en-têtes par domaine d'expéditeur."""
    by_domain = {}
    for h in headers:
        dom = companies.base_domain(h["from"])
        if not dom or "." not in dom or dom == own or dom in companies.FREEMAIL:
            continue
        d = by_domain.setdefault(dom, {"domain": dom, "count": 0, "unsubscribe": 0, "last": "",
                                       "names": collections.Counter(), "addresses": collections.Counter()})
        d["count"] += 1
        d["unsubscribe"] += 1 if h["unsubscribe"] else 0
        d["last"] = max(d["last"], h["date"])
        d["addresses"][h["from"]] += 1
        if h["from_name"]:
            d["names"][h["from_name"]] += 1
    return by_domain


def scan_mailbox(limit=3000, progress=lambda *a: None):
    """Compte les expéditeurs de la boîte de réception (en-têtes seulement) et les rapproche de la base."""
    headers = mailer.fetch_headers(limit=limit, progress=progress)
    by_domain = summarize_senders(headers, companies.base_domain(mailer.settings()["email"]))
    idx = companies.domain_index()
    out = []
    for d in by_domain.values():
        c = idx.get(d["domain"])
        address, no_reply = best_address(d["addresses"])
        out.append({"domain": d["domain"], "count": d["count"], "marketing": d["unsubscribe"] > 0,
                    "last": d["last"][:10], "sender": d["names"].most_common(1)[0][0] if d["names"] else "",
                    "address": address, "no_reply": no_reply,
                    "slug": c["slug"] if c else "", "company": c["name"] if c else "",
                    "email": (c.get("email") or "") if c else ""})
    out.sort(key=lambda x: (not x["slug"], -x["count"]))
    result = {"at": store.iso(), "messages": len(headers), "senders": out[:500]}
    store.set("mail_scan", result)
    return result


def _sites():
    fresh = SITES_FILE.exists() and time.time() - SITES_FILE.stat().st_mtime < 7 * 86400
    if not fresh:
        try:
            req = urllib.request.Request(SITES_URL, headers={"User-Agent": "efface-moi"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read(5 * 1024 * 1024)
            json.loads(raw)
            store.DATA_DIR.mkdir(exist_ok=True)
            SITES_FILE.write_bytes(raw)
        except (OSError, ValueError):
            if not SITES_FILE.exists():
                raise RuntimeError("Impossible de télécharger la liste des sites (projet Sherlock).")
    data = json.loads(SITES_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("url") and not v.get("isNSFW")}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _probe(name, site, username):
    """Renvoie 'found', 'absent' ou 'unknown'. Mêmes règles de détection que Sherlock."""
    if site.get("regexCheck"):
        try:
            if not re.search(site["regexCheck"], username):
                return "absent"
        except re.error:
            pass
    url = (site.get("urlProbe") or site["url"]).replace("{}", username)
    kind = site.get("errorType")
    if isinstance(kind, list):
        kind = kind[0]
    data = None
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}
    headers.update(site.get("headers") or {})
    if site.get("request_payload") is not None:
        data = json.dumps(site["request_payload"]).replace("{}", username).encode()
        headers.setdefault("Content-Type", "application/json")
    method = site.get("request_method") or ("POST" if data else "GET")
    opener = urllib.request.build_opener(_NoRedirect) if kind == "response_url" else urllib.request.build_opener()
    try:
        with opener.open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=10) as r:
            status, text = r.status, r.read(300_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, ""
        try:
            text = e.read(100_000).decode("utf-8", "replace")
        except OSError:
            pass
    except Exception:  # noqa: BLE001 - site injoignable : on ne conclut rien
        return "unknown"
    low = text.lower()
    if status in (403, 429, 503) or any(b in low for b in BLOCKED):
        return "unknown"
    ok = 200 <= status < 300
    if kind == "message":
        msgs = site.get("errorMsg") or []
        msgs = [msgs] if isinstance(msgs, str) else msgs
        return "found" if ok and not any(m in text for m in msgs) else "absent"
    if kind == "status_code":
        codes = site.get("errorCode") or []
        codes = [codes] if isinstance(codes, int) else codes
        return "found" if ok and status not in codes else "absent"
    return "found" if ok else "absent"


def _check(name, site, username, control):
    """Un site qui « trouve » aussi un pseudo inventé n'est pas fiable : on ne conclut rien."""
    res = _probe(name, site, username)
    if res == "found" and _probe(name, site, control) == "found":
        return "unknown"
    return res


def scan_username(username, progress=lambda *a: None):
    username = username.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{2,40}", username):
        raise ValueError("Pseudo invalide : lettres, chiffres, point, tiret et tiret bas uniquement (2 à 40 caractères).")
    progress(0, 0, "Chargement de la liste des sites…")
    sites = _sites()
    found, unknown, done = [], 0, 0
    with ThreadPoolExecutor(max_workers=24) as pool:
        control = "zq" + secrets.token_hex(5)
        futures = {pool.submit(_check, n, s, username, control): n for n, s in sites.items()}
        for f in as_completed(futures):
            name = futures[f]
            done += 1
            res = f.result()
            if res == "found":
                found.append({"site": name, "url": sites[name]["url"].replace("{}", username),
                              "home": sites[name].get("urlMain", "")})
            elif res == "unknown":
                unknown += 1
            if done % 10 == 0:
                progress(done, len(sites), "%d sites vérifiés…" % done)
    found.sort(key=lambda x: x["site"].lower())
    result = {"at": store.iso(), "username": username, "checked": len(sites), "unknown": unknown, "found": found}
    store.set("username_scan", result)
    return result
