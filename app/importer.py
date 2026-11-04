"""Importation du fichier Excel de la flotte (feuilles FLOTTE, Liste véhicules SAAQ, HORS SERVICE)."""
import re
import unicodedata
from datetime import datetime, date

import openpyxl

from . import db
from .models import Vehicle, Driver


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(v.strip()[:10], fmt).date()
            except ValueError:
                pass
    return None


def to_num(v, kind=float):
    if isinstance(v, (int, float)):
        return kind(v)
    if isinstance(v, str):
        try:
            return kind(float(v.replace(" ", "").replace(",", ".").replace("$", "")))
        except ValueError:
            return None
    return None


def to_str(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s or None


def unit_key(v):
    s = to_str(v)
    if s is None:
        return None
    return s.split(".")[0] if re.fullmatch(r"\d+(\.0)?", s) else s


def _find_header(ws, must_contain, max_rows=10):
    for idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_rows, values_only=True), start=1):
        normed = [norm(c) for c in row]
        if must_contain in normed:
            return idx, normed, list(row)
    return None, None, None


# En-tête normalisé (FLOTTE) -> attribut Vehicle
FLOTTE_MAP = {
    "#unite beneva": "beneva_number",
    "# unite": "unit_number",
    "actif / inactif": "status",
    "annee": "year",
    "marque": "make",
    "modele": "model",
    "plaque": "plate",
    "serie": "vin",
    "departement": "department",
    "# km annuellement": "km_annual",
    "remarque": "ownership",
    "paiements mensuels": "monthly_payment",
    "solde payable": "balance_due",
    "financement?": "financing",
    "date de fin de garantie": "warranty_end",
}


def _clean_driver_email(email):
    e = (to_str(email) or "").strip().lower()
    if not e or e.startswith("@") or "@" not in e:
        return None
    return e


def import_workbook(path_or_file):
    wb = openpyxl.load_workbook(path_or_file, data_only=True, read_only=True)
    stats = {"crees": 0, "mis_a_jour": 0, "chauffeurs": 0, "saaq": 0, "hors_service": 0, "ignores": 0}
    sheets = {norm(n): n for n in wb.sheetnames}
    seen = {}

    def get_vehicle(unit):
        if unit in seen:
            return seen[unit]
        v = Vehicle.query.filter_by(unit_number=unit).first()
        if v is None:
            v = Vehicle(unit_number=unit)
            db.session.add(v)
            stats["crees"] += 1
        else:
            stats["mis_a_jour"] += 1
        seen[unit] = v
        return v

    # ---------------------------------------------------------- FLOTTE
    if "flotte" in sheets:
        ws = wb[sheets["flotte"]]
        hrow, headers, raw_headers = _find_header(ws, "# unite")
        if hrow:
            for row in ws.iter_rows(min_row=hrow + 1, values_only=True):
                data = dict(zip(headers, row))
                unit = unit_key(data.get("# unite"))
                if not unit:
                    stats["ignores"] += 1
                    continue
                v = get_vehicle(unit)
                extra = {}
                for h, raw_h, val in zip(headers, raw_headers, row):
                    if not h:
                        continue
                    attr = FLOTTE_MAP.get(h)
                    if attr in ("year", "km_annual"):
                        setattr(v, attr, to_num(val, int))
                    elif attr in ("monthly_payment", "balance_due"):
                        setattr(v, attr, to_num(val))
                    elif attr == "warranty_end":
                        v.warranty_end = to_date(val)
                    elif attr == "status":
                        s = (to_str(val) or "Actif").capitalize()
                        v.status = s if s in ("Actif", "Inactif") else "Actif"
                    elif attr:
                        setattr(v, attr, to_str(val))
                    elif h.startswith("date fin de bail"):
                        v.lease_end = to_date(val)
                        extra["Fin de bail / prêt"] = to_str(val)
                    elif h not in ("chauffeur", "email") and val not in (None, ""):
                        label = re.sub(r"\s+", " ", str(raw_h)).strip()
                        extra[label] = to_str(val)
                v.extra = extra
                # Chauffeur
                name = to_str(data.get("chauffeur"))
                email = _clean_driver_email(data.get("email"))
                if name:
                    drv = None
                    if email:
                        drv = Driver.query.filter(db.func.lower(Driver.email) == email).first()
                    if drv is None:
                        drv = Driver.query.filter(db.func.lower(Driver.name) == name.lower()).first()
                    if drv is None:
                        drv = Driver(name=name, email=email, department=v.department)
                        db.session.add(drv)
                        db.session.flush()
                        stats["chauffeurs"] += 1
                    elif email and not drv.email:
                        drv.email = email
                    v.driver = drv
                else:
                    v.driver = None

    # ---------------------------------------------------------- SAAQ
    saaq_name = next((sheets[k] for k in sheets if "saaq" in k), None)
    if saaq_name:
        ws = wb[saaq_name]
        hrow, headers, _ = _find_header(ws, "unite")
        if hrow:
            for row in ws.iter_rows(min_row=hrow + 1, values_only=True):
                data = dict(zip(headers, row))
                unit = unit_key(data.get("unite"))
                if not unit:
                    continue
                v = seen.get(unit) or Vehicle.query.filter_by(unit_number=unit).first()
                if v is None:
                    continue
                v.registration_expiry = to_date(data.get("expiration"))
                v.mechanical_inspection = to_date(data.get("verification mecanique"))
                v.mechanical_status = to_str(data.get("statut verif. mec."))
                v.weight = to_num(data.get("masse"), int)
                v.gvwr = to_num(data.get("pnbv"), int)
                if not v.vin:
                    v.vin = to_str(data.get("niv"))
                if not v.plate:
                    v.plate = to_str(data.get("numero plaque"))
                ex = v.extra
                if data.get("type propriete"):
                    ex["Type propriété (SAAQ)"] = to_str(data.get("type propriete"))
                v.extra = ex
                stats["saaq"] += 1

    # ---------------------------------------------------------- HORS SERVICE
    if "hors service" in sheets:
        ws = wb[sheets["hors service"]]
        hrow, headers, _ = _find_header(ws, "# unite")
        if hrow:
            for row in ws.iter_rows(min_row=hrow + 1, values_only=True):
                data = dict(zip(headers, row))
                unit = unit_key(data.get("# unite"))
                if not unit or unit in seen:
                    continue
                v = Vehicle.query.filter_by(unit_number=unit).first()
                if v is None:
                    v = Vehicle(unit_number=unit)
                    db.session.add(v)
                    stats["crees"] += 1
                v.status = "Vendu" if "vendu" in norm(data.get("remarque")) else "Hors service"
                v.make = to_str(data.get("marque"))
                v.model = to_str(data.get("modele"))
                v.year = to_num(data.get("annee"), int)
                v.plate = to_str(data.get("plaque"))
                v.vin = to_str(data.get("serie"))
                v.remarks = to_str(data.get("remarque"))
                seen[unit] = v
                stats["hors_service"] += 1

    db.session.commit()
    wb.close()
    return stats
