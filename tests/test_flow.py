"""Essai de bout en bout sans vrai e-mail : faux serveur SMTP local, dossier de données temporaire.

    python tests/test_flow.py
"""
import json
import os
import socketserver
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="efface-test-")
os.environ["EFFACE_DATA"] = tmp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import companies, engine, letters, mailer, store  # noqa: E402

RECEIVED = []


class FakeSMTP(socketserver.StreamRequestHandler):
    def handle(self):
        def w(s):
            self.wfile.write((s + "\r\n").encode())
        w("220 test")
        data, in_data = [], False
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if in_data:
                if line == ".":
                    RECEIVED.append("\n".join(data))
                    data, in_data = [], False
                    w("250 ok")
                else:
                    data.append(line)
                continue
            cmd = line[:4].upper()
            if cmd == "EHLO":
                w("250-test")
                w("250 8BITMIME")
            elif cmd == "DATA":
                in_data = True
                w("354 go")
            elif cmd == "QUIT":
                w("221 bye")
                return
            else:
                w("250 ok")


srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSMTP)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ok = 0


def check(label, cond):
    global ok
    print(("  ok     " if cond else "  ECHEC  ") + label)
    if not cond:
        sys.exit(1)
    ok += 1


def by_slug():
    return {r["slug"]: engine.detail(r["id"]) for r in engine.list_requests()}


Path(tmp, "companies.json").write_text(json.dumps({"updated": store.iso(), "companies": [
    {"slug": "courtier-fr", "name": "Courtier FR", "email": "dpo@courtier.example", "web": "https://www.courtier.example",
     "categories": ["addresses"], "relevant-countries": ["fr"], "runs": ["FichierPlus", "AdressMax"],
     "required-elements": [{"type": "name"}, {"type": "address"}]},
    {"slug": "adtech", "name": "AdTech Inc", "email": "privacy@adtech.example", "categories": ["ads"],
     "relevant-countries": ["all"]},
    {"slug": "form-only", "name": "Formulaire SA", "webform": "https://form.example/rgpd", "categories": ["ads"],
     "relevant-countries": ["fr"]},
] + [{"slug": "x%d" % i, "name": "X%d" % i, "email": "a@x%d.example" % i, "categories": ["commerce"]}
     for i in range(5)]}), encoding="utf-8")

print("Base et recherche")
check("filtres courtiers", companies.search(preset="brokers_fr")["total"] == 3 and companies.search(preset="fr")["total"] == 8)
check("recherche par marque exploitée", companies.search(q="adressmax")["companies"][0]["slug"] == "courtier-fr")
check("domaine de base", companies.base_domain("news.mail.exemple.co.uk") == "exemple.co.uk"
      and companies.base_domain("https://www.a.fr/x") == "a.fr")

print("Courriers")
store.set("profile", {"name": "Camille Martin", "email": "camille@example.org", "other_emails": ["cm@example.net"],
                      "address": "1 rue des Lilas\n75000 Paris", "birthdate": "01/02/1990", "phone": "0600000000"})
lang, subj, body = letters.erasure(engine.profile(), companies.get("courtier-fr"))
check("français, adresse car exigée, marques citées",
      lang == "fr" and "1 rue des Lilas, 75000 Paris" in body and "FichierPlus, AdressMax" in body)
lang, subj, body = letters.erasure(engine.profile(), companies.get("adtech"))
check("anglais, sans adresse ni naissance ni téléphone",
      lang == "en" and "Lilas" not in body and "1990" not in body and "0600" not in body and "cm@example.net" in body)

print("Création")
res = engine.create_requests(["courtier-fr", "adtech", "form-only", "inconnu"], recurring=True)
check("3 créées, 1 ignorée", len(res["created"]) == 3 and len(res["skipped"]) == 1)
check("pas de doublon", engine.create_requests(["adtech"])["created"] == [])
st = by_slug()
check("sans e-mail : formulaire", st["form-only"]["status"] == "manual" and st["adtech"]["status"] == "draft")

print("Mode test")
mailer.save_settings({"email": "camille@example.org", "smtp_host": "127.0.0.1", "smtp_port": srv.server_address[1],
                      "smtp_security": "none", "delay_seconds": 0, "daily_cap": 3})
check("mode test actif par défaut", mailer.settings()["test_mode"] is True)
engine.queue([r["id"] for r in engine.list_requests()])
out = engine.send_queued()
check("fichiers .eml créés, rien d'envoyé", out["test"] == 2 and out["sent"] == 0 and not RECEIVED
      and len(list(Path(tmp, "outbox").glob("*.eml"))) == 2)
check("restent en brouillon", all(r["status"] in ("draft", "manual") for r in engine.list_requests()))

print("Envoi réel (faux serveur local)")
mailer.save_settings({"test_mode": False})
engine.queue([r["id"] for r in engine.list_requests()])
out = engine.send_queued()
check("2 envoyées", out["sent"] == 2 and len(RECEIVED) == 2)
check("contenu reçu par le serveur", any("dpo@courtier.example" in m and "Message-ID" in m for m in RECEIVED))
st = by_slug()
check("statut envoyé, échéance à 31 jours", st["adtech"]["status"] == "sent"
      and st["adtech"]["due_at"][:10] == store.iso(store.now() + timedelta(days=31))[:10])
try:
    mailer._smtp(dict(mailer.settings(), smtp_host="smtp.invalid.example", smtp_security="none"))
    refused = False
except RuntimeError:
    refused = True
check("connexion non chiffrée impossible hors de cette machine", refused)

print("Limite quotidienne")
engine.queue(engine.create_requests(["x0", "x1", "x2"])["created"])
out = engine.send_queued()
check("1 seule part (limite 3/jour), 2 restent en file", out["sent"] == 1 and out["left"] == 2 and engine.sent_today() == 3)
engine.delete([r["id"] for r in engine.list_requests() if r["slug"].startswith("x")])

print("Lecture des réponses")
later = store.iso(store.now() + timedelta(hours=2))


def H(**k):
    h = {"from": "", "from_name": "", "subject": "s", "date": later, "message_id": "<m>", "refs": [],
         "auto": False, "unsubscribe": False}
    h.update(k)
    return h


a, c = st["adtech"], st["courtier-fr"]
waiting = store.rows("SELECT * FROM requests WHERE status='sent'")
m = engine.match_replies(waiting, [
    H(**{"from": "newsletter@courtier.example", "unsubscribe": True}),
    H(**{"from": "noreply@helpdesk.example", "refs": [a["message_id"]], "auto": True}),
    H(**{"from": "quelquun@gmail.com"}),
    H(**{"from": "dpo@courtier.example", "date": "2000-01-01T00:00:00"}),
    H(**{"from": "service@support.courtier.example", "subject": "Votre demande"}),
])
check("newsletter, adresse perso et vieux message ignorés ; accusé et réponse reconnus",
      sorted((r, k) for r, k, _ in m) == sorted([(a["id"], "ack"), (c["id"], "reply")]))
engine.apply_matches(m)
st = by_slug()
check("un accusé de réception n'arrête pas le délai", st["adtech"]["status"] == "sent" and st["adtech"]["ack_at"])
check("réponse : à lire", st["courtier-fr"]["status"] == "replied" and st["courtier-fr"]["reply_subject"] == "Votre demande")
engine.set_status(c["id"], "waiting")
again = engine.match_replies(store.rows("SELECT * FROM requests WHERE id=?", (c["id"],)),
                             [H(**{"from": "service@support.courtier.example"})])
check("« pas une réponse » : retour en attente, le même message n'est pas recompté",
      engine.detail(c["id"])["status"] == "sent" and again == [])
raw = (b"From: =?utf-8?q?Service_Donn=C3=A9es?= <DPO@Courtier.example>\r\n"
       b"Subject: =?utf-8?b?UsOpcG9uc2UgYXV0b21hdGlxdWU=?=\r\nDate: Tue, 10 Nov 2026 10:00:00 +0100\r\n"
       b"In-Reply-To: <abc@x>\r\nReferences: <abc@x> <def@y>\r\n\r\n")
p = mailer.parse_headers(raw)
check("décodage des en-têtes", p["from"] == "dpo@courtier.example" and p["from_name"] == "Service Données"
      and p["auto"] and "<def@y>" in p["refs"] and p["date"].startswith("2026-11-10T"))
bounce = engine.match_replies(store.rows("SELECT * FROM requests WHERE status='sent'"),
                              [H(**{"from": "mailer-daemon@googlemail.com", "refs": [a["message_id"]],
                                    "date": store.iso(store.now() + timedelta(hours=3))})])
check("message non distribué reconnu", [(r, k) for r, k, _ in bounce] == [(a["id"], "bounce")])

engine.apply_matches(engine.match_replies(store.rows("SELECT * FROM requests WHERE id=?", (c["id"],)),
                                          [H(**{"from": "dpo@courtier.example", "subject": "Bien reçu",
                                                "date": store.iso(store.now() + timedelta(hours=4))})]))
due_before = engine.detail(c["id"])["due_at"]
engine.set_status(c["id"], "processing")
d = engine.detail(c["id"])
check("« en cours de traitement » : retour en attente, échéance inchangée, mention gardée",
      d["status"] == "sent" and d["due_at"] == due_before and d["processing_at"]
      and [r for r in engine.list_requests() if r["id"] == c["id"]][0]["processing_at"])

print("Relance, plainte, renouvellement")
past = store.iso(store.now() - timedelta(days=1))
store.update_request(a["id"], due_at=past, sent_at=store.iso(store.now() - timedelta(days=32)))
n0 = len(RECEIVED)
check("relance retenue par la limite du jour", engine.send_reminders()["reminders"] == 0)
mailer.save_settings({"daily_cap": 40})
out = engine.send_reminders()
r = engine.detail(a["id"])
check("relance envoyée en réponse au premier message", out["reminders"] == 1 and r["status"] == "reminded"
      and len(RECEIVED) == n0 + 1 and "In-Reply-To: " + a["message_id"] in RECEIVED[-1] and "Reminder" in RECEIVED[-1])
check("pas de double relance", engine.send_reminders()["reminders"] == 0)
store.update_request(a["id"], due2_at=past)
check("plainte CNIL préparée", engine.escalate() == 1 and "article 77" in engine.detail(a["id"])["complaint"]
      and "relancé le" in engine.detail(a["id"])["complaint"])
engine.set_status(c["id"], "done")
check("pas de renouvellement avant 6 mois", engine.renew() == 0)
store.update_request(c["id"], closed_at=store.iso(store.now() - timedelta(days=200)))
check("renouvellement après 6 mois, une seule fois", engine.renew() == 1 and engine.renew() == 0)
f = [r for r in engine.list_requests() if r["slug"] == "form-only"][0]
engine.set_status(f["id"], "sent_manually")
store.update_request(f["id"], due_at=past)
check("formulaire resté sans réponse : plainte", engine.escalate() == 1 and engine.detail(f["id"])["status"] == "complaint_due")
cyc = engine.run_cycle()
check("cycle complet : envoie le renouvellement en file", cyc["sending"]["sent"] == 1 and cyc["reminders"] == 0)

print("Détection : adresse de l'expéditeur")
from core import discover  # noqa: E402
senders = discover.summarize_senders([
    H(**{"from": "no-reply@news.boutique.example", "from_name": "Boutique"}),
    H(**{"from": "no-reply@news.boutique.example", "from_name": "Boutique"}),
    H(**{"from": "service.client@boutique.example", "from_name": "Boutique SAV"}),
    H(**{"from": "newsletter@promo.example"}),
    H(**{"from": "ami@gmail.com"}),
    H(**{"from": "moi@example.org"}),
], own="example.org")
check("regroupement par domaine, adresses perso et la mienne ignorées", sorted(senders) == ["boutique.example", "promo.example"])
check("adresse lue par quelqu'un préférée à la plus fréquente",
      discover.best_address(senders["boutique.example"]["addresses"]) == ("service.client@boutique.example", False))
check("adresse automatique signalée quand il n'y a qu'elle",
      discover.best_address(senders["promo.example"]["addresses"]) == ("newsletter@promo.example", True))

print("Base créée par une version plus ancienne")
with store.db() as con:
    con.execute("ALTER TABLE requests DROP COLUMN processing_at")
store._migrated = False
check("colonne manquante ajoutée sans perdre les demandes", len(engine.list_requests()) >= 3
      and all("processing_at" in r for r in engine.list_requests()))

print("Mot de passe")
store.set_password("abcd efgh")
check("chiffré sur disque, relisible", store.get_password() == "abcd efgh" and "abcd" not in store.get("mail_password"))
print("\n%d vérifications réussies." % ok)
