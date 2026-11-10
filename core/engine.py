"""Cycle de vie des demandes : création, envoi, détection des réponses, relances, plaintes, renouvellement."""
import threading
import time
from datetime import datetime, timedelta

from . import companies, letters, mailer, store

DEADLINE_DAYS = 31      # un mois (art. 12.3 RGPD), avec un jour de marge
REMINDER_DAYS = 15
STATUS = {
    "draft": "Brouillon",
    "queued": "En file d'envoi",
    "manual": "À envoyer vous-même",
    "sent": "Envoyée, en attente",
    "reminded": "Relancée, en attente",
    "replied": "Réponse reçue, à lire",
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


def queue(ids):
    n = 0
    for rid in ids:
        r = store.one("SELECT status, email FROM requests WHERE id=?", (rid,))
        if r and r["status"] == "draft" and r["email"]:
            store.update_request(rid, status="queued", error=None)
            n += 1
    return n


# ---------- envoi ----------

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
            try:
                mid, real = mailer.send(r["email"], r["subject"], r["body"])
            except Exception as e:  # noqa: BLE001
                err = "%s : %s" % (type(e).__name__, mailer._clean(e))
                store.update_request(r["id"], status="draft", error=err)
                store.event(r["id"], "error", err)
                out["errors"] += 1
                continue
            if real:
                t = store.now()
                store.update_request(r["id"], status="sent", sent_at=store.iso(t), message_id=mid, error=None,
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

def match_replies(open_requests, headers):
    """Fonction pure : associe des en-têtes de messages reçus aux demandes en attente.
    Renvoie une liste (id, type, en-tête) avec type = 'reply', 'ack' ou 'bounce'."""
    out = []
    for h in sorted(headers, key=lambda x: x["date"]):
        local, _, dom = h["from"].partition("@")
        for r in open_requests:
            mark = max(r.get("sent_at") or "", r.get("reply_at") or "", r.get("ack_at") or "")
            if not h["date"] or h["date"] <= mark:
                continue
            ids = {r.get("message_id"), r.get("reminder_message_id")} - {None, ""}
            by_ref = bool(ids & set(h["refs"]))
            rdom = companies.base_domain(r.get("email"))
            by_domain = (bool(rdom) and rdom not in companies.FREEMAIL and companies.base_domain(dom) == rdom
                         and not h["unsubscribe"])
            if by_ref and local.lower() in ("mailer-daemon", "postmaster"):
                out.append((r["id"], "bounce", h))
            elif by_ref or by_domain:
                out.append((r["id"], "ack" if h["auto"] else "reply", h))
    return out


def apply_matches(matches):
    counts = {"reply": 0, "ack": 0, "bounce": 0}
    for rid, kind, h in matches:
        r = store.one("SELECT status FROM requests WHERE id=?", (rid,))
        if not r or r["status"] not in ("sent", "reminded"):
            continue
        detail = "%s — %s" % (h["from"], h["subject"])
        if kind == "bounce":
            store.update_request(rid, status="bounced", closed_at=store.iso(), reply_at=h["date"],
                                 reply_from=h["from"], reply_subject=h["subject"])
        elif kind == "ack":
            store.update_request(rid, ack_at=h["date"])
        else:
            store.update_request(rid, status="replied", reply_at=h["date"], reply_from=h["from"],
                                 reply_subject=h["subject"])
        store.event(rid, kind, detail)
        counts[kind] += 1
    return counts


def check_replies(progress=lambda *a: None):
    waiting = store.rows("SELECT * FROM requests WHERE status IN ('sent','reminded')")
    if not waiting:
        return {"reply": 0, "ack": 0, "bounce": 0, "checked": 0}
    since = datetime.fromisoformat(min(r["sent_at"] for r in waiting)) - timedelta(days=1)
    headers = mailer.fetch_headers(since=since, progress=progress)
    counts = apply_matches(match_replies(waiting, headers))
    counts["checked"] = len(headers)
    store.set("last_check", store.iso())
    return counts


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
    store.set("last_cycle", out)
    return out


# ---------- actions manuelles ----------

def set_status(rid, action):
    r = store.one("SELECT * FROM requests WHERE id=?", (rid,))
    if not r:
        raise ValueError("Demande introuvable.")
    t = store.iso()
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
            "due2_at, closed_at, reply_at, reply_from, reply_subject, ack_at, error, processing_at")
    return store.rows("SELECT %s FROM requests ORDER BY id DESC" % cols)


def detail(rid):
    r = store.one("SELECT * FROM requests WHERE id=?", (rid,))
    if not r:
        raise ValueError("Demande introuvable.")
    r["address"] = (companies.get(r["slug"]) or {}).get("address") or ""
    r["events"] = store.rows("SELECT at, type, detail FROM events WHERE request_id=? ORDER BY id", (rid,))
    return r
