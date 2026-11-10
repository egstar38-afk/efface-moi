"""Empreinte publique : repérer les pages du web qui exposent l'utilisateur, puis les faire supprimer.

Une page arrive ici de deux façons : signalée à la main, ou trouvée par la recherche automatique (SearXNG ou
Brave Search). Chaque page se traite ensuite de l'une de ces manières : demande d'effacement au site, demande
de déréférencement aux moteurs de recherche, suppression de son propre compte, ou mise à l'écart.

La recherche automatique ne porte que sur l'identité du profil, et seulement si son e-mail principal est celui
de la messagerie connectée : l'outil sert à nettoyer ses propres traces, pas à surveiller quelqu'un d'autre.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import companies, contact, engine, letters, mailer, store
from .discover import UA

DEFAULTS = {"provider": "", "searxng_url": "", "pseudos": [], "ignore_domains": [], "weekly": True, "notify": False}
STATUS = {"new": "À examiner", "requested": "Suppression demandée au site", "delisting": "Déréférencement demandé",
          "account": "Mon compte, à supprimer", "removed": "Disparue", "ignored": "Écartée"}
EVIDENCE = {"page": "vos données figurent sur la page", "extrait": "votre nom apparaît dans l'extrait",
            "moteur": "signalée par le moteur, page illisible par un robot", "vous": "ajoutée par vous"}
KIND = {"name": "nom", "email": "e-mail", "phone": "téléphone", "pseudo": "pseudo"}
PAUSE = 2.0             # secondes entre deux recherches
MAX_QUERIES = 14
MAX_VERIFY = 40         # pages lues par passage pour vérifier que les données y figurent vraiment
DELIST_DAYS = 31
WEEK_DAYS = 7
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
JDM_URL = "https://raw.githubusercontent.com/jdm-contrib/jdm/master/_data/sites.json"
JDM_FILE = store.DATA_DIR / "jdm.json"
DIFFICULTY = {"easy": "facile", "medium": "moyenne", "hard": "difficile", "limited": "partielle",
              "impossible": "impossible en ligne"}

ENGINES = (("Google", "https://www.google.com/search?q="), ("Bing", "https://www.bing.com/search?q="),
           ("DuckDuckGo", "https://duckduckgo.com/?q="))
SITES = (("Facebook", "facebook.com"), ("Instagram", "instagram.com"), ("LinkedIn", "linkedin.com"),
         ("X (Twitter)", "x.com"), ("TikTok", "tiktok.com"), ("YouTube", "youtube.com"),
         ("Copains d'avant", "copainsdavant.linternaute.com"), ("Pappers", "pappers.fr"),
         ("Societe.com", "societe.com"), ("Pages Blanches", "pagesjaunes.fr"))

CHECKLIST = (
    {"id": "google-vous", "title": "Résultats vous concernant (Google)",
     "text": "Outil officiel de Google : il vous prévient quand votre adresse, votre téléphone ou votre e-mail "
             "apparaît dans ses résultats, et permet d'en demander le retrait en un clic. Il faut un compte Google.",
     "url": "https://myactivity.google.com/results-about-you", "link": "Ouvrir l'outil"},
    {"id": "pages-blanches", "title": "Annuaire Pages Blanches",
     "text": "Cherchez votre nom dans l'annuaire. Pour ne plus y figurer, demandez à votre opérateur téléphonique "
             "l'inscription en liste rouge : c'est gratuit.",
     "url": "https://www.pagesjaunes.fr/pagesblanches", "link": "Ouvrir l'annuaire"},
    {"id": "registres", "title": "Registres d'entreprises (Pappers, Societe.com, Infogreffe)",
     "text": "Si vous dirigez ou avez dirigé une société, votre nom figure dans des registres légaux que ces sites "
             "reprennent. Le nom ne peut en général pas être retiré, mais depuis août 2025 un dirigeant peut "
             "demander que son adresse personnelle soit masquée au registre du commerce, en ligne sur le guichet "
             "unique de l'INPI (démarche payante ; les entrepreneurs individuels ne sont pas concernés).",
     "url": "https://procedures.inpi.fr/", "link": "Guichet unique de l'INPI"},
    {"id": "google-obsolete", "title": "Page supprimée mais encore visible dans Google",
     "text": "Quand un site a retiré une page ou une information mais que Google affiche encore l'ancienne version, "
             "cet outil force la mise à jour du résultat.",
     "url": "https://search.google.com/search-console/remove-outdated-content", "link": "Ouvrir l'outil"},
    {"id": "street-view", "title": "Votre maison dans Street View",
     "text": "Dans Google Maps, affichez votre rue en Street View, cliquez sur « Signaler un problème » en bas à "
             "droite, puis demandez le floutage de votre maison, de votre visage ou de votre plaque. C'est définitif.",
     "url": "https://www.google.com/maps", "link": "Ouvrir Google Maps"},
    {"id": "bloctel", "title": "Démarchage téléphonique (Bloctel)",
     "text": "Inscription gratuite sur la liste d'opposition au démarchage téléphonique.",
     "url": "https://www.bloctel.gouv.fr/", "link": "Ouvrir Bloctel"},
    {"id": "fuites", "title": "Fuites de données",
     "text": "Vérifiez si votre e-mail figure dans une fuite connue. Si oui : changez le mot de passe du site "
             "concerné, et partout où vous utilisiez le même.",
     "url": "https://haveibeenpwned.com/", "link": "Ouvrir Have I Been Pwned"},
    {"id": "alertes", "title": "Alerte sur votre nom",
     "text": "Sans recherche automatique, Google Alertes peut vous écrire quand une nouvelle page mentionne votre "
             "nom : créez une alerte sur votre nom entre guillemets.",
     "url": "https://www.google.com/alerts", "link": "Ouvrir Google Alertes"},
)


# ---------- réglages ----------

def settings():
    s = dict(DEFAULTS)
    s.update(store.get("footprint", {}))
    return s


def _items(value):
    if isinstance(value, str):
        value = re.split(r"[,;\s]+", value)
    return [str(v).strip() for v in value or [] if str(v).strip()]


def save_settings(b):
    s = settings()
    if "provider" in b:
        if b["provider"] not in ("", "searxng", "brave"):
            raise ValueError("Moteur de recherche inconnu.")
        s["provider"] = b["provider"]
    if "searxng_url" in b:
        u = str(b["searxng_url"] or "").strip().rstrip("/")
        if u and not re.match(r"https?://[^\s/]+", u, re.I):
            raise ValueError("Adresse SearXNG invalide : elle doit commencer par http:// ou https://.")
        s["searxng_url"] = u[:300]
    if "pseudos" in b:
        s["pseudos"] = [p.lstrip("@") for p in _items(b["pseudos"])
                        if re.fullmatch(r"@?[A-Za-z0-9_.\-]{3,40}", p)][:5]
    if "ignore_domains" in b:
        s["ignore_domains"] = [d for d in dict.fromkeys(companies.base_domain(x) for x in _items(b["ignore_domains"]))
                               if d and "." in d][:30]
    for k in ("weekly", "notify"):
        if k in b:
            s[k] = bool(b[k])
    store.set("footprint", s)
    if b.get("brave_key"):
        store.set_secret("brave_key", str(b["brave_key"]).strip()[:200])
    elif b.get("forget_key"):
        store.set_secret("brave_key", "")
    return s


def provider_ready():
    s = settings()
    return bool((s["provider"] == "searxng" and s["searxng_url"]) or
                (s["provider"] == "brave" and store.get_secret("brave_key")))


def guard():
    """La recherche automatique n'est permise que sur soi-même : l'e-mail du profil doit être celui de la boîte
    connectée, ce que seul son propriétaire peut faire."""
    p = store.get("profile", {})
    if not p.get("name") or not p.get("email"):
        raise ValueError("Renseignez d'abord votre nom et votre e-mail dans Réglages.")
    if p["email"].strip().lower() != mailer.settings()["email"].strip().lower() or not store.get_password():
        raise ValueError("La recherche automatique ne porte que sur vous : l'e-mail principal de votre identité "
                         "doit être celui de la messagerie connectée dans Réglages.")


# ---------- ce que l'on cherche ----------

def phone_variants(phone):
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("33") and len(d) == 11:
        d = "0" + d[2:]
    if len(d) == 10 and d.startswith("0"):
        return [" ".join(d[i:i + 2] for i in range(0, 10, 2)), d, "+33" + d[1:]]
    return [phone.strip()] if len(d) >= 8 else []


def identity():
    p = store.get("profile", {})
    name = " ".join(str(p.get("name") or "").split())
    emails = [e for e in dict.fromkeys([p.get("email") or ""] + list(p.get("other_emails") or [])) if e]
    return {"name": name, "emails": emails, "phones": phone_variants(p.get("phone") or ""),
            "pseudos": settings()["pseudos"]}


def name_forms(name):
    parts = name.split()
    forms = [name] if len(parts) >= 2 else []
    if len(parts) >= 2:
        forms.append(" ".join(parts[1:] + parts[:1]))       # « Nom Prénom »
    return list(dict.fromkeys(forms))


def queries():
    """Liste (type, terme, requête). Chaque terme est cherché entre guillemets : expression exacte."""
    i = identity()
    out = [("name", n, '"%s"' % n) for n in name_forms(i["name"])]
    out += [("email", e, '"%s"' % e) for e in i["emails"]]
    out += [("phone", p, '"%s"' % p) for p in i["phones"]]
    out += [("pseudo", p, '"%s"' % p) for p in i["pseudos"]]
    return out[:MAX_QUERIES]


def prepared():
    """Recherches prêtes à ouvrir dans le navigateur, pour qui n'a pas de recherche automatique."""
    i = identity()

    def item(label, q, engines=ENGINES):
        return {"label": label, "links": [{"engine": n, "url": u + urllib.parse.quote_plus(q)} for n, u in engines]}

    groups = []
    if name_forms(i["name"]):
        groups.append({"label": "Votre nom", "items": [item('"%s"' % n, '"%s"' % n) for n in name_forms(i["name"])]
                       + [item("Images", '"%s"' % i["name"], (("Google Images", "https://www.google.com/search?tbm=isch&q="),
                                                               ("Bing Images", "https://www.bing.com/images/search?q=")))]})
        groups.append({"label": "Votre nom sur les réseaux et annuaires",
                       "items": [item(label, '"%s" site:%s' % (i["name"], dom), ENGINES[:2]) for label, dom in SITES]})
    if i["emails"]:
        groups.append({"label": "Vos adresses e-mail", "items": [item(e, '"%s"' % e) for e in i["emails"]]})
    if i["phones"]:
        groups.append({"label": "Votre téléphone", "items": [item(p, '"%s"' % p) for p in i["phones"]]})
    if i["pseudos"]:
        groups.append({"label": "Vos pseudos", "items": [item(p, '"%s"' % p) for p in i["pseudos"]]})
    return groups


# ---------- moteurs de recherche ----------

def _json(url, headers=None):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "efface-moi", "Accept": "application/json"},
                                                   **(headers or {})))
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read(3 * 1024 * 1024).decode("utf-8", "replace"))


def _strip(text):
    return " ".join(re.sub(r"<[^>]+>", " ", str(text or "")).split())[:400]


def search_searxng(base, q):
    data = _json(base.rstrip("/") + "/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "language": "fr-FR", "safesearch": 0}))
    return [{"url": r.get("url") or "", "title": _strip(r.get("title")), "snippet": _strip(r.get("content"))}
            for r in data.get("results") or [] if isinstance(r, dict)]


def search_brave(key, q):
    data = _json(BRAVE_URL + "?" + urllib.parse.urlencode({"q": q, "count": 20, "country": "fr"}),
                 {"X-Subscription-Token": key})
    return [{"url": r.get("url") or "", "title": _strip(r.get("title")), "snippet": _strip(r.get("description"))}
            for r in (data.get("web") or {}).get("results") or [] if isinstance(r, dict)]


def search(q):
    s = settings()
    if s["provider"] == "searxng" and s["searxng_url"]:
        return search_searxng(s["searxng_url"], q)
    if s["provider"] == "brave" and store.get_secret("brave_key"):
        return search_brave(store.get_secret("brave_key"), q)
    raise ValueError("Aucun moteur de recherche n'est configuré.")


def test_provider():
    """Recherche neutre, sans rapport avec l'utilisateur, pour vérifier le réglage."""
    try:
        return {"ok": True, "count": len(search("wikipedia"))}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except urllib.error.HTTPError as e:
        hint = " SearXNG doit autoriser le format JSON (search.formats dans settings.yml)." if e.code == 403 else ""
        return {"ok": False, "error": "Le moteur a répondu %d.%s" % (e.code, hint)}
    except Exception as e:  # noqa: BLE001 - affichée telle quelle
        return {"ok": False, "error": "%s : %s" % (type(e).__name__, mailer._clean(e))}


# ---------- tri des résultats ----------

DROP_PARAMS = re.compile(r"^(utm_|mc_)|^(fbclid|gclid|igshid|ref|ref_src|ref_url|srsltid)$", re.I)


def norm_url(url):
    """Même page, même adresse : sans ancre ni paramètres de pistage. Renvoie "" si ce n'est pas une page web."""
    try:
        u = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
                                    if not DROP_PARAMS.search(k)])
    path = u.path if u.path not in ("", "/") else "/"
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), path, query, ""))[:1000]


def present(kind, term, text, digits=""):
    """Le terme figure-t-il dans ce texte normalisé (minuscules, sans accents ni ponctuation) ?"""
    if kind == "phone":
        d = re.sub(r"\D", "", term)
        if d.startswith("33") and len(d) == 11:
            d = "0" + d[2:]
        return bool(d) and (d in digits or (d.startswith("0") and "33" + d[1:] in digits))
    padded = " %s " % text
    if kind == "name":
        return any(" %s " % companies.norm(n) in padded for n in name_forms(term) or [term])
    return " %s " % companies.norm(term) in padded


def all_terms():
    i = identity()
    return ([("name", i["name"])] if i["name"] else []) + [("email", e) for e in i["emails"]] \
        + [("phone", p) for p in i["phones"][:1]] + [("pseudo", p) for p in i["pseudos"]]


def verify(url, terms):
    """Lit la page et renvoie les types de données de l'utilisateur qui y figurent. None : page illisible."""
    res = contact._fetch(url)
    if not res:
        return None
    text = companies.norm(re.sub(r"<(script|style)\b.*?</\1>", " ", res[1], flags=re.S | re.I))
    digits = re.sub(r"\D", "", res[1])
    return list(dict.fromkeys(k for k, t in terms if present(k, t, text, digits)))


def _title(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return _strip(m.group(1))[:200] if m else ""


# ---------- pages trouvées ----------

def _insert(url, title, snippet, source, query, evidence, note=""):
    domain = companies.base_domain(urllib.parse.urlsplit(url).hostname or "")
    with store.db() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO findings(url,domain,title,snippet,source,query,evidence,status,note,found_at,status_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (url, domain, title[:300], snippet[:500], source, query, evidence, "new", note, store.iso(), store.iso()))
        return cur.lastrowid if cur.rowcount else None


def scan(progress=lambda *a: None, pause=None):
    """Interroge le moteur sur chaque élément d'identité, ne garde que les pages où il figure vraiment."""
    guard()
    pause = PAUSE if pause is None else pause
    ensure_jdm()
    s, qs, terms = settings(), queries(), all_terms()
    known = {r["url"] for r in store.rows("SELECT url FROM findings")}
    ignore = set(s["ignore_domains"])
    candidates, errors = {}, []
    for n, (kind, term, q) in enumerate(qs):
        progress(n, len(qs), "Recherche de %s…" % q)
        try:
            results = search(q)
        except Exception as e:  # noqa: BLE001 - une recherche en échec n'arrête pas les autres
            errors.append("%s : %s" % (q, mailer._clean(e)))
            results = []
        for r in results[:20]:
            url = norm_url(r["url"])
            domain = companies.base_domain(urllib.parse.urlsplit(url).hostname or "") if url else ""
            if not url or url in known or url in candidates or domain in ignore:
                continue
            excerpt = companies.norm("%s %s %s" % (r["title"], r["snippet"], urllib.parse.unquote(url)))
            in_excerpt = present(kind, term, excerpt, re.sub(r"\D", "", r["title"] + r["snippet"]))
            if kind == "name" and not in_excerpt:
                continue        # le moteur a élargi la recherche : ce résultat ne cite pas le nom exact
            candidates[url] = dict(r, kind=kind, q=q, in_excerpt=in_excerpt)
        if pause and n < len(qs) - 1:
            time.sleep(pause)
    added = 0
    for n, (url, c) in enumerate(candidates.items()):
        progress(n, len(candidates), "Vérification des pages trouvées…")
        on_page = verify(url, terms) if n < MAX_VERIFY else None
        if on_page == [] and not c["in_excerpt"]:
            continue            # page lisible, aucune de vos données dessus ni dans l'extrait : fausse piste
        if on_page:
            evidence, note = "page", "Sur la page : " + ", ".join(KIND[k] for k in on_page)
        elif c["in_excerpt"]:
            evidence, note = "extrait", ""
        else:
            evidence, note = "moteur", ""
        if _insert(url, c["title"], c["snippet"], "search", c["q"], evidence, note):
            added += 1
    out = {"at": store.iso(), "new": added, "queries": len(qs), "seen": len(candidates), "errors": errors[:3]}
    store.set("footprint_last", out)
    return out


def add_manual(url):
    clean = norm_url(url if re.match(r"https?://", str(url or "").strip(), re.I) else "https://" + str(url or "").strip())
    host = urllib.parse.urlsplit(clean).hostname or "" if clean else ""
    if not clean or re.search(r"\s", host) or ("." not in host and not contact.ALLOW_PRIVATE):
        raise ValueError("Adresse de page invalide.")
    existing = store.one("SELECT id FROM findings WHERE url=?", (clean,))
    if existing:
        return existing["id"]
    page = contact._fetch(clean)
    return _insert(clean, _title(page[1]) if page else "", "", "manual", "", "vous")


def get(fid):
    f = store.one("SELECT * FROM findings WHERE id=?", (fid,))
    if not f:
        raise ValueError("Page introuvable dans la liste.")
    return f


def list_findings():
    rows = store.rows("SELECT f.*, r.status AS request_status FROM findings f LEFT JOIN requests r ON r.id=f.request_id "
                      "ORDER BY (f.status='new') DESC, f.id DESC")
    for f in rows:
        f["delete"] = deletion_info(f["domain"])
    return rows


def act(fid, action):
    f = get(fid)
    now = store.now()
    if action in ("ignored", "account", "removed"):
        store.execute("UPDATE findings SET status=?, status_at=? WHERE id=?", (action, store.iso(now), fid))
    elif action == "new":
        store.execute("UPDATE findings SET status='new', status_at=?, due_at=NULL WHERE id=?", (store.iso(now), fid))
    elif action == "delisting":
        store.execute("UPDATE findings SET status='delisting', status_at=?, due_at=? WHERE id=?",
                      (store.iso(now), store.iso(now + timedelta(days=DELIST_DAYS)), fid))
    else:
        raise ValueError("Action inconnue.")
    return f["id"]


def delete(ids):
    for fid in ids:
        store.execute("DELETE FROM findings WHERE id=?", (int(fid),))


def request_removal(fid, email="", name=""):
    """Prépare la demande d'effacement au site qui publie la page. Sans contact connu, renvoie des propositions."""
    f = get(fid)
    if not engine.profile_ready():
        raise ValueError("Remplissez d'abord votre nom et votre e-mail dans Réglages.")
    known = companies.domain_index().get(f["domain"])
    if known and known.get("email"):
        slug = known["slug"]
    elif email:
        slug = companies.add_custom(name.strip() or f["domain"], email, "https://" + f["domain"])
    else:
        try:
            found = contact.find("https://" + f["domain"])
        except ValueError as e:
            found = {"candidates": [], "policy": "", "error": str(e)}
        return {"need_contact": True, "domain": f["domain"], "candidates": found["candidates"],
                "policy": found.get("policy", ""), "error": found.get("error", "")}
    rid, already_sent = engine.request_for_urls(slug, [f["url"]])
    store.execute("UPDATE findings SET status='requested', request_id=?, status_at=? WHERE id=?", (rid, store.iso(), fid))
    return {"need_contact": False, "request_id": rid, "already_sent": already_sent}


def delisting_info(fid):
    f = get(fid)
    p = store.get("profile", {})
    out = {"text": letters.delisting(p, [f["url"]]),
           "links": [{"engine": "Google", "url": "https://reportcontent.google.com/forms/rtbf"},
                     {"engine": "Bing", "url": "https://www.bing.com/webmaster/tools/eu-privacy-request"}],
           "complaint": ""}
    if f["status"] == "delisting":
        out["complaint"] = letters.delisting_complaint(p, f["url"], f["status_at"])
    return out


def _http_status(url):
    try:
        contact._check(url)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.build_opener(contact._Guard).open(req, timeout=contact.TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001 - site injoignable : on ne conclut rien
        return None


def recheck(progress=lambda *a: None):
    """Les pages dont on attend la suppression existent-elles encore ? Seul un « introuvable » franc compte."""
    rows = store.rows("SELECT id, url FROM findings WHERE status IN ('requested','delisting','account') "
                      "ORDER BY checked_at IS NOT NULL, checked_at LIMIT 40")
    gone = 0
    for n, f in enumerate(rows):
        progress(n, len(rows), "Vérification des pages…")
        code = _http_status(f["url"])
        if code in (404, 410):
            store.execute("UPDATE findings SET status='removed', status_at=?, checked_at=?, note=? WHERE id=?",
                          (store.iso(), store.iso(), "Page introuvable (code %d)" % code, f["id"]))
            gone += 1
        else:
            store.execute("UPDATE findings SET checked_at=? WHERE id=?", (store.iso(), f["id"]))
    return {"checked": len(rows), "gone": gone}


def weekly(progress=lambda *a: None):
    """Appelé par le suivi quotidien : une recherche par semaine, puis le contrôle des pages en attente."""
    s = settings()
    if not (s["weekly"] and provider_ready()):
        return None
    last = store.get("footprint_last")
    if last and store.now() - datetime.fromisoformat(last["at"]) < timedelta(days=WEEK_DAYS):
        return None
    try:
        guard()
    except ValueError:
        return None
    out = scan(progress)
    out["recheck"] = recheck(progress)
    if s["notify"] and out["new"]:
        pages = store.rows("SELECT url, title FROM findings WHERE status='new' ORDER BY id DESC LIMIT ?", (out["new"],))
        body = ("Efface-moi a trouvé %d nouvelle(s) page(s) qui semblent vous concerner :\n\n" % out["new"]
                + "\n".join("- %s\n  %s" % (p["title"] or "(sans titre)", p["url"]) for p in pages)
                + "\n\nOuvrez l'onglet Empreinte pour les examiner : les écarter, ou lancer leur suppression.\n")
        try:
            me = store.get("profile", {}).get("email")
            mailer.send(me, "Efface-moi : %d nouvelle(s) page(s) vous concernant" % out["new"], body)
        except Exception as e:  # noqa: BLE001 - l'alerte est un plus, son échec ne casse pas le suivi
            out["notify_error"] = mailer._clean(e)
    return out


# ---------- supprimer un compte : base libre JustDeleteMe ----------

_jdm = {"mtime": None, "index": {}}


def update_jdm():
    req = urllib.request.Request(JDM_URL, headers={"User-Agent": "efface-moi"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read(6 * 1024 * 1024).decode("utf-8"))
    keep = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict) or not isinstance(it.get("domains"), list):
            continue
        url = str(it.get("url") or "")
        keep.append({"name": str(it.get("name") or "")[:80], "url": url if re.match(r"https?://\S+$", url) else "",
                     "difficulty": str(it.get("difficulty") or ""), "domains": [str(d).lower() for d in it["domains"]][:10],
                     "notes": str(it.get("notes_fr") or it.get("notes") or "")[:400]})
    if len(keep) < 50:
        raise RuntimeError("Liste des suppressions de compte incomplète.")
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    JDM_FILE.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    return len(keep)


def ensure_jdm(max_age_days=30):
    """Télécharge la liste si elle manque ou date. À n'appeler que depuis une tâche qui va déjà sur Internet."""
    try:
        if not JDM_FILE.exists() or time.time() - JDM_FILE.stat().st_mtime > max_age_days * 86400:
            update_jdm()
    except Exception:  # noqa: BLE001 - un plus : sans elle, on n'affiche simplement pas les liens de suppression
        pass


def deletion_info(domain):
    if not domain or not JDM_FILE.exists():
        return None
    mtime = JDM_FILE.stat().st_mtime
    if _jdm["mtime"] != mtime:
        index = {}
        try:
            for it in json.loads(JDM_FILE.read_text(encoding="utf-8")):
                for d in it["domains"]:
                    index.setdefault(companies.base_domain(d), it)
        except (ValueError, KeyError, TypeError):
            index = {}
        _jdm.update(mtime=mtime, index=index)
    it = _jdm["index"].get(domain)
    if not it:
        return None
    return {"name": it["name"], "url": it["url"], "notes": it["notes"],
            "difficulty": DIFFICULTY.get(it["difficulty"], it["difficulty"])}


def enrich_accounts(scan_result):
    """Ajoute à chaque compte trouvé par la recherche de pseudo le lien pour le supprimer, quand il est connu."""
    if not scan_result:
        return scan_result
    out = dict(scan_result)
    out["found"] = [dict(f, delete=deletion_info(companies.base_domain(f.get("home") or f.get("url") or "")))
                    for f in scan_result.get("found", [])]
    return out


# ---------- liste guidée ----------

def checklist():
    done = store.get("checklist", {})
    return [dict(c, done=done.get(c["id"], "")) for c in CHECKLIST]


def check(item_id, done):
    if item_id not in {c["id"] for c in CHECKLIST}:
        raise ValueError("Élément inconnu.")
    state = store.get("checklist", {})
    if done:
        state[item_id] = store.iso()
    else:
        state.pop(item_id, None)
    store.set("checklist", state)


def state():
    try:
        guard()
        blocked = ""
    except ValueError as e:
        blocked = str(e)
    return {"settings": dict(settings(), has_key=bool(store.get_secret("brave_key"))), "ready": provider_ready(),
            "blocked": blocked, "status_labels": STATUS, "evidence_labels": EVIDENCE, "findings": list_findings(),
            "prepared": prepared(), "checklist": checklist(), "last_scan": store.get("footprint_last")}
