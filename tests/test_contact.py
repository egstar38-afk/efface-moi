"""Essai de la recherche d'adresse de contact sur un faux site local. Aucun accès à Internet.

    python tests/test_contact.py
"""
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["EFFACE_DATA"] = tempfile.mkdtemp(prefix="efface-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import contact  # noqa: E402


def cf(email, key=0x42):
    return "%02x" % key + "".join("%02x" % (b ^ key) for b in email.encode())


SEEN = []
PAGES = {
    "/": """<html><body><img src="logo@2x.png"> Écrivez à no-reply@boutique.test
        <a href="/politique-de-confidentialite#haut">Politique de confidentialit&eacute;</a>
        <a href="/contact">Nous contacter</a> <a href="/blog">Blog</a>
        <a href="https://autre.example/privacy">Privacy (autre site)</a> <a href="mailto:x@y.test">mail</a></body></html>""",
    "/politique-de-confidentialite": """<p>Notre délégué : dpo [at] boutique [dot] test. Groupe :
        <a class="__cf_email__" data-cfemail="%s">[email&#160;protected]</a>. Exemple : prenom.nom@exemple.fr</p>""" % cf("privacy@groupe.test"),
    "/contact": """<a href="mailto:contact%40boutique.test">Service client</a> webmaster@boutique.test
        0123456789abcdef0123456789abcdef@sentry.io""",
    "/blog": "auteur@blog.test",
}


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append(self.path)
        body = PAGES.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((body or "introuvable").encode("utf-8"))


srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ok = 0


def check(label, cond):
    global ok
    print(("  ok     " if cond else "  ECHEC  ") + label)
    if not cond:
        sys.exit(1)
    ok += 1


def refused(url):
    try:
        contact._check(url)
        return False
    except ValueError:
        return True


print("Garde-fous")
check("réseau local, cette machine et fichiers refusés", refused("http://127.0.0.1:%d/" % srv.server_address[1])
      and refused("http://localhost/") and refused("http://192.168.1.1/") and refused("file:///C:/Windows/win.ini"))
check("aucune page lue quand l'adresse est refusée", contact.find("http://127.0.0.1:%d" % srv.server_address[1])["candidates"] == []
      and SEEN == [])

print("Lecture des pages")
mails = contact.extract_emails(PAGES["/"] + PAGES["/politique-de-confidentialite"] + PAGES["/contact"])
check("adresses masquées retrouvées ([at], Cloudflare, %40)",
      {"dpo@boutique.test", "privacy@groupe.test", "contact@boutique.test"} <= set(mails))
check("fausses adresses écartées (image, no-reply, exemple, clé technique)",
      not any(x in " ".join(mails) for x in ("2x.png", "no-reply", "exemple.fr", "sentry.io")))
links = contact.pick_links(PAGES["/"], "http://localhost/", "localhost")
check("liens utiles du même site, confidentialité d'abord",
      [u for u, _ in links] == ["http://localhost/politique-de-confidentialite", "http://localhost/contact"])

print("Recherche complète sur le faux site")
contact.ALLOW_PRIVATE = True
res = contact.find("http://localhost:%d" % srv.server_address[1])
order = [c["email"] for c in res["candidates"]]
check("le DPO arrive en tête, puis l'autre adresse « données », puis le contact",
      order[:3] == ["dpo@boutique.test", "privacy@groupe.test", "contact@boutique.test"])
check("page d'origine indiquée", res["candidates"][0]["source"].endswith("/politique-de-confidentialite")
      and res["candidates"][0]["kind"] == "privacy")
check("le blog n'est pas lu", "/blog" not in SEEN and res["pages"] >= 3)
print("\n%d vérifications réussies." % ok)
