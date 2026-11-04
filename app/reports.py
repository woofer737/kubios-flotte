"""Rapports : listing de la flotte, coûts de réparation par camion, détail des réparations,
inventaire des outils. Affichage à l'écran, téléchargement PDF / Excel et envoi par courriel."""
import io
import os
from dataclasses import dataclass, field
from datetime import date, datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user

from . import db
from .helpers import audit, editor_required, fmt_date, fmt_money
from .models import (Vehicle, WorkOrder, Tool, User, VEHICLE_STATUSES, WO_CLOSED, TOOL_STATUS_LABELS,
                     get_setting)

bp = Blueprint("reports", __name__)


# ------------------------------------------------------------------ structure commune

@dataclass
class Col:
    label: str
    width: float = 1.0          # largeur relative
    kind: str = "text"          # text / int / money / date
    align: str = "L"


@dataclass
class Report:
    key: str
    title: str
    subtitle: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)      # (style, valeurs) ; style : None / group / subtotal / total
    summary: list = field(default_factory=list)   # [(libellé, valeur affichée)]

    def add(self, values, style=None):
        self.rows.append((style, values))


def fmt_cell(value, kind):
    if value is None or value == "":
        return ""
    if kind == "money":
        return fmt_money(value)
    if kind == "date":
        return fmt_date(value)
    if kind == "int":
        return f"{value:,}".replace(",", " ")
    return str(value)


def _unit_key(v):
    return (int(v.unit_number) if v.unit_number.isdigit() else 10**9, v.unit_number)


def _vehicle_desc(v):
    return " ".join(str(p) for p in (v.year, v.make, v.model) if p)


def _parse_date(s, default):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else default
    except ValueError:
        return default


def _wo_date(w):
    """Date retenue pour un coût : fermeture si le bon est fermé, sinon ouverture."""
    return (w.closed_at or w.created_at).date()


def _status_filter(query, statut):
    if statut == "en_service":
        return query.filter(Vehicle.status.in_(["Actif", "Inactif"]))
    if statut in VEHICLE_STATUSES:
        return query.filter(Vehicle.status == statut)
    return query


STATUT_CHOICES = [("en_service", "Actifs et inactifs"), ("Actif", "Actifs seulement")] + \
    [(s, s) for s in VEHICLE_STATUSES if s != "Actif"] + [("tous", "Tous (incluant vendus)")]
STATUT_LABELS = dict(STATUT_CHOICES)


def _period(args):
    today = date.today()
    start = _parse_date(args.get("debut"), date(today.year, 1, 1))
    end = _parse_date(args.get("fin"), today)
    if end < start:
        start, end = end, start
    return start, end


# ------------------------------------------------------------------ rapports

def report_fleet(args):
    statut = args.get("statut", "en_service")
    dept = args.get("dept", "")
    q = _status_filter(Vehicle.query, statut)
    if dept:
        q = q.filter(Vehicle.department == dept)
    vehicles = sorted(q.all(), key=_unit_key)
    r = Report("flotte", "Listing de la flotte",
               " · ".join(x for x in [STATUT_LABELS.get(statut, statut), dept and f"Département : {dept}"] if x))
    r.columns = [Col("Unité", 0.6), Col("Année", 0.55, "text", "C"), Col("Marque", 1), Col("Modèle", 1.5),
                 Col("Plaque", 0.85), Col("Département", 1.1), Col("Chauffeur", 1.5), Col("Statut", 0.85),
                 Col("Km", 0.8, "int", "R"), Col("Immatriculation", 1, "date"), Col("Vérif. méc.", 1, "date"),
                 Col("Propriété", 1), Col("Paiement mensuel", 0.95, "money", "R")]
    total_monthly = 0
    for v in vehicles:
        r.add([v.unit_number, v.year, v.make, v.model, v.plate, v.department, v.driver.name if v.driver else "",
               v.status, v.km_current, v.registration_expiry, v.mechanical_inspection, v.ownership,
               v.monthly_payment])
        total_monthly += v.monthly_payment or 0
    r.add(["Total", "", f"{len(vehicles)} véhicules"] + [""] * 9 + [total_monthly or None], "total")
    r.summary = [("Véhicules", str(len(vehicles))), ("Paiements mensuels", fmt_money(total_monthly))]
    return r


def _period_workorders(start, end):
    wos = WorkOrder.query.filter(WorkOrder.status != "annule").all()
    return [w for w in wos if start <= _wo_date(w) <= end]


def report_costs(args):
    start, end = _period(args)
    statut = args.get("statut", "tous")
    only_with = args.get("avec_couts", "1") == "1"
    tri = args.get("tri", "cout")
    vehicles = {v.id: v for v in _status_filter(Vehicle.query, statut).all()}
    stats = {vid: {"n": 0, "open": 0, "veh": 0.0, "tools": 0.0, "last": None} for vid in vehicles}
    for w in _period_workorders(start, end):
        if w.kind == "outil":
            vid, bucket = (w.tool.vehicle_id if w.tool else None), "tools"
        else:
            vid, bucket = w.vehicle_id, "veh"
        if vid not in stats:
            continue
        s = stats[vid]
        s["n"] += 1
        s["open"] += 0 if w.status in WO_CLOSED else 1
        s[bucket] += w.cost or 0
        d = _wo_date(w)
        s["last"] = max(s["last"], d) if s["last"] else d
    items = [(vehicles[vid], s) for vid, s in stats.items() if not only_with or s["n"]]
    if tri == "unite":
        items.sort(key=lambda x: _unit_key(x[0]))
    else:
        items.sort(key=lambda x: (-(x[1]["veh"] + x[1]["tools"]), -x[1]["n"], _unit_key(x[0])))

    r = Report("couts", "Coûts de réparation par camion",
               f"Du {fmt_date(start)} au {fmt_date(end)} · {STATUT_LABELS.get(statut, statut)}")
    r.columns = [Col("Unité", 0.6), Col("Véhicule", 2.2), Col("Département", 1.2), Col("Chauffeur", 1.6),
                 Col("Bons", 0.55, "int", "R"), Col("Ouverts", 0.65, "int", "R"),
                 Col("Réparations véhicule", 1.15, "money", "R"), Col("Réparations outils", 1.1, "money", "R"),
                 Col("Total", 1.1, "money", "R"), Col("Dernière réparation", 1.1, "date")]
    tot = {"n": 0, "open": 0, "veh": 0.0, "tools": 0.0}
    for v, s in items:
        r.add([v.unit_number, _vehicle_desc(v), v.department, v.driver.name if v.driver else "", s["n"], s["open"],
               s["veh"], s["tools"], s["veh"] + s["tools"], s["last"]])
        for k in tot:
            tot[k] += s[k]
    grand = tot["veh"] + tot["tools"]
    r.add(["Total", f"{len(items)} véhicules", "", "", tot["n"], tot["open"], tot["veh"], tot["tools"], grand, None],
          "total")
    with_cost = [s["veh"] + s["tools"] for _, s in items if s["veh"] + s["tools"]]
    r.summary = [("Coût total", fmt_money(grand)), ("Bons de travail", str(tot["n"])),
                 ("Camions avec réparations", str(sum(1 for _, s in items if s["n"]))),
                 ("Moyenne par camion (avec coût)", fmt_money(grand / len(with_cost)) if with_cost else "—")]
    return r


def report_repairs(args):
    start, end = _period(args)
    unit = args.get("unite", "")
    wos = [w for w in _period_workorders(start, end) if w.kind == "vehicule" and w.vehicle]
    if unit:
        wos = [w for w in wos if w.vehicle.unit_number == unit]
    groups = {}
    for w in wos:
        groups.setdefault(w.vehicle, []).append(w)
    r = Report("reparations", "Détail des réparations par camion",
               f"Du {fmt_date(start)} au {fmt_date(end)}" + (f" · Unité {unit}" if unit else ""))
    r.columns = [Col("Bon", 1.05), Col("Ouvert le", 0.95, "date"), Col("Fermé le", 0.95, "date"),
                 Col("Description", 3.2), Col("Garage / atelier", 1.5), Col("Statut", 0.95),
                 Col("Facture", 0.85), Col("Coût", 0.95, "money", "R")]
    grand = 0
    for v in sorted(groups, key=_unit_key):
        ws = sorted(groups[v], key=lambda w: w.created_at)
        driver = f" · {v.driver.name}" if v.driver else ""
        r.add([f"Unité {v.label}{driver}"], "group")
        sub = 0
        for w in ws:
            r.add([w.number, w.created_at.date(), w.closed_at.date() if w.closed_at else None, w.title,
                   w.garage.name if w.garage else "", w.status_label, w.invoice_number, w.cost])
            sub += w.cost or 0
        r.add(["", "", "", f"Sous-total unité {v.unit_number} ({len(ws)} bon{'s' if len(ws) > 1 else ''})",
               "", "", "", sub], "subtotal")
        grand += sub
    r.add(["Total", "", "", f"{len(wos)} bons de travail, {len(groups)} camions", "", "", "", grand], "total")
    r.summary = [("Coût total", fmt_money(grand)), ("Bons de travail", str(len(wos))), ("Camions", str(len(groups)))]
    return r


def report_tools(args):
    lieu = args.get("lieu", "")
    tools = Tool.query.filter(Tool.status != "retire").all() if args.get("retires") != "1" else Tool.query.all()
    groups = {}
    for t in tools:
        groups.setdefault(t.vehicle, []).append(t)
    keys = sorted([v for v in groups if v], key=_unit_key) + ([None] if None in groups else [])
    if lieu == "entrepot":
        keys = [k for k in keys if k is None]
    elif lieu:
        keys = [k for k in keys if k and k.unit_number == lieu]
    r = Report("outils", "Inventaire des outils par camion",
               "Entrepôt seulement" if lieu == "entrepot" else (f"Unité {lieu}" if lieu else "Tous les emplacements"))
    r.columns = [Col("Étiquette", 0.9), Col("Outil", 2.4), Col("Catégorie", 1.2), Col("Marque / modèle", 1.6),
                 Col("N° de série", 1.2), Col("Qté", 0.45, "int", "R"), Col("État", 1), Col("Valeur", 0.9, "money", "R")]
    grand_n, grand_v = 0, 0
    for k in keys:
        ts = sorted(groups[k], key=lambda t: (t.category.name if t.category else "", t.name))
        r.add([f"Camion {k.label}" + (f" · {k.driver.name}" if k.driver else "") if k else "Entrepôt / autre"], "group")
        sub = 0
        for t in ts:
            val = (t.value or 0) * (t.quantity or 1) if t.value is not None else None
            r.add([t.asset_tag, t.name, t.category.name if t.category else "",
                   " ".join(x for x in (t.brand, t.model) if x), t.serial, t.quantity,
                   TOOL_STATUS_LABELS.get(t.status, t.status), val])
            sub += val or 0
        r.add(["", f"Sous-total ({len(ts)} articles)", "", "", "", sum(t.quantity or 1 for t in ts), "", sub],
              "subtotal")
        grand_n += len(ts)
        grand_v += sub
    r.add(["Total", f"{grand_n} articles", "", "", "", None, "", grand_v], "total")
    r.summary = [("Articles", str(grand_n)), ("Valeur totale", fmt_money(grand_v))]
    return r


REPORTS = {
    "flotte": ("Listing de la flotte", "bi-truck-front",
               "Tous les camions : plaque, chauffeur, kilométrage, échéances SAAQ, propriété et paiements.",
               report_fleet),
    "couts": ("Coûts de réparation par camion", "bi-cash-coin",
              "Total des réparations (véhicule et outils) par camion pour une période, avec le nombre de bons.",
              report_costs),
    "reparations": ("Détail des réparations par camion", "bi-list-check",
                    "Chaque bon de travail de la période, regroupé par camion, avec garage, facture et coût.",
                    report_repairs),
    "outils": ("Inventaire des outils par camion", "bi-tools",
               "Les outils de chaque camion et de l'entrepôt, avec état et valeur.", report_tools),
}


def build(key, args):
    if key not in REPORTS:
        abort(404)
    return REPORTS[key][3](args)


# ------------------------------------------------------------------ PDF

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def to_pdf(r):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    company = get_setting("company_name") or "Kubios"
    generated = f"Généré le {fmt_date(date.today())} à {datetime.now():%H:%M} par {current_user.name}" \
        if current_user and current_user.is_authenticated else f"Généré le {fmt_date(date.today())}"

    class PDF(FPDF):
        def header(self):
            self.set_font(FONT, "B", 13)
            self.set_text_color(30, 58, 95)
            self.cell(0, 6, clean(r.title), new_x="LMARGIN", new_y="NEXT")
            self.set_font(FONT, "", 8.5)
            self.set_text_color(100, 100, 100)
            self.cell(self.epw / 2, 5, clean(f"{company}" + (f" · {r.subtitle}" if r.subtitle else "")))
            self.cell(self.epw / 2, 5, clean(generated), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_draw_color(30, 58, 95)
            self.set_line_width(0.4)
            self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
            self.ln(4)
            self.set_text_color(0, 0, 0)

        def footer(self):
            self.set_y(-10)
            self.set_font(FONT, "", 7.5)
            self.set_text_color(130, 130, 130)
            self.cell(self.epw / 2, 5, clean(f"Kubios — {r.title}"), align="L")
            self.cell(self.epw / 2, 5, f"Page {self.page_no()} / {{nb}}", align="R")

    pdf = PDF(orientation="L", unit="mm", format="Letter")
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(True, margin=14)
    arial = os.path.join(FONT_DIR, "arial.ttf")
    if os.path.exists(arial) and os.path.exists(os.path.join(FONT_DIR, "arialbd.ttf")):
        pdf.add_font("arial", "", arial)
        pdf.add_font("arial", "B", os.path.join(FONT_DIR, "arialbd.ttf"))
        FONT, clean = "arial", (lambda s: s)
    else:  # police intégrée : caractères latin-1 seulement
        FONT = "helvetica"
        clean = (lambda s: str(s).replace("—", "-").replace("’", "'").replace("·", "-").replace("œ", "oe")
                 .encode("latin-1", "replace").decode("latin-1"))
    pdf.add_page()

    if r.summary:
        pdf.set_font(FONT, "", 8)
        pdf.set_fill_color(241, 245, 249)
        w = min(60, pdf.epw / len(r.summary))
        for label, _ in r.summary:
            pdf.cell(w, 4.5, clean(label), fill=True)
        pdf.ln()
        pdf.set_font(FONT, "B", 11)
        for _, value in r.summary:
            pdf.cell(w, 6.5, clean(value), fill=True)
        pdf.ln(9)

    ncol = len(r.columns)
    head = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=(30, 58, 95))
    styles = {"group": FontFace(emphasis="BOLD", fill_color=(226, 232, 240)),
              "subtotal": FontFace(emphasis="BOLD", fill_color=(248, 250, 252)),
              "total": FontFace(emphasis="BOLD", fill_color=(254, 243, 199))}
    pdf.set_font(FONT, "", 7.5)
    with pdf.table(col_widths=tuple(c.width for c in r.columns), text_align=tuple(c.align for c in r.columns),
                   headings_style=head, line_height=4.2, padding=(0.8, 1.2), borders_layout="HORIZONTAL_LINES",
                   first_row_as_headings=True, repeat_headings=1) as table:
        hr = table.row()
        for c in r.columns:
            hr.cell(clean(c.label), align=c.align if c.align != "L" else None)
        for style, values in r.rows:
            row = table.row()
            st = styles.get(style)
            if style == "group":
                row.cell(clean(values[0]), colspan=ncol, style=st, align="L")
                continue
            for c, v in zip(r.columns, values):
                row.cell(clean(fmt_cell(v, c.kind)), style=st)
    return bytes(pdf.output())


# ------------------------------------------------------------------ Excel

def to_xlsx(r):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = r.key[:30]
    ws["A1"] = r.title
    ws["A1"].font = Font(bold=True, size=14, color="1E3A5F")
    ws["A2"] = " · ".join(x for x in [get_setting("company_name") or "Kubios", r.subtitle,
                                       f"généré le {datetime.now():%Y-%m-%d %H:%M}"] if x)
    ws["A2"].font = Font(italic=True, color="666666")
    hdr_row = 4
    for i, c in enumerate(r.columns, 1):
        cell = ws.cell(hdr_row, i, c.label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E3A5F")
        cell.alignment = Alignment(horizontal={"L": "left", "R": "right", "C": "center"}[c.align], wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = max(8, c.width * 11)
    fills = {"group": "E2E8F0", "subtotal": "F8FAFC", "total": "FEF3C7"}
    row_i = hdr_row
    for style, values in r.rows:
        row_i += 1
        for i, (c, v) in enumerate(zip(r.columns, values), 1):
            cell = ws.cell(row_i, i, v if v != "" else None)
            if c.kind == "money" and isinstance(v, (int, float)):
                cell.number_format = '#,##0.00 $'
            elif c.kind == "date" and v:
                cell.number_format = "yyyy-mm-dd"
            if style:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor=fills[style])
        if style == "group":
            for i in range(2, len(r.columns) + 1):
                ws.cell(row_i, i).fill = PatternFill("solid", fgColor=fills[style])
    ws.freeze_panes = ws.cell(hdr_row + 1, 1)
    ws.auto_filter.ref = f"A{hdr_row}:{get_column_letter(len(r.columns))}{hdr_row}"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def filename(r, ext):
    return f"kubios-{r.key}-{date.today():%Y-%m-%d}.{ext}"


MIME = {"pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def render_file(r, fmt):
    return to_pdf(r) if fmt == "pdf" else to_xlsx(r)


# ------------------------------------------------------------------ écrans

FILTER_KEYS = ("statut", "dept", "debut", "fin", "avec_couts", "tri", "unite", "lieu", "retires")


def _args(source):
    return {k: source.get(k) for k in FILTER_KEYS if source.get(k) not in (None, "")}


@bp.route("/rapports")
@login_required
def index():
    return render_template("reports/index.html", reports=REPORTS)


@bp.route("/rapports/<key>")
@login_required
def view(key):
    r = build(key, request.args)
    args = _args(request.args)
    start, end = _period(request.args)
    units = sorted(Vehicle.query.filter(Vehicle.status != "Vendu").all(), key=_unit_key)
    depts = sorted({d for (d,) in db.session.query(Vehicle.department).distinct() if d})
    users = User.query.filter(User.active.is_(True)).order_by(User.name).all()
    return render_template("reports/view.html", r=r, key=key, args=args, fmt_cell=fmt_cell,
                           statut_choices=STATUT_CHOICES, units=units, depts=depts, users=users,
                           start=start, end=end, default_statut="tous" if key == "couts" else "en_service")


@bp.route("/rapports/<key>/<fmt>")
@login_required
def download(key, fmt):
    if fmt not in MIME:
        abort(404)
    r = build(key, request.args)
    audit("telechargement", "rapport", None, f"{r.title} ({fmt.upper()}) {r.subtitle}")
    db.session.commit()
    return send_file(io.BytesIO(render_file(r, fmt)), mimetype=MIME[fmt], as_attachment=fmt == "xlsx",
                     download_name=filename(r, fmt))


@bp.route("/rapports/<key>/envoyer", methods=["POST"])
@login_required
@editor_required
def send(key):
    from .mailer import send_mail, wrap_html
    from markupsafe import escape

    args = _args(request.form)
    r = build(key, request.form)
    to = [a.strip() for a in request.form.get("to", "").replace(";", ",").split(",") if "@" in a]
    fmts = [f for f in ("pdf", "xlsx") if request.form.get(f"joindre_{f}")] or ["pdf"]
    if not to:
        flash("Indiquez au moins une adresse courriel.", "danger")
        return redirect(url_for("reports.view", key=key, **args))
    note = request.form.get("message", "").strip()
    rows = "".join(f"<tr><td style='padding:2px 12px 2px 0;color:#666'>{escape(k)}</td><td><b>{escape(v)}</b></td></tr>"
                   for k, v in r.summary)
    body = (f"<p>Bonjour,</p><p>Voici le rapport <b>{escape(r.title)}</b>"
            + (f" ({escape(r.subtitle)})" if r.subtitle else "") + ", en pièce jointe.</p>"
            + (f"<p style='white-space:pre-wrap'>{escape(note)}</p>" if note else "")
            + (f"<table style='font-size:14px'>{rows}</table>" if rows else "")
            + f"<p style='color:#666;font-size:12px'>Envoyé par {escape(current_user.name)} depuis Kubios.</p>")
    files = [(filename(r, f), render_file(r, f), MIME[f]) for f in fmts]
    ok = send_mail(to, r.title, wrap_html(body), attachments=files)
    audit("envoi", "rapport", None, f"{r.title} {r.subtitle} -> {', '.join(to)} ({'/'.join(fmts)})"
          + ("" if ok else " ÉCHEC"))
    db.session.commit()
    if ok:
        flash(f"Rapport envoyé à {', '.join(to)}.", "success")
    else:
        flash("L'envoi a échoué. Voir Administration > Journal des courriels.", "danger")
    return redirect(url_for("reports.view", key=key, **args))
