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


def serve():
    setup_logging()
    from waitress import serve as wserve
    from app import create_app
    app = create_app(start_scheduler=True)
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
