"""Essai du module Empreinte avec un faux moteur de recherche et de fausses pages. Aucun accès à Internet.

    python tests/test_empreinte.py
"""
import json
import os
import sys
import tempfile
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

tmp = tempfile.mkdtemp(prefix="efface-test-")
os.environ["EFFACE_DATA"] = tmp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import companies, contact, engine, footprint, letters, mailer, store  # noqa: E402

contact.ALLOW_PRIVATE = True
GONE = set()
ASKED = []
PAGES = {
    "/annuaire": "<html><head><title>Annuaire des anciens &eacute;l&egrave;ves</title></head><body>MARTIN Camille, promo 2008</body></html>",
    "/forum": "<title>Forum bricolage</title><p>Écrivez-moi : camille@example.org</p><script>var x='bruit'</script>",
    "/sans-rapport": "<title>Recette de crêpes</title><p>Farine, œufs, lait.</p>",
    "/petite-annonce": "<title>Vends vélo</title>Tél. 06.12.34.56.78",
    "/profil": "<title>cam_martin38 sur ForumJeux</title>Profil de cam_martin38",
}


def results(q):
    r = lambda path, title, snippet="": {"url": BASE + path, "title": title, "content": snippet}
    if "Camille Martin" in q:
        return [r("/annuaire?utm_source=moteur#haut", "Annuaire des anciens élèves", "… Martin Camille, promo 2008 …"),
                r("/annuaire", "Annuaire (doublon)", "Camille MARTIN"),
                r("/sans-rapport", "Martin Dupont et Camille Durand", "aucun rapport")]
    if "camille@example.org" in q:
        return [r("/forum", "Forum bricolage", "Écrivez-moi…"), r("/sans-rapport", "Recette de crêpes"),
                r("/bloquee", "Page protégée", "contact : camille@example.org")]
    if "06 12 34 56 78" in q:
        return [r("/petite-annonce", "Vends vélo")]
    if "cam_martin38" in q:
        return [r("/profil", "cam_martin38 sur ForumJeux"), {"url": "javascript:alert(1)", "title": "x"}]
    return []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/search":
            q = parse_qs(u.query)
            ASKED.append(q.get("q", [""])[0])
            if q.get("format") != ["json"]:
                return self._send(403, "interdit")
            return self._send(200, json.dumps({"results": results(q["q"][0])}), "application/json")
        if u.path in GONE:
            return self._send(404, "introuvable")
        if u.path == "/bloquee":
            return self._send(403, "robots interdits")
        body = PAGES.get(u.path)
        self._send(200 if body else 404, body or "introuvable")

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://localhost:%d" % srv.server_address[1]
ok = 0


def check(label, cond):
    global ok
    print(("  ok     " if cond else "  ECHEC  ") + label)
    if not cond:
        sys.exit(1)
    ok += 1


def by_path():
    return {urlsplit(f["url"]).path: f for f in footprint.list_findings()}


def fails(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


store.set("profile", {"name": "Camille Martin", "email": "camille@example.org", "phone": "+33 6 12 34 56 78"})

print("Ce que l'on cherche")
footprint.save_settings({"provider": "searxng", "searxng_url": BASE + "/", "pseudos": "@cam_martin38, x", "notify": True,
                         "ignore_domains": "https://www.Mon-Site.example/page, pas-un-domaine"})
s = footprint.settings()
check("réglages nettoyés", s["searxng_url"] == BASE and s["pseudos"] == ["cam_martin38"] and s["ignore_domains"] == ["mon-site.example"])
qs = [q for _, _, q in footprint.queries()]
check("requêtes exactes : nom dans les deux sens, e-mail, téléphone sous trois formes, pseudo",
      qs == ['"Camille Martin"', '"Martin Camille"', '"camille@example.org"', '"06 12 34 56 78"', '"0612345678"',
             '"+33612345678"', '"cam_martin38"'])
groups = footprint.prepared()
check("recherches préparées pour le navigateur", [g["label"] for g in groups][:2] == ["Votre nom", "Votre nom sur les réseaux et annuaires"]
      and groups[0]["items"][0]["links"][0]["url"] == "https://www.google.com/search?q=%22Camille+Martin%22")
check("adresses ramenées à une forme unique", footprint.norm_url(BASE + "/a/?utm_source=x&id=2&fbclid=9#b") == BASE + "/a/?id=2"
      and footprint.norm_url("javascript:alert(1)") == "" and footprint.norm_url("ftp://x.fr/a") == "")

print("Garde-fou")
check("recherche refusée tant que la messagerie connectée n'est pas celle du profil", fails(footprint.scan) and ASKED == [])
mailer.save_settings({"email": "quelquun-dautre@example.org"})
store.set_password("secret")
check("refusée aussi avec la boîte de quelqu'un d'autre", fails(footprint.scan) and ASKED == [])
mailer.save_settings({"email": "Camille@Example.org"})

print("Recherche automatique")
out = footprint.scan(pause=0)
found = by_path()
check("une recherche par requête", len(ASKED) == 7 and out["queries"] == 7)
check("pages retenues, sans doublon ni fausse piste",
      sorted(found) == ["/annuaire", "/bloquee", "/forum", "/petite-annonce", "/profil"] and out["new"] == 5)
check("preuve lue sur la page (nom inversé, e-mail, téléphone pointé, pseudo)",
      found["/annuaire"]["evidence"] == "page" and "nom" in found["/annuaire"]["note"]
      and "e-mail" in found["/forum"]["note"] and "téléphone" in found["/petite-annonce"]["note"]
      and "pseudo" in found["/profil"]["note"])
check("page illisible gardée grâce à l'extrait du moteur", found["/bloquee"]["evidence"] == "extrait")
check("titre et domaine enregistrés", found["/forum"]["title"] == "Forum bricolage" and found["/forum"]["domain"] == "localhost")
footprint.act(found["/profil"]["id"], "ignored")
again = footprint.scan(pause=0)
check("nouveau passage : rien en double, la page écartée le reste", again["new"] == 0 and by_path()["/profil"]["status"] == "ignored")

print("Page signalée à la main")
fid = footprint.add_manual(BASE + "/petite-annonce#photo")
check("déjà connue : pas de doublon", fid == found["/petite-annonce"]["id"])
PAGES["/blog"] = "<title>Blog de voyage</title>photo de Camille"
fid = footprint.add_manual(BASE + "/blog?utm_campaign=x")
check("ajoutée avec son titre", by_path()["/blog"]["title"] == "Blog de voyage" and by_path()["/blog"]["evidence"] == "vous")
check("adresse invalide refusée", fails(lambda: footprint.add_manual("pas une adresse")))

print("Faire supprimer")
r = footprint.request_removal(found["/forum"]["id"])
check("site inconnu : on demande d'abord un contact", r["need_contact"] and r["domain"] == "localhost")
r = footprint.request_removal(found["/forum"]["id"], "webmaster@forum.example", "Forum bricolage")
req = engine.detail(r["request_id"])
check("brouillon créé, la page est citée dans le courrier", req["status"] == "draft" and BASE + "/forum" in req["body"]
      and "contenus publiés aux adresses suivantes" in req["body"] and by_path()["/forum"]["status"] == "requested")
r2 = footprint.request_removal(found["/annuaire"]["id"])
req = engine.detail(r2["request_id"])
check("deuxième page du même site : ajoutée au même brouillon", r2["request_id"] == r["request_id"]
      and BASE + "/annuaire" in req["body"] and BASE + "/forum" in req["body"] and json.loads(req["urls"]) == [BASE + "/forum", BASE + "/annuaire"])
store.update_request(req["id"], status="sent", sent_at=store.iso())
r3 = footprint.request_removal(found["/petite-annonce"]["id"])
check("demande déjà partie : la page y est rattachée, sans second courrier", r3["already_sent"] and r3["request_id"] == req["id"]
      and BASE + "/petite-annonce" not in engine.detail(req["id"])["body"])
check("statut de la demande visible sur la page", by_path()["/forum"]["request_status"] == "sent")
lang, _, body = letters.erasure(engine.profile(), {"name": "X Ltd", "relevant-countries": ["gb"]}, urls=["https://x.example/p"])
check("courrier anglais : pages citées aussi", lang == "en" and "- https://x.example/p" in body and "following addresses" in body)
check("courrier sans page : inchangé", "adresses suivantes" not in letters.erasure(engine.profile(), {"name": "Y", "custom": True})[2])

print("Déréférencement")
bid = by_path()["/bloquee"]["id"]
footprint.act(bid, "delisting")
f = footprint.get(bid)
info = footprint.delisting_info(bid)
check("délai d'un mois suivi", f["status"] == "delisting" and f["due_at"][:10] == store.iso(store.now() + timedelta(days=31))[:10])
check("texte prêt, liens Google et Bing, plainte CNIL", "Camille Martin" in info["text"] and BASE + "/bloquee" in info["text"]
      and [x["engine"] for x in info["links"]] == ["Google", "Bing"] and "article 77" in info["complaint"])

print("La page a-t-elle disparu ?")
GONE.add("/forum")
out = footprint.recheck()
check("page introuvable : marquée disparue ; page bloquée : on ne conclut rien", out["gone"] == 1
      and by_path()["/forum"]["status"] == "removed" and by_path()["/bloquee"]["status"] == "delisting")

print("Suppression de compte")
footprint.JDM_FILE.write_text(json.dumps([{"name": "ForumJeux", "url": "https://forumjeux.example/compte/supprimer",
                                           "difficulty": "hard", "domains": ["forumjeux.example"], "notes": "Par e-mail."}]), encoding="utf-8")
check("lien de suppression connu", footprint.deletion_info("forumjeux.example") == {
    "name": "ForumJeux", "url": "https://forumjeux.example/compte/supprimer", "notes": "Par e-mail.", "difficulty": "difficile"})
acc = footprint.enrich_accounts({"found": [{"site": "ForumJeux", "url": "https://www.forumjeux.example/u/cam", "home": "https://forumjeux.example/"},
                                           {"site": "Autre", "url": "https://autre.example/cam", "home": ""}]})
check("comptes de la recherche de pseudo complétés", acc["found"][0]["delete"]["difficulty"] == "difficile" and acc["found"][1]["delete"] is None)

print("Passage hebdomadaire")
check("pas deux recherches la même semaine", footprint.weekly() is None)
last = store.get("footprint_last")
store.set("footprint_last", dict(last, at=store.iso(store.now() - timedelta(days=8))))
PAGES["/nouveau"] = "<title>Nouvelle page</title>Camille Martin habite ici"
_old = results
results = lambda q: [{"url": BASE + "/nouveau", "title": "Nouvelle page", "content": "Camille Martin"}] if "Camille Martin" in q else _old(q)
footprint.PAUSE = 0
w = footprint.weekly()
alert = list(Path(tmp, "outbox").glob("*.eml"))
check("une semaine plus tard : nouvelle page trouvée, pages en attente recontrôlées", w["new"] == 1 and "recheck" in w)
check("alerte écrite pour soi-même (mode test : fichier)", len(alert) == 1 and "/nouveau" in alert[0].read_text(encoding="utf-8", errors="replace"))
cyc = engine.run_cycle()
check("le suivi quotidien ne relance pas la recherche", "footprint" not in cyc and "footprint_error" not in cyc)

print("Divers")
footprint._json = lambda url, headers=None: {"web": {"results": [{"url": "https://ex.example/a", "title": "A <strong>B</strong>", "description": "d"}]}}
check("réponse Brave lue", footprint.search_brave("cle", "x") == [{"url": "https://ex.example/a", "title": "A B", "snippet": "d"}])
footprint.check("bloctel", True)
check("liste guidée cochée", [c["id"] for c in footprint.checklist() if c["done"]] == ["bloctel"] and fails(lambda: footprint.check("inconnu", True)))
with store.db() as con:
    con.execute("ALTER TABLE requests DROP COLUMN urls")
store._migrated = False
check("base d'une version précédente complétée", all("status" in r for r in engine.list_requests()) and "urls" in engine.detail(req["id"]))
print("\n%d vérifications réussies." % ok)
