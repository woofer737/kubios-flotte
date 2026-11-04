"""Point d'entrée du logiciel Kubios.

    python run.py          -> superviseur : lance le serveur et le relance après une mise à jour ou une panne
    python run.py serve    -> serveur web seul (utilisé par le superviseur)

Variables d'environnement : KUBIOS_PORT (défaut 8080), KUBIOS_HOST (défaut 0.0.0.0).
"""
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("KUBIOS_DATA_DIR", os.path.join(BASE, "data"))
os.makedirs(DATA, exist_ok=True)


def setup_logging():
    handler = RotatingFileHandler(os.path.join(DATA, "kubios.log"), maxBytes=5_000_000, backupCount=5,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr:  # absent sous pythonw (service)
        root.addHandler(logging.StreamHandler())


# ---------------------------------------------------------------- filet de sécurité des mises à jour
# Ce code ne dépend pas du dossier app/ : il fonctionne même si la nouvelle version est brisée.
PENDING = os.path.join(DATA, "update_pending.json")
RESULT = os.path.join(DATA, "update_result.json")
MAX_FAILED_STARTS = 2
PROTECTED = {"data", ".venv", ".git", "__pycache__", ".claude"}


def _read_pending():
    import json
    try:
        with open(PENDING, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _record_in_db(skip_version, details):
    """Écrit directement dans la base (sans l'application) : audit + version à ne pas réinstaller."""
    import sqlite3
    from datetime import datetime
    db_path = os.path.join(DATA, "kubios.db")
    if not os.path.exists(db_path):
        return
    c = sqlite3.connect(db_path)
    try:
        c.execute("INSERT OR REPLACE INTO setting (key, value) VALUES ('update_skip_version', ?)", (skip_version,))
        c.execute("INSERT INTO audit_log (created_at, user_label, action, entity, details) VALUES (?, ?, ?, ?, ?)",
                  (datetime.now(), "système", "mise_a_jour_retour_arriere", "systeme", details))
        c.commit()
    except sqlite3.Error:
        logging.getLogger("kubios").exception("Impossible d'écrire le retour arrière dans la base")
    finally:
        c.close()


def rollback(pending, error_text):
    """Remet le programme tel qu'il était avant la mise à jour."""
    import zipfile
    log = logging.getLogger("kubios.securite")
    snap = pending.get("snapshot")
    log.error("La version %s ne démarre pas : retour à la version %s", pending.get("to"), pending.get("from"))
    if not snap or not os.path.exists(snap):
        log.error("Copie du programme introuvable (%s) : retour arrière impossible", snap)
        os.remove(PENDING)
        return False
    base_norm = os.path.normpath(BASE)
    with zipfile.ZipFile(snap) as z:
        for info in z.infolist():
            if info.filename.split("/")[0] in PROTECTED or info.is_dir():
                continue
            dest = os.path.normpath(os.path.join(BASE, info.filename))
            if not dest.startswith(base_norm):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as s, open(dest, "wb") as d:
                d.write(s.read())
    req = os.path.join(BASE, "requirements.txt")
    if os.path.exists(req):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req], capture_output=True, timeout=600)
    _record_in_db(pending.get("to", ""), f"Version {pending.get('to')} n'a pas démarré : retour à "
                                         f"{pending.get('from')}. Erreur : {error_text[-800:]}")
    _write_json(RESULT, {"status": "rollback", "from": pending.get("from"), "to": pending.get("to"),
                         "error": error_text[-3000:]})
    os.remove(PENDING)
    log.info("Retour arrière terminé : version %s restaurée", pending.get("from"))
    return True


def _confirm_update_ok(delay=60):
    """Après 60 s de fonctionnement normal, la mise à jour est considérée réussie."""
    import threading

    def ok():
        pending = _read_pending()
        if pending:
            _write_json(RESULT, {"status": "ok", "from": pending.get("from"), "to": pending.get("to")})
            os.remove(PENDING)
            logging.getLogger("kubios").info("Mise à jour %s confirmée", pending.get("to"))
    t = threading.Timer(delay, ok)
    t.daemon = True
    t.start()


def serve():
    setup_logging()
    import traceback
    pending = _read_pending()
    try:
        from waitress import serve as wserve
        from app import create_app
        app = create_app(start_scheduler=True)
    except Exception:  # noqa: BLE001 — n'importe quelle erreur de démarrage
        err = traceback.format_exc()
        logging.getLogger("kubios").error("Échec du démarrage :\n%s", err)
        if pending:
            pending["attempts"] = int(pending.get("attempts", 0)) + 1
            if pending["attempts"] >= MAX_FAILED_STARTS:
                rollback(pending, err)
            else:
                _write_json(PENDING, pending)
        sys.exit(1)
    if pending:
        _confirm_update_ok()
    host = os.environ.get("KUBIOS_HOST", "0.0.0.0")
    port = int(os.environ.get("KUBIOS_PORT", "8080"))
    logging.getLogger("kubios").info("Kubios v%s démarré sur http://%s:%s", app.config["APP_VERSION"], host, port)
    wserve(app, host=host, port=port, threads=8)


def supervise():
    setup_logging()
    log = logging.getLogger("kubios.superviseur")
    while True:
        log.info("Démarrage du serveur")
        code = subprocess.call([sys.executable, os.path.join(BASE, "run.py"), "serve"], cwd=BASE)
        if code == 3:
            log.info("Redémarrage demandé (mise à jour)")
            continue
        if code == 0:
            log.info("Arrêt normal")
            return
        log.warning("Le serveur s'est arrêté (code %s) — relance dans 10 s", code)
        time.sleep(10)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve()
    else:
        supervise()
