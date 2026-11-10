"""Recherche de l'adresse « données personnelles » d'une entreprise sur son propre site web.

On lit la page d'accueil, on suit les liens vers la politique de confidentialité, les mentions légales et la
page contact, puis on classe les adresses e-mail trouvées. Ce ne sont que des propositions : l'utilisateur
vérifie et choisit, rien n'est envoyé automatiquement à une adresse trouvée ainsi.
"""
import collections
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from . import companies
from .discover import NO_REPLY, UA

ALLOW_PRIVATE = False       # réservé aux tests (serveur factice sur 127.0.0.1)
MAX_BYTES = 600_000
TIMEOUT = 8
MAX_PAGES = 7

# Mots repérés dans un lien (texte ou adresse), sans accents ni ponctuation. Plus le score est haut, plus la
# page a de chances de donner l'adresse du délégué à la protection des données.
LINK_HINTS = (
    (3, ("confidentialite", "privacy", "vie privee", "donnees perso", "rgpd", "gdpr", "protection des donnees",
         "data protection", "datenschutz")),
    (2, ("mentions legales", "legal", "cgu", "cgv", "conditions generales", "terms", "impressum")),
    (1, ("contact", "cookies")),
)
COMMON_PATHS = ("/politique-de-confidentialite", "/confidentialite", "/privacy-policy", "/privacy",
                "/mentions-legales", "/contact")

EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63})@((?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,24})"
                   r"(?![A-Za-z0-9-])")
NOT_A_TLD = {"png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "css", "js", "json", "woff", "woff2", "ico", "map"}
JUNK_DOMAINS = {"sentry.io", "wixpress.com", "example.com", "example.org", "exemple.com", "exemple.fr",
                "domain.com", "domaine.fr", "domaine.com", "email.com", "yourdomain.com", "votredomaine.fr",
                "mail.com", "site.com", "email.fr", "mail.fr", "domaine.org", "doe.com"}
JUNK_LOCALS = {"name", "nom", "prenom.nom", "votre", "your", "email", "adresse", "john.doe", "jean.dupont",
               "user", "utilisateur", "xxx", "exemple", "example", "vous", "you", "moi"}
KINDS = (
    ("privacy", 100, "protection des données",
     re.compile(r"dpo|dpd|privacy|rgpd|gdpr|donnees|data.?protect|protection|vie.?privee|cnil|datenschutz|"
                r"informatique.?et.?libertes|personal.?data|mesdonnees")),
    ("legal", 60, "service juridique", re.compile(r"legal|juridique|compliance|conformite")),
    ("contact", 40, "contact général",
     re.compile(r"contact|service.?client|support|sav|client|hello|bonjour|info|help|aide|customer|accueil|relation")),
)


def _check(url):
    """Refuse tout ce qui n'est pas un site public : pas de fichier local, pas d'adresse du réseau interne."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Adresse de site invalide.")
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        raise ValueError("Site introuvable : %s" % u.hostname) from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise ValueError("Adresse refusée : elle ne désigne pas un site public.")


class _Guard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url):
    """Renvoie (adresse finale, texte de la page) ou None."""
    try:
        _check(url)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
                                                   "Accept": "text/html,application/xhtml+xml"})
        with urllib.request.build_opener(_Guard).open(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            if ctype and "html" not in ctype and "text" not in ctype:
                return None
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
            try:
                return r.geturl(), raw.decode(charset, "replace")
            except LookupError:
                return r.geturl(), raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - site injoignable, bloqué ou mal formé : on passe à la page suivante
        return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text)))
            self._href = None


def pick_links(page, base_url, site):
    """Fonction pure : les pages du même site les plus susceptibles de donner l'adresse, meilleures d'abord."""
    parser = _Links()
    try:
        parser.feed(page)
    except Exception:  # noqa: BLE001 - HTML cassé : on garde les liens déjà lus
        pass
    best = {}
    for href, text in parser.links:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urllib.parse.urldefrag(urllib.parse.urljoin(base_url, href.strip()))[0]
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or companies.base_domain(u.hostname or "") != site:
            continue
        label = companies.norm(urllib.parse.unquote(u.path) + " " + text)
        score = max((s for s, words in LINK_HINTS if any(w in label for w in words)), default=0)
        if score and score > best.get(url, 0):
            best[url] = score
    return sorted(best.items(), key=lambda kv: (-kv[1], len(kv[0])))[:MAX_PAGES]


def _cf_decode(hexa):
    """Adresses masquées par Cloudflare (« [email protected] »)."""
    try:
        b = bytes.fromhex(hexa)
        return bytes(c ^ b[0] for c in b[1:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError, IndexError):
        return ""


def extract_emails(page):
    """Fonction pure : adresses e-mail plausibles d'une page, avec leur nombre d'apparitions."""
    # Les pages modernes embarquent leur texte dans du JavaScript, où « > » s'écrit antislash-u-003e.
    t = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), page)
    t = html.unescape(t)
    hidden = re.findall(r'data-cfemail="([0-9a-fA-F]+)"', t) + re.findall(r"email-protection#([0-9a-fA-F]+)", t)
    t += " " + " ".join(_cf_decode(h) for h in hidden)
    t = re.sub(r"%40", "@", t, flags=re.I)
    t = re.sub(r"\s*[\[({]\s*(?:at|arobase)\s*[\])}]\s*", "@", t, flags=re.I)
    t = re.sub(r"\s*[\[({]\s*(?:dot|point)\s*[\])}]\s*", ".", t, flags=re.I)
    out = collections.Counter()
    for local, domain in EMAIL.findall(t):
        local, domain = local.lower().strip("."), domain.lower()
        if (domain.rsplit(".", 1)[-1] in NOT_A_TLD or companies.base_domain(domain) in JUNK_DOMAINS
                or local in JUNK_LOCALS or re.fullmatch(r"[0-9a-f]{20,}", local) or NO_REPLY.search(local)):
            continue
        out["%s@%s" % (local, domain)] += 1
    return out


def classify(email):
    local = companies.norm(email.split("@")[0]).replace(" ", "")
    for kind, score, label, pattern in KINDS:
        if pattern.search(local):
            return kind, score, label
    return "other", 20, "autre adresse"


def rank(found, site):
    """Fonction pure. found : {adresse: {"count", "source", "page"}}. Meilleure proposition d'abord."""
    out = []
    for email, f in found.items():
        kind, score, label = classify(email)
        same = companies.base_domain(email) == site
        score += (15 if same else 0) + 5 * f["page"] + min(f["count"], 3)
        out.append({"email": email, "kind": kind, "label": label, "source": f["source"], "same_domain": same,
                    "score": score})
    out.sort(key=lambda c: (-c["score"], c["email"]))
    return out


def find(web):
    web = (web or "").strip()
    if not web:
        raise ValueError("Indiquez d'abord le site web de l'entreprise.")
    if "@" in web and "/" not in web:
        web = web.rsplit("@", 1)[-1]
    if not re.match(r"https?://", web, re.I):
        web = "https://" + web
    site = companies.base_domain(urllib.parse.urlsplit(web).hostname or "")
    if not site or ("." not in site and not ALLOW_PRIVATE):
        raise ValueError("Adresse de site invalide.")
    home = None
    starts = [web] if ALLOW_PRIVATE else [web, "https://www." + site, "https://" + site]
    for start in dict.fromkeys(starts):
        home = _fetch(start)
        if home:
            break
    if not home:
        return {"site": site, "pages": 0, "candidates": [], "policy": "",
                "error": "Le site %s ne répond pas, ou refuse les lectures automatiques." % site}
    home_url, home_text = home
    site = companies.base_domain(urllib.parse.urlsplit(home_url).hostname or "") or site
    links = pick_links(home_text, home_url, site)
    if len(links) < 3:      # peu de liens utiles (site en JavaScript, par exemple) : on essaie les adresses habituelles
        known = {u for u, _ in links}
        root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(home_url))
        links += [(root + p, 2) for p in COMMON_PATHS if root + p not in known]
    links = links[:MAX_PAGES]
    pages = [(home_url, 0, home_text)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (url, score), res in zip(links, pool.map(lambda l: _fetch(l[0]), links)):
            if res:
                pages.append((res[0], score, res[1]))
    found = {}
    for url, score, text in pages:
        for email, n in extract_emails(text).items():
            f = found.setdefault(email, {"count": 0, "source": url, "page": score})
            f["count"] += n
            if score > f["page"]:
                f["page"], f["source"] = score, url
    # La page « confidentialité » la mieux notée, pour que l'utilisateur puisse l'ouvrir si rien n'est trouvé.
    read = {u for u, _, _ in pages}
    policy = next((u for u, sc in links if sc == 3 and u in read), next((u for u, sc in links if sc == 3), ""))
    ranked = rank(found, site)
    if any(c["kind"] != "other" for c in ranked):
        # Dès qu'une vraie adresse de contact existe, les autres (souvent des exemples ou des personnes) gênent.
        ranked = [c for c in ranked if c["kind"] != "other"]
    return {"site": site, "pages": len(pages), "candidates": ranked[:6], "policy": policy}
