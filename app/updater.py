"""Mises à jour depuis GitHub (Releases).

Procédure : on publie une « Release » sur GitHub avec un tag de version (ex. v1.1.0).
Le logiciel compare avec le fichier VERSION, télécharge l'archive, fait une sauvegarde
de la base de données, remplace les fichiers du programme (jamais le dossier data/),
installe les dépendances puis redémarre (code de sortie 3 intercepté par run.py).
"""
import io
import os
import shutil
import subprocess
import sys
import threading
import zipfile

import requests

from . import BASE_DIR, get_version
from .models import get_setting

PROTECTED = {"data", ".venv", ".git", "__pycache__"}
RESTART_EXIT_CODE = 3


def _headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "Kubios-Updater"}
    token = get_setting("github_token")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def parse_version(v):
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def check():
    """Retourne dict(current, latest, available, notes, zip_url) ou lève une exception."""
    repo = (get_setting("github_repo") or "").strip().strip("/")
    if not repo or "/" not in repo:
        raise ValueError("Dépôt GitHub non configuré (format : proprietaire/depot).")
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=_headers(), timeout=30)
    if r.status_code == 404:
        raise ValueError("Aucune « release » publiée sur ce dépôt (ou dépôt privé sans jeton valide).")
    r.raise_for_status()
    data = r.json()
    current = get_version()
    latest = data.get("tag_name", "")
    return {
        "current": current,
        "latest": latest.lstrip("vV"),
        "available": parse_version(latest) > parse_version(current),
        "notes": data.get("body") or "",
        "name": data.get("name") or latest,
        "zip_url": data.get("zipball_url"),
        "published": data.get("published_at"),
    }


def check_and_store():
    """Vérifie GitHub et mémorise le résultat (affiché dans le menu et la page Mises à jour).
    Ne lève pas d'exception : l'erreur est mémorisée."""
    from datetime import datetime
    from . import db
    from .models import set_setting
    if not (get_setting("github_repo") or "").strip():
        return None
    info, error = None, ""
    try:
        info = check()
    except Exception as e:  # noqa: BLE001
        error = str(e)[:500]
    set_setting("update_last_check", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    set_setting("update_error", error)
    if info:
        set_setting("update_latest", info["latest"])
        set_setting("update_available", "1" if info["available"] else "0")
    db.session.commit()
    return info


def cached_status():
    """Dernier résultat connu, sans appeler GitHub."""
    from datetime import datetime
    last = get_setting("update_last_check", "") or ""
    latest = get_setting("update_latest", "") or ""
    return {
        "last_check": datetime.strptime(last, "%Y-%m-%d %H:%M:%S") if last else None,
        "latest": latest,
        # Recalculé à partir de VERSION : l'avis disparaît dès que la mise à jour est installée
        "available": bool(latest) and parse_version(latest) > parse_version(get_version()),
        "error": get_setting("update_error", "") or "",
    }


def _data_dir():
    from . import DATA_DIR
    return DATA_DIR


def snapshot_code():
    """Copie du programme actuel (sans data/ ni .venv) pour pouvoir revenir en arrière."""
    from datetime import datetime
    folder = os.path.join(_data_dir(), "backups")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"code-{get_version()}-{datetime.now():%Y%m%d-%H%M%S}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BASE_DIR):
            rel_root = os.path.relpath(root, BASE_DIR)
            if rel_root != "." and rel_root.split(os.sep)[0] in PROTECTED | {".claude"}:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in PROTECTED and d != ".claude"]
            for f in files:
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, BASE_DIR).replace(os.sep, "/"))
    # On garde les 5 dernières copies du programme
    snaps = sorted((f for f in os.listdir(folder) if f.startswith("code-") and f.endswith(".zip")),
                   key=lambda f: os.path.getmtime(os.path.join(folder, f)), reverse=True)
    for old in snaps[5:]:
        os.remove(os.path.join(folder, old))
    return path


def apply(zip_url, backup_fn):
    """Télécharge et installe la mise à jour. Retourne un journal texte."""
    import json
    from datetime import datetime
    log = []
    backup = backup_fn()
    log.append(f"Sauvegarde complète : {os.path.basename(backup) if backup else '—'}")
    r = requests.get(zip_url, headers=_headers(), timeout=300)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    root = names[0].split("/")[0] + "/" if names and "/" in names[0] else ""
    if not any(n == root + "VERSION" for n in names) or not any(n.startswith(root + "app/") for n in names):
        raise ValueError("L'archive ne ressemble pas au logiciel Kubios (VERSION ou app/ manquant).")
    new_version = zf.read(root + "VERSION").decode("utf-8", "ignore").strip()
    old_version = get_version()
    snap = snapshot_code()
    log.append(f"Copie du programme actuel : {os.path.basename(snap)}")
    # Marqueur lu par run.py : si la nouvelle version ne démarre pas, retour automatique à la copie
    with open(os.path.join(_data_dir(), "update_pending.json"), "w", encoding="utf-8") as f:
        json.dump({"from": old_version, "to": new_version, "snapshot": snap, "attempts": 0,
                   "at": datetime.now().isoformat(timespec="seconds")}, f)
    count = 0
    for info in zf.infolist():
        rel = info.filename[len(root):]
        if not rel or rel.split("/")[0] in PROTECTED:
            continue
        dest = os.path.normpath(os.path.join(BASE_DIR, rel))
        if not dest.startswith(os.path.normpath(BASE_DIR)):
            continue  # protection contre les chemins malicieux
        if info.is_dir():
            os.makedirs(dest, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zf.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        count += 1
    log.append(f"{count} fichiers mis à jour.")
    req = os.path.join(BASE_DIR, "requirements.txt")
    if os.path.exists(req):
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req],
                           capture_output=True, text=True, timeout=600)
        log.append("Dépendances : " + ("OK" if p.returncode == 0 else p.stderr[-500:]))
    log.append(f"Nouvelle version : {new_version} (avant : {old_version}). Redémarrage…")
    return "\n".join(log)


# ------------------------------------------------------------------ installation automatique

_installing = threading.Lock()


def auto_install(app, info=None, reason="automatique"):
    """Installe la dernière version sans intervention. Retourne un message ou None."""
    from html import escape
    from . import db
    from .helpers import audit
    from .scheduler import backup_db
    if not _installing.acquire(blocking=False):
        return None
    try:
        info = info or check_and_store()
        if not info or not info["available"]:
            return None
        if info["latest"] == (get_setting("update_skip_version", "") or ""):
            return f"version {info['latest']} ignorée (retour arrière précédent)"
        try:
            log = apply(info["zip_url"], lambda: backup_db(app, label="avant-mise-a-jour"))
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            audit("mise_a_jour_echec", "systeme", None, f"installation {reason} {info['latest']} : {e}")
            db.session.commit()
            _notify_admins(f"Échec de la mise à jour {info['latest']}",
                           f"<p>L'installation {reason} de la version <b>{info['latest']}</b> a échoué "
                           f"avant le redémarrage. La version {get_version()} reste en place.</p>"
                           f"<pre>{escape(str(e))}</pre>")
            return f"échec : {e}"
        audit("mise_a_jour_logiciel", "systeme", None, f"installation {reason} {info['current']} → {info['latest']}")
        db.session.commit()
        schedule_restart()
        return log
    finally:
        _installing.release()


def nightly_job(app):
    """Tâche horaire : installe pendant l'heure choisie si le mode « nuit » est actif."""
    from datetime import datetime
    if get_setting("update_auto_mode") != "nuit":
        return None
    try:
        hour = int(get_setting("update_install_hour") or 3)
    except ValueError:
        hour = 3
    if datetime.now().hour != hour:
        return None
    return auto_install(app, reason="automatique (nuit)")


def _notify_admins(subject, html):
    from .mailer import send_mail, wrap_html
    from .models import User
    to = [u.email for u in User.query.filter_by(role="admin", active=True).all()]
    if to:
        send_mail(to, subject, wrap_html(html))


def report_result():
    """Lit le résultat laissé par run.py après un redémarrage (réussite ou retour arrière) et avise les admins."""
    import json
    from html import escape
    from . import db
    from .helpers import audit
    from .models import set_setting
    path = os.path.join(_data_dir(), "update_result.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            res = json.load(f)
    except (OSError, ValueError):
        res = {"status": "inconnu"}
    os.remove(path)
    base = (get_setting("base_url") or "").rstrip("/")
    if res.get("status") == "ok":
        set_setting("update_skip_version", "")
        audit("mise_a_jour_reussie", "systeme", None, f"{res.get('from')} → {res.get('to')} : démarrage confirmé")
        db.session.commit()
        _notify_admins(f"Mise à jour {res.get('to')} installée",
                       f"<p>Kubios a été mis à jour de la version <b>{res.get('from')}</b> à la version "
                       f"<b>{res.get('to')}</b> et fonctionne normalement.</p>"
                       f"<p><a href='{base}/admin/mises-a-jour'>Voir les mises à jour</a></p>")
        return "réussite signalée"
    # Retour arrière : run.py a déjà écrit l'audit et update_skip_version directement dans la base
    _notify_admins(f"Mise à jour {res.get('to')} ANNULÉE — retour à {res.get('from')}",
                   f"<p style='color:#b91c1c'><b>La version {res.get('to')} n'a pas réussi à démarrer.</b></p>"
                   f"<p>Kubios est revenu automatiquement à la version <b>{res.get('from')}</b>. Vos données ne "
                   f"sont pas touchées. Cette version ne sera pas réinstallée automatiquement.</p>"
                   f"<pre style='font-size:11px;background:#f3f4f6;padding:8px'>{escape((res.get('error') or '')[-1500:])}</pre>")
    return "retour arrière signalé"


def schedule_restart(delay=2.0):
    """Quitte le processus avec le code 3 : run.py relance le serveur."""
    def _exit():
        os._exit(RESTART_EXIT_CODE)
    threading.Timer(delay, _exit).start()
