"""Essai du relevé des réponses : rattachement, lecture du texte, proposition de classement.
La boîte mail est simulée : aucun accès à Internet, aucun message réel.

    python tests/test_reponses.py
"""
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="efface-test-")
os.environ["EFFACE_DATA"] = tmp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import companies, engine, mailer, replies, store  # noqa: E402

ok = 0


def check(label, cond):
    global ok
    print(("  ok     " if cond else "  ECHEC  ") + label)
    if not cond:
        sys.exit(1)
    ok += 1


NAMES = ["Avast Software", "Qonto", "DataCorp GmbH", "Pixel Media", "Closed Co", "Zeta Ads", "Gamma Tech",
         "Delta Group", "Omega Data", "Legacy One", "Legacy Two"]
Path(tmp, "companies.json").write_text(json.dumps({"updated": store.iso(), "companies": [
    {"slug": n.split()[0].lower(), "name": n, "email": "privacy@%s.example" % n.split()[0].lower(), "categories": ["ads"]}
    for n in NAMES]}), encoding="utf-8")
store.set("profile", {"name": "Camille Martin", "email": "camille@example.org"})
mailer.save_settings({"email": "camille@example.org"})
sent = store.iso(store.now() - timedelta(hours=5))
ID = {}
for rid in engine.create_requests([n.split()[0].lower() for n in NAMES])["created"]:
    r = engine.detail(rid)
    ID[r["slug"]] = rid
    store.update_request(rid, status="sent", sent_at=sent, message_id="<m%d@example.org>" % rid,
                         due_at=store.iso(store.now() + timedelta(days=30)))
engine.set_status(ID["closed"], "done")


def at(hours):
    return store.iso(store.now() - timedelta(hours=5) + timedelta(hours=hours))


def H(uid, sender, subject, date, name="", refs=(), auto=False, unsub=False, folder="INBOX"):
    return {"from": sender, "from_name": name, "subject": subject, "date": date, "message_id": "<%s@recu>" % uid,
            "refs": list(refs), "auto": auto, "unsubscribe": unsub, "folder": folder, "uid": str(uid)}


SUBJ = "Re: Request for erasure of my personal data (Article 17 GDPR)"
INBOX = [
    H(1, "privacy@gendigital.example", SUBJ, at(1), "Gen Privacy", refs=["<m%d@example.org>" % ID["avast"]]),
    H(2, "dpo@qonto.example", "RE: Demande d'effacement de mes données personnelles", at(1)),
    H(3, "support@helpdesk.example", "Ihre Anfrage", at(1), "DataCorp Support", auto=True),
    H(4, "noreply@tickets.example", "[Ticket 4411] Request for erasure [EM-%d]" % ID["pixel"], at(1), auto=True),
    H(5, "privacy@unknown-vendor.example", SUBJ, at(2), "Vendor Privacy"),
    H(6, "agent@crm-desk.example", "Your privacy request", at(2), "CRM Desk"),
    H(7, "news@qonto.example", "Nos nouveautés de novembre", at(3), unsub=True),
    H(8, "privacy@closed.example", SUBJ, at(2)),
    H(9, "legal@gamma.example", SUBJ, at(2), folder="[Gmail]/Spam"),
    H(10, "camille@example.org", "Note pour moi : RGPD", at(2)),
    H(11, "mailer-daemon@googlemail.example", "Delivery Status Notification (Failure)", at(1),
      refs=["<m%d@example.org>" % ID["delta"]]),
    H(12, "ami@gmail.com", "Dîner samedi ?", at(2)),
]
TEXTS = {
    1: "Hello,\n\nWe have received your request and will respond within 30 days.\n\nOn Tue, Camille wrote:\n> I hereby request the erasure",
    2: "Bonjour,\n\nNous vous confirmons que vos données ont bien été supprimées de nos systèmes.\n\nCordialement",
    3: "Guten Tag,\n\num Ihre Anfrage zu bearbeiten, bitte bestätigen Sie Ihre Identität über den folgenden Link.",
    4: "Your request has been received. A ticket has been created and we will get back to you.",
    5: "Hi,\n\nWe could not find any data associated with your email address in our systems.\n\nRegards",
    6: "Hello,\n\nPlease complete our web form to submit your request.\n\n-----Original Message-----\nFrom: Camille\nTo: privacy@zeta.example\nI hereby request",
    8: "We confirm your data has been deleted.",
    9: "Dear Camille, thanks for reaching out. We are looking into your request.",
}
READ = []
mailer.fetch_headers = lambda since=None, limit=None, progress=None, junk=False: [dict(h) for h in INBOX]


def fake_texts(items, progress=None):
    READ.extend(items)
    return {(f, u): TEXTS[int(u)] for f, u in items if int(u) in TEXTS}


mailer.fetch_texts = fake_texts
store.set("replies_reread", "fait")       # pas de relecture d'anciennes versions dans ce premier scénario

print("Comprendre une réponse")
check("message d'origine cité retiré de l'extrait", replies.clean(TEXTS[1]) == "Hello,\n\nWe have received your request and will respond within 30 days."
      and "privacy@zeta.example" not in replies.clean(TEXTS[6]))
cases = [("Your personal data has been erased from all our systems.", "done"),
         ("Nous avons bien procédé à l'effacement de vos données.", "done"),
         ("We do not hold any personal data relating to you.", "done"),
         ("Wir haben Ihre Daten gelöscht.", "done"),
         ("To verify your identity, please send us a copy of your ID.", "needs_action"),
         ("Merci de nous préciser l'adresse e-mail concernée.", "needs_action"),
         ("We are unable to comply with your request as we are required to retain these records.", "refused"),
         ("Thank you for contacting us. We received your request; it will be deleted within 30 days.", "processing"),
         ("Bonjour, nous accusons réception de votre demande, qui sera traitée sous 30 jours.", "processing"),
         ("Bonjour Camille, tu viens dîner samedi ?", "")]
check("classement de dix réponses types (français, anglais, allemand)", all(replies.classify(t)[1] == a for t, a in cases))
check("nom d'entreprise réduit à ce qui l'identifie", replies.name_tokens("DataCorp GmbH") == {"datacorp"}
      and replies.name_tokens("Group Media Services") == set() and replies.name_tokens("AVAST Software s.r.o") == {"avast"})
raw = (b"From: a@b.example\r\nMIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=XX\r\n\r\n--XX\r\n"
       b"Content-Type: text/html; charset=utf-8\r\n\r\n<html><style>p{}</style><p>Bonjour,</p><p>Donn&eacute;es supprim&eacute;es.</p></html>\r\n--XX--\r\n")
check("texte extrait d'un message HTML", "Données supprimées." in mailer.message_text(raw) and "p{}" not in mailer.message_text(raw))
cut = b"Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\nQm9uam91ciwgdm9zIGRvbm7DqWVzIG9udCDDqXTDqSBzdXBwcmltw6llcy4=\r\n"
check("texte lu en base64, message tronqué toléré", "supprimées" in mailer.message_text(cut) and mailer.message_text(cut[:-30]) is not None)
sub, body = engine.with_reference({"id": 42, "subject": "Request for erasure", "body": "Texte.\n", "lang": "en"})
check("référence ajoutée à l'envoi, une seule fois", sub == "Request for erasure [EM-42]" and body.endswith("Reference of my request : EM-42\n")
      and engine.with_reference({"id": 42, "subject": sub, "body": body, "lang": "en"}) == (sub, body))

print("Relevé de la boîte")
out = engine.check_replies()
st = {slug: engine.detail(rid) for slug, rid in ID.items()}
check("réponse directe depuis un autre domaine : rattachée, classée « en cours »",
      st["avast"]["status"] == "replied" and st["avast"]["suggestion"] == "processing" and "réponse directe" in st["avast"]["reply_how"])
check("même domaine : suppression confirmée proposée, extrait gardé",
      st["qonto"]["suggestion"] == "done" and "bien été supprimées" in st["qonto"]["reply_excerpt"])
check("nom de l'entreprise chez l'expéditeur ; message automatique qui réclame une action : signalé",
      st["datacorp"]["status"] == "replied" and st["datacorp"]["suggestion"] == "needs_action")
check("référence EM dans l'objet ; simple accusé de réception automatique : noté, rien à valider",
      st["pixel"]["status"] == "sent" and st["pixel"]["ack_at"] and st["pixel"]["suggestion"] is None)
check("rattachée grâce au texte (adresse contactée citée), classée d'après la réponse seule",
      st["zeta"]["status"] == "replied" and st["zeta"]["suggestion"] == "needs_action" and "adresse contactée est citée" in st["zeta"]["reply_how"])
check("réponse rangée dans les indésirables : retrouvée et signalée comme telle",
      st["gamma"]["status"] == "replied" and "indésirables" in st["gamma"]["reply_how"])
check("message non distribué reconnu", st["delta"]["status"] == "bounced")
check("demande déjà classée, lettre d'information, courrier personnel, message de soi : ignorés",
      st["closed"]["status"] == "done" and st["omega"]["status"] == "sent" and out["reply"] == 5 and out["ack"] == 1 and out["bounce"] == 1)
orphans = store.get("orphan_replies")
check("réponse impossible à rattacher : mise de côté avec extrait et proposition",
      len(orphans) == 1 and orphans[0]["from"] == "privacy@unknown-vendor.example" and orphans[0]["action"] == "done"
      and "could not find" in orphans[0]["excerpt"])
check("texte lu seulement pour les réponses et les messages ressemblants", sorted(int(u) for _, u in READ) == [1, 2, 3, 4, 5, 6, 9])
READ.clear()
again = engine.check_replies()
check("second relevé : rien de nouveau, rien de relu", again["reply"] == 0 and again["ack"] == 0 and READ == []
      and len(store.get("orphan_replies")) == 1)

print("Valider")
check("validation d'une proposition", engine.accept([ID["qonto"]]) == 1 and engine.detail(ID["qonto"])["status"] == "done"
      and engine.detail(ID["qonto"])["suggestion"] is None)
due = engine.detail(ID["avast"])["due_at"]
n = engine.accept(only="processing")
a = engine.detail(ID["avast"])
check("validation groupée des « en cours de traitement » seulement : retour en attente, échéance inchangée",
      n == 2 and a["status"] == "sent" and a["due_at"] == due and a["processing_at"] and engine.detail(ID["datacorp"])["status"] == "replied")
engine.set_status(ID["datacorp"], "refused")
check("l'utilisateur peut classer autrement : la proposition disparaît", engine.detail(ID["datacorp"])["suggestion"] is None)

print("Rattacher à la main")
engine.attach_orphan(orphans[0]["key"], ID["omega"])
o = engine.detail(ID["omega"])
check("message rattaché, proposition conservée", o["status"] == "replied" and o["suggestion"] == "done" and store.get("orphan_replies") == []
      and "Rattachée par vous" in o["reply_how"])
engine.accept([ID["omega"]])
engine.set_status(ID["omega"], "waiting")
INBOX.append(H(13, "dpo@unknown-vendor.example", "Suite de votre dossier", at(4)))
TEXTS[13] = "We confirm that your data has now been deleted."
engine.check_replies()
check("l'expéditeur est retenu : son message suivant est rattaché tout seul",
      engine.detail(ID["omega"])["status"] == "replied" and "déjà rattaché" in engine.detail(ID["omega"])["reply_how"])
INBOX.append(H(14, "privacy@elsewhere.example", SUBJ, at(4)))
TEXTS[14] = "Thanks."
engine.check_replies()
key = store.get("orphan_replies")[0]["key"]
engine.dismiss_orphan(key)
engine.check_replies()
check("message écarté : il ne revient pas", store.get("orphan_replies") == [])

print("Messages relevés par une version précédente")
old = at(1)
INBOX[:] = [H(20, "privacy@legacy.example", SUBJ, old), H(21, "privacy@avast.example", "Re: your request", old)]
TEXTS[20] = "Your data has been deleted."
TEXTS[21] = "Your data has been deleted."
store.update_request(ID["legacy"], status="replied", reply_at=old, reply_from="privacy@legacy.example", reply_subject=SUBJ)
store.update_request(ID["avast"], reply_at=old)       # classée « en cours » par l'utilisateur plus haut
store.set("replies_reread", None)
engine.check_replies()
check("ancienne réponse sans extrait : relue une fois, proposition ajoutée",
      engine.detail(ID["legacy"])["suggestion"] == "done" and engine.detail(ID["legacy"])["reply_excerpt"] == "Your data has been deleted.")
check("demande déjà classée par l'utilisateur : laissée telle quelle", engine.detail(ID["avast"])["status"] == "sent")
print("\n%d vérifications réussies." % ok)
