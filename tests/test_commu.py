"""Essai du partage d'adresses entre utilisateurs. Aucun accès à Internet, aucune donnée réelle.

    python tests/test_commu.py
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

tmp = tempfile.mkdtemp(prefix="efface-test-")
os.environ["EFFACE_DATA"] = tmp
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import companies, contact, engine, letters, store  # noqa: E402

spec = importlib.util.spec_from_file_location("adresse", ROOT / "outils" / "adresse.py")
adresse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adresse)
ok = 0


def check(label, cond):
    global ok
    print(("  ok     " if cond else "  ECHEC  ") + label)
    if not cond:
        sys.exit(1)
    ok += 1


print("Lecture du fichier partagé")
received = [
    {"name": "Boutique Durand", "email": "DPO@Boutique-Durand.example", "web": "https://www.boutique-durand.example"},
    {"name": "Boutique Durand", "email": "autre@boutique-durand.example"},                       # doublon
    {"name": "<script>alert(1)</script>", "email": "pas-une-adresse"},                          # e-mail invalide
    {"name": "Piège", "email": "x@piege.example", "web": "javascript:alert(1)", "webform": "file:///etc/passwd"},
    {"name": "Déjà Connue", "email": "rgpd@connue.example"},
    "n'importe quoi", {"email": "sans-nom@x.example"},
]
clean = companies.clean_community(received)
check("fiches invalides et doublons écartés", [c["name"] for c in clean] == ["Boutique Durand", "Piège", "Déjà Connue"])
check("adresse normalisée, liens dangereux retirés", clean[0]["email"] == "dpo@boutique-durand.example"
      and clean[1]["web"] == "" and clean[1]["webform"] == "")
Path(tmp, "companies.json").write_text(json.dumps({"updated": store.iso(), "community": received, "companies": [
    {"slug": "connue", "name": "Connue SA", "email": "dpo@connue.example", "categories": ["ads"]}]}), encoding="utf-8")
names = [c["name"] for c in companies.all_companies()]
check("ajoutées à la suite de la base, sans l'entreprise que la base connaît déjà",
      names == ["Connue SA", "Boutique Durand", "Piège"])
check("filtre dédié et compteur", companies.search(preset="community")["total"] == 2 and companies.info()["community"] == 2)
check("courrier en français pour une adresse partagée", letters.language_for(companies.get("commu-boutique-durand")) == "fr")

print("Proposer une adresse")
store.set("profile", {"name": "Camille Martin", "email": "camille@example.org"})
slug = companies.add_custom("Garage Lemoine", "contact@garage-lemoine.example", "https://garage-lemoine.example")
rid = engine.create_requests([slug])["created"][0]
check("rien à partager tant que l'entreprise n'a pas répondu", engine.shareable() == [])
store.update_request(rid, status="sent", sent_at=store.iso())
check("un simple envoi ne suffit pas", engine.shareable() == [])
engine.set_status(rid, "done")
share = engine.shareable()
check("proposée une fois la suppression confirmée", len(share) == 1 and share[0]["outcome"] == "Suppression confirmée")
q = {k: v[0] for k, v in parse_qs(urlsplit(share[0]["url"]).query).items()}
check("le lien ne contient que les coordonnées de l'entreprise",
      q == {"template": "adresse.yml", "title": "Adresse : Garage Lemoine", "nom": "Garage Lemoine",
            "email": "contact@garage-lemoine.example", "site": "https://garage-lemoine.example",
            "resultat": "Suppression confirmée"} and "camille" not in share[0]["url"].lower())
engine.mark_shared(slug)
check("plus proposée une fois partagée ou écartée", engine.shareable() == [])
slug2 = companies.add_custom("Boutique Durand bis", "dpo@boutique-durand.example")
engine.set_status(engine.create_requests([slug2])["created"][0], "done")
check("pas proposée si l'adresse est déjà partagée", engine.shareable() == [])

print("Relecture côté GitHub")
BODY = """### Nom de l'entreprise

Garage Lemoine

### Adresse e-mail de contact

Contact@Garage-Lemoine.example

### Site web de l'entreprise

garage-lemoine.example

### Lien du formulaire de demande

_No response_

### Résultat obtenu avec cette adresse

Suppression confirmée

### Où avez-vous trouvé cette adresse ?

_No response_
"""
p = adresse.parse(BODY)
check("formulaire lu", p == {"name": "Garage Lemoine", "email": "Contact@Garage-Lemoine.example",
                             "web": "https://garage-lemoine.example", "webform": "", "result": "Suppression confirmée", "source": ""})
adresse.FILE = Path(tmp, "communaute", "entreprises.json")
contact.find = lambda web: {"site": "garage-lemoine.example", "pages": 3, "candidates": [
    {"email": "contact@garage-lemoine.example", "source": "https://garage-lemoine.example/mentions-legales"}]}
report = adresse.verifier(BODY)
check("rapport : même domaine et adresse retrouvée sur le site", "même domaine" in report and "figure sur le site" in report
      and "acceptee" in report)
contact.find = lambda web: {"site": "x", "pages": 4, "candidates": [{"email": "dpo@autre.example", "source": "u"}]}
report = adresse.verifier(BODY.replace("Contact@Garage-Lemoine.example", "no-reply@gmail.com"))
check("rapport : alertes (autre domaine, adresse grand public, automatique, introuvable sur le site)",
      all(t in report for t in ("n'est pas sur le domaine", "grand public", "envoi automatique", "pas été trouvée")))
bad = adresse.verifier("### Nom de l'entreprise\n\n`x` **gras**\n\n### Adresse e-mail de contact\n\npas une adresse\n")
check("proposition illisible signalée, texte reçu neutralisé", "illisible" in bad and "`'x' **gras**`" in bad)
code, msg = adresse.accepter(BODY, 12)
saved = json.loads(adresse.FILE.read_text(encoding="utf-8"))
check("acceptée : fiche écrite", code == 0 and len(saved) == 1 and saved[0]["email"] == "contact@garage-lemoine.example"
      and saved[0]["issue"] == 12 and saved[0]["web"] == "https://garage-lemoine.example")
check("acceptée deux fois : pas de doublon", adresse.accepter(BODY, 13)[0] == 0 and len(json.loads(adresse.FILE.read_text(encoding="utf-8"))) == 1)
check("illisible : refusée, fichier intact", adresse.accepter("rien", 14)[0] == 1 and len(json.loads(adresse.FILE.read_text(encoding="utf-8"))) == 1)
check("le fichier produit est relu tel quel par l'application", [c["slug"] for c in companies.clean_community(saved)] == ["commu-garage-lemoine"])
real = json.loads((ROOT / "communaute" / "entreprises.json").read_text(encoding="utf-8"))
check("le fichier du dépôt est valide", isinstance(real, list) and len(companies.clean_community(real)) == len(real))
print("\n%d vérifications réussies." % ok)
