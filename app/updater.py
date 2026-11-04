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


def apply(zip_url, backup_fn):
    """Télécharge et installe la mise à jour. Retourne un journal texte."""
    log = []
    backup = backup_fn()
    log.append(f"Sauvegarde de la base : {backup}")
    r = requests.get(zip_url, headers=_headers(), timeout=300)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    root = names[0].split("/")[0] + "/" if names and "/" in names[0] else ""
    if not any(n == root + "VERSION" for n in names) or not any(n.startswith(root + "app/") for n in names):
        raise ValueError("L'archive ne ressemble pas au logiciel Kubios (VERSION ou app/ manquant).")
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
    log.append(f"Nouvelle version : {get_version()}. Redémarrage…")
    return "\n".join(log)


def schedule_restart(delay=2.0):
    """Quitte le processus avec le code 3 : run.py relance le serveur."""
    def _exit():
        os._exit(RESTART_EXIT_CODE)
    threading.Timer(delay, _exit).start()
