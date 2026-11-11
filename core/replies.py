"""Comprendre une réponse : à quelle demande elle se rapporte, et ce qu'elle dit.

Tout ici est fait de fonctions pures (pas de réseau, pas de base) : elles reçoivent des en-têtes et du texte,
et renvoient un rattachement et une proposition. La proposition n'est jamais appliquée seule : l'utilisateur valide.
"""
import re

from . import companies

REF = re.compile(r"\bEM-(\d{1,7})\b")

# Mots trop courants pour reconnaître une entreprise dans le nom d'un expéditeur.
GENERIC = {"gmbh", "inc", "ltd", "llc", "sas", "sarl", "group", "groupe", "software", "technologies", "technology",
           "media", "digital", "france", "europe", "international", "the", "and", "corp", "limited", "company",
           "data", "online", "market", "marketing", "network", "networks", "solutions", "services", "labs", "global",
           "interactive", "mobile", "video", "ads", "advertising", "systems", "holding", "holdings", "deutschland",
           "ireland", "team", "support", "privacy", "service", "client", "clients", "contact", "info", "mail"}

# Sujets ou expéditeurs qui évoquent une réponse à une demande RGPD, quand rien d'autre ne permet de la rattacher.
CANDIDATE = re.compile(
    r"rgpd|gdpr|dsar|privacy|data.?protection|donn[ée]es personnelles|vie priv[ée]e|effacement|erasure|"
    r"suppression|deletion|delete|l[öo]schung|datenschutz|data subject|your request|votre demande|"
    r"ticket|case\b|\bdpo\b|article 17|right to be forgotten|droit [àa] l.oubli", re.I)
OUR_SUBJECT = re.compile(r"demande d.effacement de mes donn[ée]es|request for erasure of my personal data|"
                         r"relance .{0,3}demande d.effacement|reminder .{0,3}erasure request", re.I)

# Où commence le message d'origine cité dans une réponse : on ne lit que ce qui précède.
QUOTE = re.compile(
    r"^(?:>.*|On .{5,120} wrote:|Le .{5,120} a [ée]crit ?:|Am .{5,120} schrieb.*:|-{2,} ?(?:Original Message|Message d.origine|"
    r"Forwarded message|Message transf[ée]r[ée]).*|_{10,}|From: .+|De ?: .+|Von: .+|Envoy[ée] ?: .+|Sent: .+)$", re.I | re.M)

# Ce que dit la réponse. Chaque motif vaut un point pour sa catégorie ; l'ordre de CATEGORIES départage.
PATTERNS = {
    "needs_action": (
        r"(?:verify|confirm|prove|validate) your (?:identity|request|e-?mail|email address)",
        r"proof of (?:identity|id)|copy of (?:your |an? )?(?:id|passport|identity|identification)",
        r"pi[èe]ce d.identit[ée]|justificatif d.identit[ée]|(?:v[ée]rifier|confirmer|justifier de) votre identit[ée]",
        r"please (?:provide|send|confirm|reply|complete|fill|click|specify|let us know|use|submit|visit)",
        r"(?:click|cliquez) (?:on |sur )?(?:the |this |le |ce |la )?(?:link|button|lien|bouton)",
        r"merci de (?:nous )?(?:fournir|pr[ée]ciser|confirmer|compl[ée]ter|remplir|indiquer|cliquer|r[ée]pondre|transmettre)",
        r"veuillez (?:nous |cliquer|confirmer|remplir|compl[ée]ter|fournir|pr[ée]ciser|indiquer|utiliser)",
        r"(?:pourriez|pouvez)[- ]vous (?:nous )?(?:fournir|pr[ée]ciser|confirmer|indiquer|transmettre)",
        r"(?:we|i) (?:need|require|kindly ask|would need) (?:you|some|more|additional|further)",
        r"additional information|informations? compl[ée]mentaires?|further information (?:is|to)",
        r"(?:submit|make|fill).{0,40}(?:web ?form|online form|portal|formulaire)|via (?:notre|our) (?:formulaire|portail|form|portal)",
        r"bitte (?:best[äa]tigen|senden|klicken|geben|teilen|nennen|f[üu]llen|nutzen|verwenden)",
        r"ben[öo]tigen wir|identit[äa]t (?:nachweisen|best[äa]tigen|[üu]berpr[üu]fen)|um ihre anfrage (?:bearbeiten|zu bearbeiten)",
    ),
    "refused": (
        r"(?:unable|not able|cannot|can.?t|can not) (?:to )?(?:comply|fulfil+|delete|erase|process your request|action)",
        r"(?:required|obliged|obligated) (?:by law )?to (?:retain|keep|store)",
        r"legal obligation to (?:retain|keep)|obligation l[ée]gale de conserv",
        r"ne (?:pouvons|sommes) pas (?:en mesure de )?(?:donner suite|supprimer|effacer|acc[ée]der [àa] votre demande)",
        r"manifestly unfounded|manifestement infond[ée]e|(?:reject|refus|declin)\w* (?:your|votre) (?:request|demande)",
        r"(?:does|do) not apply|not subject to (?:the )?gdpr|n.est pas applicable|ne s.applique pas",
        r"k[öo]nnen (?:wir )?ihre[rm]? .{0,40}nicht (?:nachkommen|entsprechen)|gesetzliche aufbewahrungspflicht",
    ),
    "nodata": (
        r"(?:do|does|did) not (?:hold|have|process|store|possess|retain) any (?:personal )?(?:data|information|record)",
        r"no (?:personal )?(?:data|information|records?) (?:about|relating to|concerning|associated with|linked to|for|on) (?:you|your)",
        r"(?:could|can|were|was|are) (?:not|unable to) (?:find|locate|identify|match)|unable to (?:find|locate|identify)",
        r"no (?:matching )?(?:record|account|profile|data)s? (?:was |were |has been |have been )?found",
        r"aucune donn[ée]e|ne (?:d[ée]tenons|poss[ée]dons|traitons|disposons) (?:d.)?aucune|n.avons (?:trouv[ée]|identifi[ée]) aucun",
        r"ne figurez pas|n.apparaissez pas|introuvable dans (?:nos|notre)",
        r"keine (?:personenbezogenen |ihrer )?daten|liegen uns keine|nicht (?:in unsere[rnm]|bei uns) .{0,40}(?:gespeichert|vorhanden|bekannt)",
    ),
    "done": (
        r"(?:has|have) (?:now |already |successfully |been |duly )*(?:been )?(?:deleted|erased|removed|anonymi[sz]ed|purged)",
        r"(?:we|i)(?: have|.ve)? (?:now |already |successfully |duly )*(?:deleted|erased|removed|anonymi[sz]ed|processed your (?:deletion|erasure))",
        r"(?:deletion|erasure|removal|opt.?out) (?:request )?(?:has been|is|was) (?:now )?(?:complete|completed|processed|carried out|done|confirmed|successful)",
        r"your request has been (?:completed|fulfilled|actioned|executed)",
        r"ont (?:bien )?[ée]t[ée] (?:supprim[ée]e?s?|effac[ée]e?s?|anonymis[ée]e?s?)|a (?:bien )?[ée]t[ée] (?:supprim[ée]e?|effac[ée]e?|cl[ôo]tur[ée]e?)",
        r"avons (?:bien )?(?:supprim[ée]|effac[ée]|proc[ée]d[ée] [àa] (?:la suppression|l.effacement))",
        r"(?:suppression|effacement) (?:de vos donn[ée]es )?(?:a [ée]t[ée] |est )?(?:effectu[ée]e?|r[ée]alis[ée]e?|confirm[ée]e?|termin[ée]e?)",
        r"no longer (?:hold|have|process|store)|wurden gel[öo]scht|haben .{0,40}gel[öo]scht",
        r"(?:been )?(?:opted out|unsubscribed|suppressed)|added to (?:our )?suppression list",
        r"(?:wurden?|haben wir|sind) .{0,60}gel[öo]scht|l[öo]schung (?:ist |wurde )?(?:erfolgt|durchgef[üu]hrt|abgeschlossen|vorgenommen)",
    ),
    "processing": (
        r"(?:we|i)(?: have|.ve)? (?:well |duly |safely )?received your (?:request|e-?mail|message|enquiry|inquiry)",
        r"(?:thank you|thanks) for (?:your (?:request|e-?mail|message|enquiry|inquiry)|contacting|reaching out|getting in touch)",
        r"(?:will|shall) (?:respond|reply|get back|process|review|handle|be in touch|come back)",
        r"is (?:being|currently being) (?:processed|reviewed|handled)|(?:working|looking) (?:on|into) (?:it|your)",
        r"within (?:\d+|one|thirty|a) (?:business |working |calendar )?(?:days?|hours?|months?|weeks?)",
        r"(?:ticket|case|reference|request) (?:number|id|#|no\.?)|a (?:ticket|case) has been (?:created|opened)",
        r"nous avons bien re[çc]u|accusons r[ée]ception|bien pris(?:e)? en compte|a (?:bien )?[ée]t[ée] (?:enregistr[ée]e|transmise|prise en compte)",
        r"(?:sera|est en cours d[e']) ?(?:trait[ée]e?|traitement|examin[ée]e?)|reviendrons vers vous|dans (?:un|les) d[ée]lais?|sous \d+ (?:jours|heures)",
        r"wir haben ihre (?:anfrage|nachricht) erhalten|automatic reply|r[ée]ponse automatique|auto.?reply",
        r"ihre (?:anfrage|nachricht|e-?mail) (?:ist |wurde )?(?:bei uns )?(?:eingegangen|erhalten)|eingang ihrer|in bearbeitung",
        r"wir (?:werden|k[üu]mmern uns|bearbeiten|melden uns|pr[üu]fen)|schnellstm[öo]glich|vielen dank f[üu]r ihre (?:anfrage|nachricht|e-?mail)",
        r"(?:request|ticket|case) (?:has been )?(?:logged|registered|created|opened|received)|logged successfully",
    ),
}
CATEGORIES = ("needs_action", "refused", "nodata", "done", "processing")       # ordre de priorité en cas d'égalité
COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in PATTERNS.items()}

# catégorie -> (action proposée, phrase affichée à l'utilisateur)
SUGGESTION = {
    "done": ("done", "Ils disent avoir supprimé vos données"),
    "nodata": ("done", "Ils disent n'avoir aucune donnée sur vous : rien à supprimer"),
    "needs_action": ("needs_action", "Ils vous demandent quelque chose (confirmation, justificatif, formulaire)"),
    "refused": ("refused", "Ils refusent, ou disent que la demande ne s'applique pas"),
    "processing": ("processing", "Ils accusent réception et annoncent un traitement"),
}


def clean(text, limit=1500):
    """Le texte propre à la réponse : sans le message d'origine cité, sans lignes vides en trop."""
    text = (text or "").replace("\r", "")
    m = QUOTE.search(text)
    if m and m.start() > 40:        # une citation dès la première ligne : réponse écrite sous la citation, on garde tout
        text = text[:m.start()]
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out, blank = [], False
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif out and not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()[:limit]


def classify(text, subject=""):
    """Renvoie (catégorie, action proposée, phrase) ; catégorie "" si rien de net."""
    body = " ".join((subject + "\n" + (text or "")).split())
    scores = {k: sum(1 for rx in COMPILED[k] if rx.search(body)) for k in CATEGORIES}
    # Un accusé de réception dit souvent « nous supprimerons… » ou « si besoin nous vous demanderons… » :
    # une seule tournure de ce genre ne l'emporte pas sur plusieurs tournures d'accusé de réception.
    if scores["processing"] >= 2:
        for k in ("needs_action", "done", "refused"):
            if scores[k] == 1:
                scores[k] = 0
    best = max(CATEGORIES, key=lambda k: (scores[k], -CATEGORIES.index(k)))
    if not scores[best]:
        return "", "", ""
    action, phrase = SUGGESTION[best]
    return best, action, phrase


def name_tokens(name):
    """Mots qui identifient l'entreprise : « AVAST Software s.r.o » -> {"avast"}."""
    words = [w for w in companies.norm(name).split() if w not in GENERIC and not w.isdigit()]
    return {w for w in words if len(w) >= 4} or {w for w in words[:1] if len(w) >= 3}


def is_candidate(h):
    """Ce message, non rattaché, ressemble-t-il à une réponse à une demande RGPD ?"""
    if OUR_SUBJECT.search(h.get("subject") or ""):
        return True
    if h.get("unsubscribe"):
        return False
    return bool(CANDIDATE.search("%s %s %s" % (h.get("subject") or "", h.get("from_name") or "", h.get("from") or "")))


def header_evidence(r, h, aliases=None):
    """Indices tirés des seuls en-têtes. Renvoie (score, explication). 10 = certain, 3 = probable."""
    ids = {r.get("message_id"), r.get("reminder_message_id")} - {None, ""}
    if ids & set(h.get("refs") or []):
        return 10, "réponse directe à votre message"
    if str(r["id"]) in REF.findall(h.get("subject") or ""):
        return 10, "référence EM-%d dans l'objet" % r["id"]
    sender = companies.base_domain(h.get("from") or "")
    target = companies.base_domain(r.get("email") or "")
    if sender and sender not in companies.FREEMAIL:
        if sender == target and not h.get("unsubscribe"):
            return 6, "même domaine que l'adresse contactée"
        if (aliases or {}).get(sender) == r.get("slug"):
            return 6, "expéditeur déjà rattaché à cette entreprise"
    if not h.get("unsubscribe"):
        who = set(companies.norm("%s %s" % (h.get("from_name") or "", (h.get("from") or "").replace("@", " "))).split())
        if name_tokens(r.get("name") or "") & who:
            return 3, "le nom de l'entreprise figure chez l'expéditeur"
    return 0, ""


def body_evidence(r, text):
    """Indices tirés du texte complet (réponse et message cité)."""
    low = (text or "").lower()
    if not low:
        return 0, ""
    if str(r["id"]) in REF.findall(text):
        return 10, "référence EM-%d dans le message" % r["id"]
    score, why = 0, []
    if r.get("email") and r["email"].lower() in low:
        score += 5
        why.append("l'adresse contactée est citée")
    target = companies.base_domain(r.get("email") or "")
    if target and target not in companies.FREEMAIL and target in low:
        score += 2
        why.append("leur domaine est cité")
    words = set(companies.norm(text[:6000]).split())
    if name_tokens(r.get("name") or "") & words:
        score += 2
        why.append("leur nom est cité")
    return score, ", ".join(why)


def best_match(waiting, h, text=None, aliases=None):
    """La demande à laquelle ce message répond, si elle se détache nettement. Renvoie (demande, explication) ou (None, "")."""
    scored = []
    for r in waiting:
        mark = max(r.get("sent_at") or "", r.get("reply_at") or "", r.get("ack_at") or "")
        if not h.get("date") or h["date"] <= mark:
            continue
        s, why = header_evidence(r, h, aliases)
        if text is not None and s < 10:
            s2, why2 = body_evidence(r, text)
            if s2 >= 10:
                s, why = s2, why2
            elif s2:
                s, why = s + s2, ", ".join(x for x in (why, why2) if x)
        if s:
            scored.append((s, r, why))
    if not scored:
        return None, ""
    scored.sort(key=lambda x: -x[0])
    top = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0
    if top[0] >= 10 or (top[0] >= 3 and top[0] - runner >= 2):
        return top[1], top[2]
    return None, ""


def guesses(waiting, h, text=None, aliases=None, limit=3):
    """Pour un message non rattaché : les demandes les plus plausibles, meilleures d'abord (ids)."""
    scored = []
    for r in waiting:
        s = header_evidence(r, h, aliases)[0] + (body_evidence(r, text)[0] if text is not None else 0)
        if s:
            scored.append((s, r["id"]))
    return [rid for _, rid in sorted(scored, reverse=True)[:limit]]
