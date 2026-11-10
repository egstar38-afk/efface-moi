"""Relecture des adresses proposées par les utilisateurs. Lancé par GitHub Actions (.github/workflows/adresses.yml).

    python outils/adresse.py verifier   lit ISSUE_BODY, écrit un rapport en Markdown pour aider à décider
    python outils/adresse.py accepter   lit ISSUE_BODY et NUMERO, ajoute la fiche à communaute/entreprises.json

Le texte de la proposition est une donnée non fiable : il n'est jamais exécuté, seulement lu et filtré.
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import companies, contact  # noqa: E402
from core.discover import NO_REPLY  # noqa: E402

FILE = ROOT / "communaute" / "entreprises.json"
# Début du titre de chaque champ du formulaire (.github/ISSUE_TEMPLATE/adresse.yml) -> nom du champ.
FIELDS = (("nom de l'entreprise", "name"), ("adresse e-mail", "email"), ("site web", "web"),
          ("lien du formulaire", "webform"), ("résultat", "result"), ("où avez-vous trouvé", "source"))


def parse(body):
    out = {}
    parts = re.split(r"^###\s+(.+?)\s*$", body or "", flags=re.M)
    for label, value in zip(parts[1::2], parts[2::2]):
        key = next((k for start, k in FIELDS if label.strip().lower().startswith(start)), None)
        value = value.strip()
        if key:
            out[key] = "" if value in ("", "_No response_") else value.splitlines()[0].strip()
    for k in ("web", "webform"):
        if out.get(k) and not re.match(r"https?://", out[k], re.I):
            out[k] = "https://" + out[k]
    return out


def entry(body):
    raw = parse(body)
    clean = companies.clean_community([raw])
    return (clean[0] if clean else None), raw


def load():
    try:
        items = json.loads(FILE.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except (OSError, ValueError):
        return []


def code(text):
    """Affiche un texte reçu sans qu'il puisse être interprété comme du Markdown."""
    return "`%s`" % (str(text or "").replace("`", "'").replace("\n", " ")[:200] or " ")


def verifier(body):
    c, raw = entry(body)
    if not c:
        return ("**Proposition illisible.** Il faut au moins un nom d'entreprise et une adresse e-mail valide.\n\n"
                "Reçu : nom %s, e-mail %s.\n\nVous pouvez corriger la proposition, ou la fermer."
                % (code(raw.get("name")), code(raw.get("email"))))
    ok, warn = "✅", "⚠️"
    checks = []
    if any(str(i.get("email", "")).lower() == c["email"] for i in load()):
        checks.append((warn, "Cette adresse est **déjà** dans les adresses partagées."))
    mail_dom, site_dom = companies.base_domain(c["email"]), companies.base_domain(c["web"])
    if not c["web"]:
        checks.append((warn, "Aucun site web indiqué : impossible de recouper."))
    elif mail_dom == site_dom:
        checks.append((ok, "L'adresse e-mail est sur le même domaine que le site (%s)." % code(site_dom)))
    else:
        checks.append((warn, "L'adresse e-mail (%s) n'est pas sur le domaine du site (%s). Fréquent pour un groupe, "
                             "mais à regarder de près." % (code(mail_dom), code(site_dom))))
    if mail_dom in companies.FREEMAIL:
        checks.append((warn, "Adresse chez un fournisseur grand public (%s) : inhabituel pour une entreprise." % code(mail_dom)))
    if NO_REPLY.search(c["email"].split("@")[0]):
        checks.append((warn, "L'adresse ressemble à une adresse d'envoi automatique, que personne ne lit."))
    if c["web"]:
        try:
            found = contact.find(c["web"])
        except ValueError as e:
            found = {"pages": 0, "candidates": [], "error": str(e)}
        hit = next((x for x in found["candidates"] if x["email"] == c["email"]), None)
        if hit:
            checks.append((ok, "L'adresse **figure sur le site** de l'entreprise : %s" % hit["source"]))
        elif not found["pages"]:
            checks.append((warn, "Le site n'a pas pu être lu par un robot : ouvrez sa politique de confidentialité "
                                 "pour vérifier l'adresse à la main."))
        else:
            seen = ", ".join(code(x["email"]) for x in found["candidates"][:4]) or "aucune"
            checks.append((warn, "L'adresse n'a **pas été trouvée** sur les %d pages lues du site. Adresses vues : %s."
                           % (found["pages"], seen)))
    lines = ["Vérification automatique de la proposition :", "",
             "| | |", "|---|---|",
             "| Entreprise | %s |" % code(c["name"]), "| Adresse | %s |" % code(c["email"]),
             "| Site | %s |" % code(c["web"]), "| Formulaire | %s |" % code(c["webform"]),
             "| Résultat déclaré | %s |" % code(raw.get("result")), "| Source indiquée | %s |" % code(raw.get("source")), ""]
    lines += ["- %s %s" % x for x in checks]
    lines += ["", "**Pour accepter :** ajoutez l'étiquette `acceptee` (colonne de droite, « Labels »). "
                  "L'adresse est alors ajoutée à la base et la proposition se ferme toute seule.",
              "**Pour refuser :** fermez simplement la proposition."]
    return "\n".join(lines)


def accepter(body, numero):
    """Renvoie (code de sortie, message). 0 : ajoutée ou déjà présente ; 1 : proposition illisible."""
    c, _ = entry(body)
    if not c:
        return 1, "Proposition illisible : rien n'a été ajouté. Corrigez-la, puis retirez et remettez l'étiquette `acceptee`."
    items = load()
    if any(str(i.get("email", "")).lower() == c["email"] for i in items):
        return 0, "Cette adresse était déjà dans les adresses partagées : rien à ajouter. Merci !"
    items.append({"name": c["name"], "email": c["email"], "web": c["web"], "webform": c["webform"],
                  "added": date.today().isoformat(), "issue": numero})
    items.sort(key=lambda i: str(i.get("name", "")).lower())
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0, ("Merci ! L'adresse de **%s** est ajoutée aux adresses partagées. Les utilisateurs la recevront à leur "
               "prochaine mise à jour de la base (onglet Entreprises, « Mettre à jour »)." % c["name"].replace("*", ""))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    body = os.environ.get("ISSUE_BODY", "")
    if action == "verifier":
        print(verifier(body))
    elif action == "accepter":
        status, message = accepter(body, int(os.environ.get("NUMERO") or 0))
        print(message)
        sys.exit(status)
    else:
        sys.exit(__doc__)
