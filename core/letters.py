"""Courriers RGPD. Textes adaptés des modèles du projet datarequests.org (domaine public, CC0)."""
from datetime import datetime

FRENCH_SPEAKING = {"fr", "be", "lu", "ch", "mc"}

ERASURE = {
    "fr": {
        "subject": "Demande d'effacement de mes données personnelles (article 17 du RGPD)",
        "body": """Madame, Monsieur,

Je vous demande par la présente l'effacement immédiat de toutes les données à caractère personnel me concernant, conformément à l'article 17 du Règlement général sur la protection des données (RGPD).

Si j'ai consenti au traitement de mes données, je retire ce consentement. Je m'oppose également à tout traitement de mes données, y compris le profilage et la prospection commerciale, conformément à l'article 21 du RGPD.

Si vous avez communiqué ces données à des tiers, vous devez leur transmettre ma demande d'effacement, conformément à l'article 19 du RGPD. Merci de m'indiquer qui sont ces destinataires.

Ma demande vise {scope}tous les services et sociétés dont vous êtes responsable du traitement au sens de l'article 4, point 7, du RGPD.
{pages}
Conformément à l'article 12, paragraphe 3, du RGPD, vous devez me confirmer l'effacement dans les meilleurs délais et au plus tard un mois après réception de cette demande. Si vous refusez d'y donner suite, vous devez m'en donner les raisons.

Informations permettant de m'identifier :
{identity}

Sans réponse dans ce délai, je saisirai la CNIL, autorité de contrôle compétente.

Cordialement,
{name}
""",
        "scope": "explicitement {runs}, ainsi que ",
        "pages": "Elle vise en particulier les contenus publiés aux adresses suivantes, que je vous demande de supprimer :",
    },
    "en": {
        "subject": "Request for erasure of my personal data (Article 17 GDPR)",
        "body": """To whom it may concern,

I hereby request the immediate erasure of all personal data concerning me, in accordance with Article 17 of the General Data Protection Regulation (GDPR).

If I have given consent to the processing of my personal data, I hereby withdraw it. I also object to any processing of my personal data, including profiling and direct marketing, in accordance with Article 21 GDPR.

If you have disclosed these data to third parties, you must communicate my erasure request to each recipient, as laid down in Article 19 GDPR. Please inform me about those recipients.

My request covers {scope}all services and companies for which you are the controller as defined by Article 4(7) GDPR.
{pages}
As laid down in Article 12(3) GDPR, you must confirm the erasure without undue delay and in any event within one month of receipt of this request. If you refuse to act on it, you must give me your reasons.

Information to identify me:
{identity}

If I receive no answer within that period, I will lodge a complaint with the French supervisory authority (CNIL).

Yours faithfully,
{name}
""",
        "scope": "explicitly {runs}, as well as ",
        "pages": "It concerns in particular the content published at the following addresses, which I ask you to remove:",
    },
}

REMINDER = {
    "fr": {
        "subject": "Relance — demande d'effacement du {date} (article 17 du RGPD)",
        "body": """Madame, Monsieur,

Le {date}, je vous ai adressé une demande d'effacement de mes données personnelles, conformément à l'article 17 du RGPD. Je n'ai pas reçu de réponse : le délai d'un mois fixé par l'article 12, paragraphe 3, du RGPD est dépassé.

Je vous demande donc de traiter cette demande et de m'en confirmer l'exécution sous 15 jours. Passé ce délai, je déposerai une plainte auprès de la CNIL.

Ma demande initiale est reproduite ci-dessous.

Cordialement,
{name}

-------- Demande du {date} --------
{original}""",
    },
    "en": {
        "subject": "Reminder — erasure request of {date} (Article 17 GDPR)",
        "body": """To whom it may concern,

On {date}, I sent you a request for erasure of my personal data under Article 17 GDPR. I have not received an answer: the one-month period set by Article 12(3) GDPR has expired.

I therefore ask you to process this request and confirm it within 15 days. After that, I will lodge a complaint with the French supervisory authority (CNIL).

My original request is reproduced below.

Yours faithfully,
{name}

-------- Request of {date} --------
{original}""",
    },
}

COMPLAINT = """Plainte à déposer sur https://www.cnil.fr/fr/plaintes (rubrique « Exercer ses droits »)

Je dépose une plainte conformément à l'article 77 du RGPD contre l'organisme suivant :
{company}{contact}

Le {sent}, je lui ai envoyé{channel} une demande d'effacement de mes données personnelles (article 17 du RGPD) et d'opposition à leur traitement (article 21). {reminder}À ce jour, {outcome}.

Je vous demande d'intervenir auprès de cet organisme pour qu'il donne suite à ma demande.

Je joins à cette plainte la copie de ma demande{and_reminder}. Vous pouvez me joindre à l'adresse {email}.

{name}
"""


def fr_date(iso_str):
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
              "octobre", "novembre", "décembre"]
    d = datetime.fromisoformat(iso_str)
    return "%s %s %d" % ("1er" if d.day == 1 else d.day, months[d.month - 1], d.year)


def en_date(iso_str):
    d = datetime.fromisoformat(iso_str)
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def language_for(company):
    countries = set(company.get("relevant-countries") or [])
    if company.get("custom") or company.get("community") or countries & FRENCH_SPEAKING:
        return "fr"
    # Beaucoup d'entreprises françaises sont classées « tous pays » : on regarde aussi l'adresse et le domaine.
    last_line = (company.get("address") or "").strip().splitlines()[-1:] or [""]
    if last_line[0].strip().lower() in ("france", "luxembourg", "monaco"):
        return "fr"
    for src in (company.get("email"), company.get("web")):
        host = (src or "").lower().split("@")[-1].split("//")[-1].split("/")[0]
        if host.endswith(".fr"):
            return "fr"
    return "en"


def identity_block(profile, company, lang):
    """N'inclut que le strict nécessaire : nom et e-mails ; l'adresse postale, la date de naissance et
    le téléphone seulement si le profil le demande ou si l'entreprise exige l'adresse."""
    fr = lang == "fr"
    sep = " : " if fr else ": "
    lines = ["%s%s%s" % ("Nom" if fr else "Name", sep, profile.get("name", ""))]
    emails = [profile.get("email", "")] + [e for e in profile.get("other_emails", []) if e]
    lines.append("%s%s%s" % ("E-mail" if fr else "Email", sep, ", ".join(e for e in emails if e)))
    required = {e.get("type") for e in company.get("required-elements") or []}
    if profile.get("address") and (profile.get("include_address") or "address" in required):
        lines.append("%s%s%s" % ("Adresse" if fr else "Address", sep, ", ".join(profile["address"].splitlines())))
    if profile.get("birthdate") and (profile.get("include_birthdate") or "birthdate" in required):
        lines.append("%s%s%s" % ("Date de naissance" if fr else "Date of birth", sep, profile["birthdate"]))
    if profile.get("phone") and profile.get("include_phone"):
        lines.append("%s%s%s" % ("Téléphone" if fr else "Phone", sep, profile["phone"]))
    return "\n".join(lines)


def erasure(profile, company, lang=None, urls=None):
    """urls : pages précises à faire supprimer (module Empreinte), citées dans le courrier."""
    lang = lang or language_for(company)
    t = ERASURE[lang]
    runs = [r for r in company.get("runs") or [] if r][:15]
    scope = t["scope"].format(runs=", ".join(runs)) if runs else ""
    pages = ""
    if urls:
        pages = "\n" + t["pages"] + "\n" + "\n".join("- " + u for u in urls) + "\n"
    body = t["body"].format(scope=scope, pages=pages, identity=identity_block(profile, company, lang),
                            name=profile.get("name", ""))
    return lang, t["subject"], body


DELISTING = """Je demande le déréférencement des pages ci-dessous : qu'elles n'apparaissent plus dans les résultats d'une recherche portant sur mon nom, « {name} ». Cette demande s'appuie sur l'article 17 du RGPD et sur l'arrêt de la Cour de justice de l'Union européenne du 13 mai 2014 (Google Spain, C-131/12).

Pages concernées :
{pages}

Motif : ces pages contiennent des données personnelles me concernant ({data}). Je ne suis pas une personne publique, ces informations ne présentent pas d'intérêt pour le public et leur diffusion porte atteinte à ma vie privée.

{name}
"""

DELISTING_COMPLAINT = """Plainte à déposer sur https://www.cnil.fr/fr/plaintes (rubrique « Internet », déréférencement)

Je dépose une plainte conformément à l'article 77 du RGPD contre le moteur de recherche auquel j'ai adressé, le {sent}, une demande de déréférencement restée sans réponse satisfaisante.

Recherche concernée : mon nom, « {name} ».
Page dont je demande le déréférencement :
{page}

Motif de ma demande : cette page contient des données personnelles me concernant. Je ne suis pas une personne publique et sa diffusion porte atteinte à ma vie privée.

Je vous demande d'intervenir auprès du moteur de recherche pour qu'il donne suite à ma demande. Vous pouvez me joindre à l'adresse {email}.

{name}
"""


def delisting(profile, urls):
    data = "nom" + (", adresse e-mail" if profile.get("email") else "") + (", téléphone" if profile.get("phone") else "")
    return DELISTING.format(name=profile.get("name", ""), pages="\n".join("- " + u for u in urls), data=data)


def delisting_complaint(profile, url, sent_at):
    return DELISTING_COMPLAINT.format(name=profile.get("name", ""), page=url, sent=fr_date(sent_at),
                                      email=profile.get("email", ""))


def reminder(profile, request):
    lang = request.get("lang") or "fr"
    t = REMINDER[lang]
    date = (fr_date if lang == "fr" else en_date)(request["sent_at"])
    return (t["subject"].format(date=date),
            t["body"].format(date=date, name=profile.get("name", ""), original=request["body"]))


def complaint(profile, request, company):
    contact = ""
    if company:
        if company.get("address"):
            contact += "\n" + company["address"]
        if company.get("web"):
            contact += "\n" + company["web"]
    if request.get("email"):
        contact += "\nAdresse utilisée pour ma demande : " + request["email"]
    reminded = request.get("reminded_at")
    refused = request.get("status") == "refused"
    return COMPLAINT.format(
        company=request["name"], contact=contact, sent=fr_date(request["sent_at"]),
        channel=" par e-mail" if request.get("email") else "",
        reminder=("Sans réponse dans le délai d'un mois, je l'ai relancé le %s. " % fr_date(reminded)) if reminded else "",
        outcome="cet organisme a refusé de donner suite à ma demande" if refused
        else "je n'ai reçu aucune réponse satisfaisante",
        and_reminder=" et de ma relance" if reminded else "",
        email=profile.get("email", ""), name=profile.get("name", ""))
