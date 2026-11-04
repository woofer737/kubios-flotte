"""Sauvegarde complète et restauration.

Une sauvegarde est un fichier .zip qui contient :
  - kubios.db   : toute la base (véhicules, outils, bons de travail, utilisateurs, audit,
                  ET tous les paramètres : courriel Microsoft 365, rappels, GitHub, avertissement…)
  - uploads/    : photos et pièces jointes des bons de travail
  - secret.key  : clé de session du logiciel
  - info.txt    : version et date
Les anciennes sauvegardes .db (v1.0) peuvent aussi être restaurées.
"""
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from flask import current_app

from . import DATA_DIR, get_version

KEEP = 30


def _copy_sqlite(src, dest):
    """Copie cohérente d'une base SQLite (connexions fermées explicitement : requis sous Windows)."""
    s, d = sqlite3.connect(src), sqlite3.connect(dest)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
REQUIRED_TABLES = {"user", "setting", "vehicle", "work_order"}


def _cfg(app=None):
    return (app or current_app).config


def _db_path(app=None):
    return _cfg(app)["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")


def list_backups(app=None):
    d = _cfg(app)["BACKUP_DIR"]
    files = [f for f in os.listdir(d) if f.startswith("kubios-") and f.endswith((".zip", ".db"))]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    return [(f, os.path.getsize(os.path.join(d, f)), datetime.fromtimestamp(os.path.getmtime(os.path.join(d, f))))
            for f in files]


def create_backup(app=None, label=None):
    cfg = _cfg(app)
    src = _db_path(app)
    if not os.path.exists(src):
        return None
    name = f"kubios-{datetime.now():%Y%m%d-%H%M%S}" + (f"-{label}" if label else "") + ".zip"
    dest = os.path.join(cfg["BACKUP_DIR"], name)
    tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp_db.close()
    try:
        _copy_sqlite(src, tmp_db.name)  # copie cohérente même pendant l'utilisation
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db.name, "kubios.db")
            up = cfg["UPLOAD_DIR"]
            for f in os.listdir(up):
                p = os.path.join(up, f)
                if os.path.isfile(p):
                    z.write(p, f"uploads/{f}")
            key = os.path.join(DATA_DIR, "secret.key")
            if os.path.exists(key):
                z.write(key, "secret.key")
            z.writestr("info.txt", f"Kubios {get_version()}\nSauvegarde du {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    finally:
        os.unlink(tmp_db.name)
    _prune(cfg["BACKUP_DIR"])
    return dest


def _prune(folder):
    files = [f for f in os.listdir(folder) if f.startswith("kubios-") and f.endswith((".zip", ".db"))]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(folder, f)), reverse=True)
    for old in files[KEEP:]:
        os.remove(os.path.join(folder, old))


def _validate_db(path):
    c = None
    try:
        c = sqlite3.connect(path)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES <= tables:
            raise ValueError("Ce fichier n'est pas une base Kubios (tables manquantes).")
        if c.execute("SELECT count(*) FROM user").fetchone()[0] == 0:
            raise ValueError("La sauvegarde ne contient aucun utilisateur.")
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("La base de la sauvegarde est corrompue.")
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Fichier de base de données invalide : {e}")
    finally:
        if c is not None:
            c.close()


def restore_backup(path, app=None):
    """Restaure une sauvegarde (.zip ou .db). Fait d'abord une sauvegarde de sécurité de l'état actuel.
    Retourne le nom de la sauvegarde de sécurité. Le serveur doit ensuite redémarrer."""
    from . import db
    cfg = _cfg(app)
    work = tempfile.mkdtemp(prefix="kubios-restore-")
    try:
        has_uploads, key_data = False, None
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if "kubios.db" not in names:
                    raise ValueError("Le fichier .zip ne contient pas kubios.db.")
                with z.open("kubios.db") as s, open(os.path.join(work, "kubios.db"), "wb") as d:
                    shutil.copyfileobj(s, d)
                os.makedirs(os.path.join(work, "uploads"))
                for n in names:
                    if n.startswith("uploads/") and not n.endswith("/"):
                        has_uploads = True
                        base = os.path.basename(n)  # jamais de chemin : protection contre les fichiers malicieux
                        if base:
                            with z.open(n) as s, open(os.path.join(work, "uploads", base), "wb") as d:
                                shutil.copyfileobj(s, d)
                if "secret.key" in names:
                    key_data = z.read("secret.key").decode("utf-8", "ignore").strip()
        else:
            shutil.copyfile(path, os.path.join(work, "kubios.db"))
        new_db = os.path.join(work, "kubios.db")
        _validate_db(new_db)

        safety = create_backup(app, label="avant-restauration")

        # Remplacement de la base en place
        db.session.remove()
        db.engine.dispose()
        _copy_sqlite(new_db, _db_path(app))
        # Pièces jointes
        if has_uploads:
            up = cfg["UPLOAD_DIR"]
            for f in os.listdir(up):
                p = os.path.join(up, f)
                if os.path.isfile(p):
                    os.remove(p)
            for f in os.listdir(os.path.join(work, "uploads")):
                shutil.copy2(os.path.join(work, "uploads", f), os.path.join(up, f))
        if key_data and len(key_data) >= 32:
            with open(os.path.join(DATA_DIR, "secret.key"), "w", encoding="utf-8") as f:
                f.write(key_data)
        return os.path.basename(safety) if safety else None
    finally:
        shutil.rmtree(work, ignore_errors=True)
