"""Cycle de vie des demandes : création, envoi, détection des réponses, relances, plaintes, renouvellement."""
import json
import threading
import time
from datetime import datetime, timedelta

from . import companies, letters, mailer, replies, store

DEADLINE_DAYS = 31      # un mois (art. 12.3 RGPD), avec un jour de marge
REMINDER_DAYS = 15
STATUS = {
    "draft": "Brouillon",
    "queued": "En file d'envoi",
    "manual": "À envoyer vous-même",
    "sent": "Envoyée, en attente",
    "reminded": "Relancée, en attente",
    "replied": "Réponse reçue, à valider",
    "needs_action": "Ils demandent quelque chose",
    "complaint_due": "Plainte CNIL à déposer",
    "complaint_filed": "Plainte déposée",
    "done": "Suppression confirmée",
    "refused": "Refusée",
    "bounced": "Adresse invalide",
}
CLOSED = ("done", "refused", "complaint_filed", "bounced")
AUTOMATION_DEFAULTS = {"auto_remind": True, "repeat_months": 6}

JOBS = {}
_jobs_lock = threading.Lock()
_send_lock = threading.Lock()


def automation():
    a = dict(AUTOMATION_DEFAULTS)
    a.update(store.get("automation", {}))
    return a


def profile():
    return store.get("profile", {})


def profile_ready():
    p = profile()
    return bool(p.get("name") and p.get("email"))


# ---------- tâches de fond ----------

def start_job(name, fn):
    with _jobs_lock:
        if JOBS.get(name, {}).get("running"):
            return False
        job = JOBS[name] = {"running": True, "done": 0, "total": 0, "message": "", "result": None, "error": None}

    def progress(done, total, message=""):
        job.update(done=done, total=total, message=message)

    def run():
        try:
            job["result"] = fn(progress)
        except Exception as e:  # noqa: BLE001 - l'erreur est affichée dans l'interface
            job["error"] = "%s : %s" % (type(e).__name__, mailer._clean(e))
        finally:
            job["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return True


# ---------- création ----------

def create_requests(slugs, recurring=False):
    if not profile_ready():
        raise ValueError("Remplissez d'abord votre nom et votre e-mail dans l'onglet Profil.")
    p = profile()
    created, skipped = [], []
    for slug in dict.fromkeys(slugs):
        c = companies.get(slug)
        if not c:
            skipped.append({"slug": slug, "reason": "introuvable"})
            continue
        open_one = store.one("SELECT id FROM requests WHERE slug=? AND status NOT IN (%s)" % ",".join("?" * len(CLOSED)),
                             (slug, *CLOSED))
        if open_one:
            skipped.append({"slug": slug, "reason": "demande déjà en cours"})
            continue
        lang, subject, body = letters.erasure(p, c)
        status = "draft" if c.get("email") else "manual"
        rid = store.execute(
            "INSERT INTO requests(slug,name,email,webform,lang,status,recurring,subject,body,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (slug, c["name"], c.get("email") or "", c.get("webform") or "", lang, status,
             1 if recurring else 0, subject, body, store.iso()))
        store.event(rid, "created", "")
        created.append(rid)
    return {"created": created, "skipped": skipped}


def request_for_urls(slug, urls):
    """Demande d'effacement citant des pages précises (module Empreinte). Tant que la demande au même site
    n'est pas partie, les pages s'y ajoutent. Renvoie (id, True si une demande déjà envoyée couvre ce site)."""
    company = companies.get(slug)
    if not company:
        raise ValueError("Entreprise introuvable.")
    marks = ",".join("?" * len(CLOSED))
    r = store.one("SELECT * FROM requests WHERE slug=? AND status NOT IN (%s) ORDER BY id DESC LIMIT 1" % marks,
                  (slug, *CLOSED))
    if r and r["status"] not in ("draft", "manual"):
        return r["id"], True
    if not r:
        rid = create_requests([slug])["created"][0]
        r = store.one("SELECT * FROM requests WHERE id=?", (rid,))
    known = json.loads(r["urls"] or "[]")
    merged = known + [u for u in urls if u not in known]
    lang, subject, body = letters.erasure(profile(), company, urls=merged)
    store.update_request(r["id"], urls=json.dumps(merged), lang=lang, subject=subject, body=body)
    return r["id"], False


def queue(ids):
    n = 0
    for rid in ids:
        r = store.one("SELECT status, email FROM requests WHERE id=?", (rid,))
        if r and r["status"] == "draft" and r["email"]:
            store.update_request(rid, status="queued", error=None)
            n += 1
    return n


# ---------- envoi ----------

def with_reference(r):
    """Ajoute à l'objet et au courrier une référence propre à la demande (EM-12). Beaucoup de services
    répondent depuis un autre domaine, sans lien technique avec le message d'origine : la référence, qu'ils
    citent presque toujours, permet alors de rattacher leur réponse."""
    ref = "EM-%d" % r["id"]
    subject, body = r["subject"] or "", r["body"] or ""
    if ref not in subject:
        subject = "%s [%s]" % (subject, ref)
    if ref not in body:
        label = "Reference of my request" if r.get("lang") == "en" else "Référence de ma demande"
        body = "%s\n\n%s : %s\n" % (body.rstrip(), label, ref)
    return subject, body


def sent_today():
    start = store.now().replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    return store.one("SELECT COUNT(*) AS n FROM events WHERE type IN ('sent','reminder_sent') AND at>=?", (start,))["n"]


def _room():
    return mailer.settings()["daily_cap"] - sent_today()


def send_queued(progress=lambda *a: None):
    """Envoie les demandes en file, en respectant la limite quotidienne et la pause entre deux envois."""
    with _send_lock:
        todo = store.rows("SELECT * FROM requests WHERE status='queued' ORDER BY id")
        s = mailer.settings()
        out = {"sent": 0, "test": 0, "errors": 0, "left": 0}
        for i, r in enumerate(todo):
            progress(i, len(todo), "Envoi à %s…" % r["name"])
            if not s["test_mode"] and _room() <= 0:
                out["left"] = len(todo) - i
                break
            subject, body = with_reference(r)
            try:
                mid, real = mailer.send(r["email"], subject, body)
            except Exception as e:  # noqa: BLE001
                err = "%s : %s" % (type(e).__name__, mailer._clean(e))
                store.update_request(r["id"], status="draft", error=err)
                store.event(r["id"], "error", err)
                out["errors"] += 1
                continue
            if real:
                t = store.now()
                store.update_request(r["id"], status="sent", sent_at=store.iso(t), message_id=mid, error=None,
                                     subject=subject, body=body,
                                     due_at=store.iso(t + timedelta(days=DEADLINE_DAYS)))
                store.event(r["id"], "sent", r["email"])
                out["sent"] += 1
                if i < len(todo) - 1:
                    time.sleep(s["delay_seconds"])
            else:
                store.update_request(r["id"], status="draft")
                store.event(r["id"], "test", "Mode test : fichier .eml créé dans data/outbox, rien n'a été envoyé.")
                out["test"] += 1
        progress(len(todo), len(todo), "Terminé")
        return out


# ---------- réponses ----------

WAITING = ("sent", "reminded", "needs_action", "replied")      # demandes pour lesquelles un message peut encore arriver
MAX_TEXTS = 80                                                  # messages lus en entier par relevé
SUGGESTION_LABEL = {"done": "Suppression confirmée", "needs_action": "Ils demandent quelque chose",
                    "refused": "Ils refusent", "processing": "En cours de traitement"}


def match_replies(open_requests, headers, aliases=None):
    """Fonction pure, d'après les seuls en-têtes : rattache chaque message à la demande qu'il concerne.
    Renvoie une liste (id, type, en-tête) avec type = 'reply', 'ack' (réponse automatique) ou 'bounce'."""
    out = []
    for h in sorted(headers, key=lambda x: x["date"]):
        r, how = replies.best_match(open_requests, h, None, aliases)
        if r:
            out.append((r["id"], _kind(h, how), dict(h, how=how)))
    return out


def _kind(h, how):
    if how.startswith("réponse directe") and h["from"].partition("@")[0].lower() in ("mailer-daemon", "postmaster"):
        return "bounce"
    return "ack" if h.get("auto") else "reply"


def _key(h):
    return (h.get("folder") or "INBOX", str(h.get("uid") or ""))


def apply_matches(matches, texts=None):
    """Enregistre les rattachements. Avec le texte des messages, ajoute l'extrait et une proposition de
    classement, que l'utilisateur validera. Un message automatique qui se borne à accuser réception est
    noté sans rien demander ; s'il réclame une action ou confirme la suppression, il est signalé."""
    counts = {"reply": 0, "ack": 0, "bounce": 0}
    for rid, kind, h in matches:
        r = store.one("SELECT status, suggestion FROM requests WHERE id=?", (rid,))
        if not r or r["status"] not in WAITING:
            continue
        text = (texts or {}).get(_key(h))
        excerpt = replies.clean(text) if text else ""
        category, action, phrase = replies.classify(excerpt, h["subject"]) if text is not None else ("", "", "")
        detail = "%s — %s" % (h["from"], h["subject"])
        if kind == "bounce":
            store.update_request(rid, status="bounced", closed_at=store.iso(), reply_at=h["date"],
                                 reply_from=h["from"], reply_subject=h["subject"], suggestion=None)
        elif kind == "ack" and (category == "processing" or text is None):
            # Message automatique qui accuse seulement réception (ou dont le texte n'a pas été lu) : noté, sans plus.
            store.update_request(rid, ack_at=h["date"])
        elif r["status"] == "replied" and r["suggestion"] not in (None, "", "processing") and action in ("", "processing"):
            # Une réponse plus parlante attend déjà la validation : ce message-ci n'y change rien.
            store.update_request(rid, ack_at=h["date"])
            kind = "ack"
        else:
            how = h.get("how") or ""
            if h.get("folder") and h["folder"] != "INBOX":
                how += " ; trouvée dans les indésirables"
            store.update_request(rid, status="replied", reply_at=h["date"], reply_from=h["from"],
                                 reply_subject=h["subject"], reply_excerpt=excerpt, suggestion=action or None,
                                 reply_how="%s%s" % (phrase + ". " if phrase else "", ("Rattachée car : " + how) if how else ""))
            kind = "reply"
        store.event(rid, kind, detail)
        counts[kind] += 1
    return counts


def has_waiting():
    return bool(store.one("SELECT id FROM requests WHERE status IN (%s) AND sent_at IS NOT NULL LIMIT 1"
                          % ",".join("?" * len(WAITING)), WAITING))


def check_replies(progress=lambda *a: None):
    """Relève la boîte : rattache les réponses, lit leur texte, propose un classement, met de côté celles
    qui ressemblent à une réponse sans pouvoir être rattachées."""
    zero = {"reply": 0, "ack": 0, "bounce": 0, "checked": 0, "orphans": 0}
    everyone = store.rows("SELECT * FROM requests WHERE sent_at IS NOT NULL")
    if not store.get("replies_reread"):
        # Une seule fois : les messages repérés par les versions précédentes, qui ne lisaient que les en-têtes,
        # sont relus pour recevoir un extrait et une proposition. Les demandes déjà classées par l'utilisateur
        # ne sont pas touchées.
        for r in everyone:
            if r["reply_excerpt"] is None and r["status"] in ("sent", "reminded") and r["ack_at"] and not r["processing_at"]:
                store.update_request(r["id"], ack_at=None)
            elif r["reply_excerpt"] is None and r["status"] == "replied":
                store.update_request(r["id"], ack_at=None, reply_at=None)
        store.set("replies_reread", store.iso())
        everyone = store.rows("SELECT * FROM requests WHERE sent_at IS NOT NULL")
    waiting = [r for r in everyone if r["status"] in WAITING]
    if not waiting:
        return zero
    first = min(r["sent_at"] for r in waiting)
    headers = mailer.fetch_headers(since=datetime.fromisoformat(first) - timedelta(days=1), progress=progress, junk=True)
    aliases = store.get("reply_aliases", {})
    own = mailer.settings()["email"].lower()
    set_aside = store.get("seen_candidates", [])
    seen = set(set_aside)
    matched, candidates = [], []
    for h in sorted(headers, key=lambda x: x["date"]):
        if h["from"] == own or not h["date"]:
            continue
        r, how = replies.best_match(waiting, h, None, aliases)
        if r:
            matched.append((r["id"], _kind(h, how), dict(h, how=how)))
        elif h["date"] > first and (h["message_id"] or "%s/%s" % _key(h)) not in seen and replies.is_candidate(h):
            # Déjà traité (réponse plus ancienne que la dernière relevée, ou demande déjà classée) : on passe.
            if not any(replies.header_evidence(x, h, aliases)[0] >= 3 for x in everyone):
                candidates.append(h)
    want = [_key(h) for _, kind, h in matched if kind != "bounce"] + [_key(h) for h in candidates]
    texts = mailer.fetch_texts([k for k in dict.fromkeys(want) if k[1]][:MAX_TEXTS], progress) if want else {}
    orphans = store.get("orphan_replies", [])
    for h in candidates:
        text = texts.get(_key(h))
        if text is None:
            continue        # texte pas encore lu (trop de messages d'un coup, ou lecture en échec) : au prochain relevé
        key = h["message_id"] or "%s/%s" % _key(h)
        set_aside.append(key)       # examiné une fois pour toutes : il ne sera pas relu
        r, how = replies.best_match(waiting, h, text, aliases)
        if r:
            matched.append((r["id"], "ack" if h.get("auto") else "reply", dict(h, how=how)))
            continue
        if text and any(replies.body_evidence(x, text)[0] >= 5 for x in everyone):
            continue        # concerne une demande déjà classée, ou un message déjà relevé
        excerpt = replies.clean(text) if text else ""
        category, action, phrase = replies.classify(excerpt, h["subject"]) if text is not None else ("", "", "")
        if h.get("auto") and category == "processing" and not replies.OUR_SUBJECT.search(h["subject"]):
            continue        # accusé de réception automatique impossible à rattacher : sans intérêt
        orphans.append({"key": key, "from": h["from"], "from_name": h["from_name"], "subject": h["subject"],
                        "date": h["date"], "excerpt": excerpt[:700], "action": action, "phrase": phrase,
                        "junk": bool(h.get("folder") and h["folder"] != "INBOX"),
                        "guesses": replies.guesses(waiting, h, text, aliases)})
    counts = apply_matches(sorted(matched, key=lambda x: x[2]["date"]), texts)
    store.set("orphan_replies", orphans[-100:])
    store.set("seen_candidates", set_aside[-2000:])
    counts.update(checked=len(headers), orphans=len(orphans))
    store.set("last_check", store.iso())
    return counts


def attach_orphan(key, rid):
    """L'utilisateur indique à quelle demande se rapporte un message mis de côté. L'expéditeur est retenu :
    ses prochains messages seront rattachés seuls."""
    orphans = store.get("orphan_replies", [])
    o = next((x for x in orphans if x["key"] == key), None)
    r = store.one("SELECT * FROM requests WHERE id=?", (rid,))
    if not o or not r:
        raise ValueError("Message ou demande introuvable.")
    domain = companies.base_domain(o["from"])
    if domain and domain not in companies.FREEMAIL:
        aliases = store.get("reply_aliases", {})
        aliases[domain] = r["slug"]
        store.set("reply_aliases", aliases)
    store.update_request(rid, status="replied", reply_at=o["date"], reply_from=o["from"], reply_subject=o["subject"],
                         reply_excerpt=o["excerpt"], suggestion=o["action"] or None, closed_at=None,
                         reply_how="%sRattachée par vous." % (o["phrase"] + ". " if o["phrase"] else ""))
    store.event(rid, "reply", "%s — %s" % (o["from"], o["subject"]))
    store.set("orphan_replies", [x for x in orphans if x["key"] != key])


def dismiss_orphan(key):
    store.set("orphan_replies", [x for x in store.get("orphan_replies", []) if x["key"] != key])


def accept(ids=None, only=None):
    """Valide la proposition de l'application pour ces demandes (toutes celles en attente de validation si ids
    est vide), éventuellement limitée à un type de proposition. Renvoie le nombre de demandes classées."""
    rows = store.rows("SELECT id, suggestion FROM requests WHERE status='replied' AND suggestion IS NOT NULL")
    n = 0
    for r in rows:
        if (ids and r["id"] not in ids) or (only and r["suggestion"] != only) or r["suggestion"] not in SUGGESTION_LABEL:
            continue
        set_status(r["id"], r["suggestion"])
        n += 1
    return n


# ---------- relances, plaintes, renouvellement ----------

def _complaint_for(r):
    return letters.complaint(profile(), r, companies.get(r["slug"]))


def send_reminders():
    s = mailer.settings()
    out = {"reminders": 0, "errors": 0}
    if s["test_mode"] or not automation()["auto_remind"]:
        return out
    due = store.rows("SELECT * FROM requests WHERE status='sent' AND email<>'' AND due_at<=? ORDER BY due_at", (store.iso(),))
    with _send_lock:
        for i, r in enumerate(due):
            if _room() <= 0:
                break
            subject, body = letters.reminder(profile(), r)
            if "EM-%d" % r["id"] not in subject:
                subject += " [EM-%d]" % r["id"]
            try:
                mid, _ = mailer.send(r["email"], subject, body, in_reply_to=r["message_id"])
            except Exception as e:  # noqa: BLE001
                store.event(r["id"], "error", "Relance : %s" % mailer._clean(e))
                out["errors"] += 1
                continue
            t = store.now()
            store.update_request(r["id"], status="reminded", reminded_at=store.iso(t), reminder_message_id=mid,
                                 due2_at=store.iso(t + timedelta(days=REMINDER_DAYS)))
            store.event(r["id"], "reminder_sent", r["email"])
            out["reminders"] += 1
            if i < len(due) - 1:
                time.sleep(s["delay_seconds"])
    return out


def escalate():
    """Délai de relance dépassé (ou demande par formulaire restée sans réponse) : plainte CNIL à déposer."""
    n = 0
    late = store.rows("SELECT * FROM requests WHERE (status='reminded' AND due2_at<=?) "
                      "OR (status='sent' AND email='' AND due_at<=?)", (store.iso(), store.iso()))
    for r in late:
        store.update_request(r["id"], status="complaint_due", complaint=_complaint_for(r))
        store.event(r["id"], "complaint_due", "Aucune réponse dans les délais.")
        n += 1
    return n


def renew():
    """Comme Incogni : redemande l'effacement aux courtiers tous les N mois, car ils rachètent les données."""
    months = int(automation()["repeat_months"] or 0)
    if months <= 0 or not profile_ready():
        return 0
    limit = store.iso(store.now() - timedelta(days=30 * months))
    # Seule la demande la plus récente de chaque entreprise compte.
    old = store.rows("SELECT slug FROM requests r WHERE recurring=1 AND status='done' AND closed_at<=? "
                     "AND id=(SELECT MAX(id) FROM requests WHERE slug=r.slug)", (limit,))
    n = 0
    for o in old:
        res = create_requests([o["slug"]], recurring=True)
        n += queue(res["created"])
    return n


def run_cycle(progress=lambda *a: None):
    """Le travail automatique complet. Lancé par le bouton, ou chaque jour par la tâche planifiée."""
    out = {"at": store.iso()}
    if mailer.ready(for_imap=True):
        try:
            progress(0, 0, "Lecture des réponses…")
            out["replies"] = check_replies(progress)
        except Exception as e:  # noqa: BLE001
            out["replies_error"] = "%s : %s" % (type(e).__name__, mailer._clean(e))
    progress(0, 0, "Relances…")
    out.update(send_reminders())
    out["complaints"] = escalate()
    out["renewed"] = renew()
    out["sending"] = send_queued(progress)
    try:
        from . import footprint     # import tardif : footprint dépend de ce module
        found = footprint.weekly(progress)
        if found:
            out["footprint"] = found
    except Exception as e:  # noqa: BLE001 - la recherche hebdomadaire ne doit jamais bloquer les relances
        out["footprint_error"] = "%s : %s" % (type(e).__name__, mailer._clean(e))
    store.set("last_cycle", out)
    return out


# ---------- actions manuelles ----------

def set_status(rid, action):
    r = store.one("SELECT * FROM requests WHERE id=?", (rid,))
    if not r:
        raise ValueError("Demande introuvable.")
    t = store.iso()
    store.update_request(rid, suggestion=None)      # l'utilisateur a tranché : la proposition n'a plus lieu d'être
    if action in ("done", "refused", "complaint_filed"):
        fields = {"status": action, "closed_at": t}
        if action == "refused" and r["sent_at"]:
            fields["complaint"] = letters.complaint(profile(), dict(r, status="refused"), companies.get(r["slug"]))
        store.update_request(rid, **fields)
    elif action == "needs_action":
        store.update_request(rid, status="needs_action")
    elif action == "waiting":      # « ce n'était pas une vraie réponse » ou « j'ai répondu, j'attends »
        if not r["sent_at"]:
            raise ValueError("Cette demande n'a pas encore été envoyée.")
        store.update_request(rid, status="reminded" if r["reminded_at"] else "sent", closed_at=None)
    elif action == "processing":   # « nous avons bien reçu votre demande, nous la traitons »
        # Ce n'est pas encore une réponse sur le fond : la demande repasse en attente et le délai d'un mois
        # continue de courir. Sans suite de leur part, la relance partira donc comme prévu.
        if not r["sent_at"]:
            raise ValueError("Cette demande n'a pas encore été envoyée.")
        store.update_request(rid, status="reminded" if r["reminded_at"] else "sent", processing_at=t, closed_at=None)
    elif action == "sent_manually":  # demande déposée via le formulaire du site
        now = store.now()
        store.update_request(rid, status="sent", sent_at=store.iso(now),
                             due_at=store.iso(now + timedelta(days=DEADLINE_DAYS)))
    elif action == "complaint_due":
        if not r["sent_at"]:
            raise ValueError("Cette demande n'a pas encore été envoyée.")
        store.update_request(rid, status="complaint_due", complaint=_complaint_for(r))
    elif action == "unqueue":
        if r["status"] == "queued":
            store.update_request(rid, status="draft")
    else:
        raise ValueError("Action inconnue.")
    store.event(rid, "manual", action)


def delete(ids):
    for rid in ids:
        # Les envois restent comptés dans la limite quotidienne même si la demande est supprimée.
        store.execute("UPDATE events SET request_id=NULL WHERE request_id=? AND type IN ('sent','reminder_sent')", (rid,))
        store.execute("DELETE FROM events WHERE request_id=?", (rid,))
        store.execute("DELETE FROM requests WHERE id=?", (rid,))


# ---------- partage d'adresses avec les autres utilisateurs ----------

SHARE_OUTCOME = {"done": "Suppression confirmée", "needs_action": "L'entreprise a répondu",
                 "refused": "L'entreprise a répondu", "complaint_filed": "L'entreprise a répondu"}


def shareable():
    """Entreprises ajoutées à la main dont l'adresse a fait ses preuves (c'est l'utilisateur qui l'a constaté),
    pas encore proposées et absentes des adresses déjà partagées."""
    done = set(store.get("shared", []))
    known = {c["email"] for c in companies._load().get("community", [])}
    out = []
    for c in store.get("custom_companies", []):
        email = (c.get("email") or "").lower()
        if not email or c["slug"] in done or email in known:
            continue
        r = store.one("SELECT status FROM requests WHERE slug=? AND status IN (%s) ORDER BY id DESC LIMIT 1"
                      % ",".join("?" * len(SHARE_OUTCOME)), (c["slug"], *SHARE_OUTCOME))
        if r:
            outcome = SHARE_OUTCOME[r["status"]]
            out.append({"slug": c["slug"], "name": c["name"], "email": c["email"], "web": c.get("web") or "",
                        "outcome": outcome, "url": companies.share_url(c, outcome)})
    return out


def mark_shared(slug):
    """Proposée ou écartée : on ne la suggère plus."""
    done = store.get("shared", [])
    if slug and slug not in done:
        store.set("shared", done + [slug])


def list_requests():
    cols = ("id, slug, name, email, webform, lang, status, recurring, created_at, sent_at, due_at, reminded_at, "
            "due2_at, closed_at, reply_at, reply_from, reply_subject, ack_at, error, processing_at, suggestion, reply_how")
    return store.rows("SELECT %s FROM requests ORDER BY id DESC" % cols)


def detail(rid):
    r = store.one("SELECT * FROM requests WHERE id=?", (rid,))
    if not r:
        raise ValueError("Demande introuvable.")
    r["address"] = (companies.get(r["slug"]) or {}).get("address") or ""
    r["events"] = store.rows("SELECT at, type, detail FROM events WHERE request_id=? ORDER BY id", (rid,))
    return r
