"""Efface-moi : demandes d'effacement RGPD automatisées, suivies et relancées, depuis votre propre boîte mail.

    python efface.py          ouvre l'interface dans le navigateur
    python efface.py auto     fait le suivi sans interface (réponses, relances, renouvellements) ; pour la tâche planifiée

Sur un serveur : voir serveur/efface-serveur.sh.
"""
import argparse
import json
import os
import secrets
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from core import companies, contact, discover, engine, footprint, mailer, store, task

TOKEN = secrets.token_urlsafe(24)
WEB = store.ROOT / "web" / "index.html"
ICON = store.ROOT / "web" / "icone.ico"
INSTANCE = store.DATA_DIR / "server.json"                 # port de l'instance en cours, pour n'en lancer qu'une
SERVER_MODE = bool(os.environ.get("EFFACE_SERVER"))       # tourne sur un serveur, consultée à distance
SERVER = None


def state():
    # Les tâches d'abord : si l'une se termine pendant la lecture, l'interface redemandera l'état.
    jobs = json.loads(json.dumps(engine.JOBS))
    reqs = engine.list_requests()
    stats = {}
    for r in reqs:
        stats[r["status"]] = stats.get(r["status"], 0) + 1
    mail = mailer.settings()
    mail.update(has_password=bool(store.get_password()), ready=mailer.ready(),
                imap_ready=mailer.ready(for_imap=True))
    return {
        "profile": engine.profile(), "profile_ready": engine.profile_ready(), "mail": mail,
        "companies": companies.info(), "presets": companies.PRESETS, "status_labels": engine.STATUS,
        "requests": reqs, "stats": stats, "automation": engine.automation(), "jobs": jobs,
        "sent_today": engine.sent_today(), "last_cycle": store.get("last_cycle"),
        "last_check": store.get("last_check"), "mail_scan": store.get("mail_scan"),
        "username_scan": footprint.enrich_accounts(store.get("username_scan")),
        "footprint": footprint.state(),
        "custom": [companies.summary(c) for c in store.get("custom_companies", [])],
        "shareable": engine.shareable(),
        "app": {"version": store.VERSION, "exe": store.FROZEN, "server": SERVER_MODE, "windows": sys.platform == "win32",
                "data": str(store.DATA_DIR), "outbox": str(mailer.OUTBOX)},
    }


def save_profile(b):
    p = {k: str(b.get(k, "")).strip() for k in ("name", "email", "address", "birthdate", "phone")}
    p["other_emails"] = [e.strip() for e in b.get("other_emails", []) if str(e).strip()][:10]
    for k in ("include_address", "include_birthdate", "include_phone"):
        p[k] = bool(b.get(k))
    store.set("profile", p)
    if p["email"] and not mailer.settings()["email"]:
        mailer.save_settings({"email": p["email"]})


def save_mail(b):
    if "smtp_security" in b and b["smtp_security"] not in ("ssl", "starttls", "none"):
        raise ValueError("Sécurité SMTP inconnue.")
    mailer.save_settings(b)
    if b.get("password"):
        store.set_password(str(b["password"]).replace(" ", "") if "gmail" in mailer.settings()["smtp_host"]
                           else str(b["password"]))
    elif b.get("forget_password"):
        store.set_password("")


def save_automation(b):
    a = engine.automation()
    a["auto_remind"] = bool(b.get("auto_remind", a["auto_remind"]))
    a["repeat_months"] = max(0, min(int(b.get("repeat_months", a["repeat_months"]) or 0), 24))
    store.set("automation", a)


def job(name, fn):
    if not engine.start_job(name, fn):
        raise ValueError("Cette opération est déjà en cours.")
    return {"started": True}


def open_folder(b):
    path = mailer.OUTBOX if b.get("which") == "outbox" else store.DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    if SERVER_MODE or sys.platform != "win32":
        raise ValueError("Ce dossier se trouve sur la machine qui fait tourner l'application : %s" % path)
    os.startfile(path)


def quit_app(b):
    if SERVER_MODE:
        raise ValueError("Sur un serveur, l'application reste en marche.")
    threading.Timer(0.3, SERVER.shutdown).start()


def update_bases(progress):
    """Base des entreprises, adresses partagées, et liste des liens de suppression de compte."""
    count = companies.update(progress)
    footprint.ensure_jdm(0)
    return {"count": count}


def need_search():
    if not footprint.provider_ready():
        raise ValueError("Choisissez d'abord un moteur de recherche (SearXNG ou Brave) et enregistrez.")


def need_imap():
    if not mailer.ready(for_imap=True):
        raise ValueError("Configurez d'abord votre messagerie (adresse, mot de passe d'application) dans Réglages.")


POST = {
    "/api/profile": lambda b: save_profile(b),
    "/api/mail": lambda b: save_mail(b),
    "/api/mail/test": lambda b: mailer.test_connection(),
    "/api/automation": lambda b: save_automation(b),
    "/api/companies/update": lambda b: job("update", update_bases),
    "/api/companies/custom": lambda b: {"slug": companies.add_custom(
        b.get("name", ""), b.get("email", ""), b.get("web", ""), b.get("webform", ""), b.get("address", ""))},
    "/api/companies/custom/delete": lambda b: companies.remove_custom(b.get("slug", "")),
    "/api/companies/find-contact": lambda b: contact.find(str(b.get("web", ""))[:300]),
    "/api/requests/create": lambda b: engine.create_requests(list(b.get("slugs", []))[:1000], bool(b.get("recurring"))),
    "/api/requests/send": lambda b: (engine.queue([int(i) for i in b.get("ids", [])]),
                                     job("send", engine.send_queued))[1],
    "/api/requests/status": lambda b: engine.set_status(int(b["id"]), str(b.get("action", ""))),
    "/api/requests/delete": lambda b: engine.delete([int(i) for i in b.get("ids", [])]),
    "/api/cycle": lambda b: job("cycle", engine.run_cycle),
    "/api/scan/mail": lambda b: (need_imap(), job("mailscan", lambda p: discover.scan_mailbox(
        max(100, min(int(b.get("limit", 3000)), 20000)), p)))[1],
    "/api/scan/username": lambda b: job("userscan", lambda p: (footprint.ensure_jdm(), discover.scan_username(
        str(b.get("username", "")), p))[1]),
    "/api/footprint/settings": lambda b: footprint.save_settings(b) and None,
    "/api/footprint/test": lambda b: footprint.test_provider(),
    "/api/footprint/scan": lambda b: (footprint.guard(), need_search(), job("footprint", footprint.scan))[2],
    "/api/footprint/recheck": lambda b: job("recheck", footprint.recheck),
    "/api/footprint/add": lambda b: {"id": footprint.add_manual(str(b.get("url", ""))[:1200])},
    "/api/footprint/action": lambda b: footprint.act(int(b["id"]), str(b.get("action", ""))),
    "/api/footprint/request": lambda b: footprint.request_removal(int(b["id"]), str(b.get("email", ""))[:200],
                                                                   str(b.get("name", ""))[:120]),
    "/api/footprint/delete": lambda b: footprint.delete([int(i) for i in b.get("ids", [])]),
    "/api/checklist": lambda b: footprint.check(str(b.get("id", "")), bool(b.get("done"))),
    "/api/task/enable": lambda b: (task.enable(), task.full_status())[1],
    "/api/task/disable": lambda b: (task.disable(), task.full_status())[1],
    "/api/startup/enable": lambda b: (task.startup_enable(), task.full_status())[1],
    "/api/startup/disable": lambda b: (task.startup_disable(), task.full_status())[1],
    "/api/share/done": lambda b: engine.mark_shared(str(b.get("slug", ""))[:200]),
    "/api/open-folder": lambda b: open_folder(b),
    "/api/quit": lambda b: quit_app(b),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "EffaceMoi"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        """Seule la page servie par cette application, sur cette machine, peut appeler l'API."""
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost") and secrets.compare_digest(self.headers.get("X-Token") or "", TOKEN)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            if (self.headers.get("Host") or "").split(":")[0] not in ("127.0.0.1", "localhost"):
                return self._send(403, {"error": "Interdit"})
            html = WEB.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/favicon.ico" and ICON.exists():
            return self._send(200, ICON.read_bytes(), "image/x-icon")
        if not url.path.startswith("/api/"):
            return self._send(404, {"error": "Introuvable"})
        if url.path == "/api/ping":     # sert seulement à reconnaître une instance déjà lancée
            return self._send(200, {"app": "efface-moi"})
        if not self._allowed():
            return self._send(403, {"error": "Interdit"})
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/state":
                return self._send(200, state())
            if url.path == "/api/companies/search":
                return self._send(200, companies.search(q.get("q", ""), q.get("preset", "")))
            if url.path == "/api/request":
                return self._send(200, engine.detail(int(q.get("id", 0))))
            if url.path == "/api/footprint/delisting":
                return self._send(200, footprint.delisting_info(int(q.get("id", 0))))
            if url.path == "/api/task":
                return self._send(200, task.full_status())
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "Introuvable"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "Interdit"})
        fn = POST.get(urlparse(self.path).path)
        if not fn:
            return self._send(404, {"error": "Introuvable"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1024 * 1024:
                return self._send(413, {"error": "Trop volumineux"})
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Requête invalide.")
            return self._send(200, {"ok": True, "result": fn(body)})
        except (ValueError, KeyError, TypeError) as e:
            return self._send(400, {"error": str(e) or "Requête invalide."})
        except Exception as e:  # noqa: BLE001 - affichée telle quelle dans l'interface
            return self._send(500, {"error": "%s : %s" % (type(e).__name__, mailer._clean(e))})


def running_port():
    """Port d'une instance déjà lancée sur ce dossier de données, ou None."""
    try:
        port = int(json.loads(INSTANCE.read_text(encoding="utf-8"))["port"])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:%d/api/ping" % port, timeout=1.5) as r:
            return port if json.loads(r.read()).get("app") == "efface-moi" else None
    except Exception:  # noqa: BLE001 - pas de fichier, port fermé, autre programme : aucune instance
        return None


def serve(port, open_browser):
    global SERVER
    existing = running_port()
    if existing:        # déjà lancée (fenêtre fermée, double clic) : on rouvre simplement la page
        url = "http://127.0.0.1:%d/" % existing
        print("Efface-moi tourne déjà : %s" % url, flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    if not srv:
        sys.exit("Aucun port libre entre %d et %d." % (port, port + 19))
    SERVER = srv
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    INSTANCE.write_text(json.dumps({"port": srv.server_address[1], "pid": os.getpid()}), encoding="utf-8")
    print("Efface-moi est ouvert sur %s\nPour l'arrêter : bouton « Quitter » dans la page, ou fermez cette fenêtre." % url,
          flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            INSTANCE.unlink()
        except OSError:
            pass


def auto(delay=0):
    if delay:
        time.sleep(delay)
    out = engine.run_cycle()
    line = json.dumps(out, ensure_ascii=False)
    store.DATA_DIR.mkdir(exist_ok=True)
    with open(store.DATA_DIR / "auto.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Demandes d'effacement RGPD automatisées.")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "auto"])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--delay", type=int, default=0, help="attendre N secondes avant le suivi (lancement au démarrage)")
    args = ap.parse_args()
    if args.command == "auto":
        auto(max(0, min(args.delay, 3600)))
    else:
        serve(args.port, not args.no_browser)
