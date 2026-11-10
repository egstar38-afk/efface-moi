"""Lancement automatique du suivi sous Windows, sans fenêtre :
- une tâche planifiée quotidienne (9 h) ;
- en option, à chaque ouverture de session (pour un PC qui n'est pas allumé à 9 h).
Les deux lancent « auto » : lecture des réponses, relances, renouvellements. Sur un serveur Linux, c'est cron
qui s'en charge (voir serveur/efface-serveur.sh)."""
import subprocess
import sys
from pathlib import Path

from . import store

TASK_NAME = "EffaceMoi - suivi quotidien"
SCRIPT = store.ROOT / "efface.py"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "EffaceMoi"
STARTUP_DELAY = 120     # secondes : laisse au réseau le temps de se connecter après l'ouverture de session


def _python():
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    return str(quiet if quiet.exists() else exe)


def program():
    """Ce que la tâche doit retrouver pour être considérée comme pointant vers cette installation."""
    return str(sys.executable if store.FROZEN else SCRIPT)


def command():
    if store.FROZEN:
        return '"%s" auto' % sys.executable
    return '"%s" "%s" auto' % (_python(), SCRIPT)


def _run(args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def status():
    if sys.platform != "win32":
        return {"supported": False, "enabled": False, "here": False}
    r = _run(["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    enabled = r.returncode == 0
    # Si le dossier a été déplacé depuis l'activation, la tâche pointe vers l'ancien emplacement.
    return {"supported": True, "enabled": enabled, "here": enabled and program().lower() in r.stdout.lower()}


def enable(hour="09:00"):
    r = _run(["/Create", "/F", "/SC", "DAILY", "/ST", hour, "/TN", TASK_NAME, "/TR", command()])
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "Création de la tâche refusée par Windows.")
    return status()


def disable():
    r = _run(["/Delete", "/F", "/TN", TASK_NAME])
    if r.returncode != 0 and status()["enabled"]:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return status()


# ---------- au démarrage : entrée « Run » du compte Windows courant (aucun droit administrateur requis) ----------

def startup_status(key=RUN_KEY):
    if sys.platform != "win32":
        return {"supported": False, "enabled": False, "here": False}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, RUN_NAME)
    except OSError:
        return {"supported": True, "enabled": False, "here": False}
    return {"supported": True, "enabled": True, "here": program().lower() in str(value).lower()}


def startup_enable(key=RUN_KEY):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, "%s --delay %d" % (command(), STARTUP_DELAY))
    return startup_status(key)


def startup_disable(key=RUN_KEY):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_NAME)
    except OSError:
        pass
    return startup_status(key)


def full_status():
    return dict(status(), startup=startup_status())
