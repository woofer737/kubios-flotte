"""Kubios Licences — émetteur de licences pour Kubios (Groupe SRB).

Signe les licences avec la clé privée Ed25519 de Groupe SRB, rangée dans
%APPDATA%\\Kubios Licences\\cle-privee.pem. Kubios ne contient que la clé publique.
Registre des licences émises : %APPDATA%\\Kubios Licences\\licences-emises.json
"""
import base64
import calendar
import json
import os
import shutil
import sys
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from cryptography.hazmat.primitives import serialization as S
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

APP_NAME = "Kubios Licences"
VERSION = "1.0.0"
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
KEY_PATH = os.path.join(DATA_DIR, "cle-privee.pem")
REGISTRY = os.path.join(DATA_DIR, "licences-emises.json")
PREFIX = "KBL1"
NAVY, AMBER = "#1e3a5f", "#f59e0b"


# ------------------------------------------------------------------ logique

def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def end_for(start, months):
    """Fin incluse : 1 mois à partir du 6 nov. = jusqu'au 5 déc."""
    return add_months(start, months) - timedelta(days=1)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load_key():
    if not os.path.exists(KEY_PATH):
        return None
    with open(KEY_PATH, "rb") as f:
        return S.load_pem_private_key(f.read(), None)


def public_b64(key):
    return base64.b64encode(key.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)).decode()


def load_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_registry(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY)


def next_id(items, today):
    prefix = f"L-{today:%Y%m%d}-"
    n = sum(1 for i in items if i["id"].startswith(prefix)) + 1
    return f"{prefix}{n:02d}"


def sign(key, data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{PREFIX}.{b64u(payload)}.{b64u(key.sign(payload))}"


def parse_date(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Date invalide : « {s} » (format AAAA-MM-JJ)")


# ------------------------------------------------------------------ interface

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — Groupe SRB")
        self.geometry("980x680")
        self.minsize(900, 600)
        self.configure(bg="#eef2f7")
        self.key = load_key()
        self.items = load_registry()
        self._style()
        self._header()
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=14, pady=(8, 6))
        self.tab_new = ttk.Frame(nb, padding=14)
        self.tab_list = ttk.Frame(nb, padding=10)
        self.tab_key = ttk.Frame(nb, padding=14)
        nb.add(self.tab_new, text="  Nouvelle licence  ")
        nb.add(self.tab_list, text="  Licences émises  ")
        nb.add(self.tab_key, text="  Clé de signature  ")
        self._build_new()
        self._build_list()
        self._build_key()
        tk.Label(self, text=f"Propulsé par Groupe SRB — © {date.today().year}   ·   v{VERSION}", bg="#eef2f7",
                 fg="#64748b", font=("Segoe UI", 8)).pack(pady=(0, 6))
        if not self.key:
            nb.select(self.tab_key)
            self.after(300, lambda: messagebox.showwarning(
                APP_NAME, "Aucune clé de signature trouvée sur ce poste.\n\n"
                          "Importez la clé de Groupe SRB (cle-privee.pem) dans l'onglet « Clé de signature »."))

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("vista")
        except tk.TclError:
            st.theme_use("clam")
        st.configure(".", font=("Segoe UI", 10))
        st.configure("Treeview", rowheight=24)
        st.configure("H.TLabel", font=("Segoe UI", 11, "bold"), foreground=NAVY)
        st.configure("Muted.TLabel", foreground="#64748b")
        st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 8))

    def _header(self):
        h = tk.Frame(self, bg=NAVY, height=62)
        h.pack(fill="x")
        logo = tk.Label(h, text="🔑", bg=AMBER, fg=NAVY, font=("Segoe UI Emoji", 16), width=3)
        logo.pack(side="left", padx=(16, 10), pady=12)
        tk.Label(h, text="Kubios Licences", bg=NAVY, fg="white", font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(h, text="  Émission des licences Kubios", bg=NAVY, fg="#cbd5e1", font=("Segoe UI", 10)).pack(side="left")

    # ---------- onglet Nouvelle licence
    def _build_new(self):
        f = self.tab_new
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Client", style="H.TLabel").grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self.v_client = tk.StringVar()
        self.v_email = tk.StringVar()
        ttk.Label(f, text="Nom du client *").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.v_client, width=50).grid(row=r, column=1, sticky="we", pady=3); r += 1
        ttk.Label(f, text="Courriel du client").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.v_email, width=50).grid(row=r, column=1, sticky="we", pady=3); r += 1

        ttk.Label(f, text="Durée", style="H.TLabel").grid(row=r, column=0, columnspan=2, sticky="w", pady=(12, 0)); r += 1
        self.v_start = tk.StringVar(value=date.today().isoformat())
        ttk.Label(f, text="Date de début").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.v_start, width=14).grid(row=r, column=1, sticky="w", pady=3); r += 1
        self.v_dur = tk.StringVar(value="12")
        box = ttk.Frame(f)
        box.grid(row=r, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Durée").grid(row=r, column=0, sticky="w")
        for v, t in (("1", "1 mois"), ("3", "3 mois"), ("6", "6 mois"), ("12", "12 mois"), ("date", "Date précise :")):
            ttk.Radiobutton(box, text=t, value=v, variable=self.v_dur, command=self._preview).pack(side="left", padx=(0, 12))
        self.v_end = tk.StringVar(value=end_for(date.today(), 12).isoformat())
        self.e_end = ttk.Entry(box, textvariable=self.v_end, width=12)
        self.e_end.pack(side="left")
        r += 1
        self.l_period = ttk.Label(f, text="", style="Muted.TLabel")
        self.l_period.grid(row=r, column=1, sticky="w"); r += 1

        ttk.Label(f, text="Limites", style="H.TLabel").grid(row=r, column=0, columnspan=2, sticky="w", pady=(12, 0)); r += 1
        self.v_resp = tk.IntVar(value=3)
        ttk.Label(f, text="Nombre de responsables").grid(row=r, column=0, sticky="w", pady=3)
        rb = ttk.Frame(f)
        rb.grid(row=r, column=1, sticky="w")
        ttk.Spinbox(rb, from_=1, to=999, textvariable=self.v_resp, width=6).pack(side="left")
        ttk.Label(rb, text="  administrateurs + gestionnaires actifs (lecture seule illimitée)", style="Muted.TLabel").pack(side="left")
        r += 1
        self.v_inst = tk.StringVar()
        ttk.Label(f, text="ID d'installation").grid(row=r, column=0, sticky="w", pady=3)
        ib = ttk.Frame(f)
        ib.grid(row=r, column=1, sticky="we")
        ttk.Entry(ib, textvariable=self.v_inst, width=28).pack(side="left")
        ttk.Label(ib, text="  facultatif : lie la licence à un seul poste (Kubios > Administration > Licence)",
                  style="Muted.TLabel").pack(side="left")
        r += 1
        self.v_note = tk.StringVar()
        ttk.Label(f, text="Note interne").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.v_note).grid(row=r, column=1, sticky="we", pady=3); r += 1

        ttk.Button(f, text="🔑  Émettre la licence", style="Big.TButton", command=self.issue).grid(
            row=r, column=1, sticky="w", pady=(14, 8)); r += 1

        ttk.Label(f, text="Clé de licence (à envoyer au client)", style="H.TLabel").grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self.t_key = tk.Text(f, height=5, wrap="char", font=("Consolas", 9), relief="solid", borderwidth=1)
        self.t_key.grid(row=r, column=0, columnspan=2, sticky="nsew", pady=4)
        f.rowconfigure(r, weight=1); r += 1
        bb = ttk.Frame(f)
        bb.grid(row=r, column=0, columnspan=2, sticky="w")
        ttk.Button(bb, text="Copier la clé", command=self.copy_key).pack(side="left", padx=(0, 8))
        ttk.Button(bb, text="Enregistrer le fichier .lic…", command=self.save_lic).pack(side="left")
        for v in (self.v_start, self.v_end):
            v.trace_add("write", lambda *a: self._preview())
        self._preview()
        self.last = None

    def _period(self):
        start = parse_date(self.v_start.get())
        if self.v_dur.get() == "date":
            end = parse_date(self.v_end.get())
        else:
            end = end_for(start, int(self.v_dur.get()))
        if end < start:
            raise ValueError("La date de fin est avant la date de début.")
        return start, end

    def _preview(self):
        self.e_end.configure(state="normal" if self.v_dur.get() == "date" else "disabled")
        try:
            start, end = self._period()
            if self.v_dur.get() != "date":
                self.v_end.set(end.isoformat()) if self.v_end.get() != end.isoformat() else None
            self.l_period.configure(text=f"Valide du {start:%Y-%m-%d} au {end:%Y-%m-%d} inclusivement "
                                         f"({(end - start).days + 1} jours)", foreground="#64748b")
        except ValueError as e:
            self.l_period.configure(text=str(e), foreground="#b91c1c")

    def issue(self):
        if not self.key:
            messagebox.showerror(APP_NAME, "Importez d'abord la clé de signature (onglet « Clé de signature »).")
            return
        client = self.v_client.get().strip()
        if not client:
            messagebox.showerror(APP_NAME, "Le nom du client est obligatoire.")
            return
        try:
            start, end = self._period()
            resp = int(self.v_resp.get())
            if resp < 1:
                raise ValueError("Au moins 1 responsable.")
        except (ValueError, tk.TclError) as e:
            messagebox.showerror(APP_NAME, str(e) or "Valeur invalide.")
            return
        today = date.today()
        data = {"v": 1, "id": next_id(self.items, today), "client": client, "courriel": self.v_email.get().strip(),
                "debut": start.isoformat(), "fin": end.isoformat(), "responsables": resp,
                "installation": self.v_inst.get().strip().upper(), "emis": datetime.now().isoformat(timespec="seconds"),
                "note": self.v_note.get().strip()}
        lic = sign(self.key, data)
        self.items.append(dict(data, cle=lic))
        save_registry(self.items)
        self.last = dict(data, cle=lic)
        self.t_key.delete("1.0", "end")
        self.t_key.insert("1.0", lic)
        self._refresh_list()
        messagebox.showinfo(APP_NAME, f"Licence {data['id']} émise pour {client}.\n\n"
                                      f"Du {data['debut']} au {data['fin']}, {resp} responsable(s).\n\n"
                                      "Copiez la clé ou enregistrez le fichier .lic pour l'envoyer au client.")

    def copy_key(self):
        k = self.t_key.get("1.0", "end").strip()
        if k:
            self.clipboard_clear()
            self.clipboard_append(k)
            messagebox.showinfo(APP_NAME, "Clé copiée dans le presse-papiers.")

    def save_lic(self, item=None):
        item = item or self.last
        if not item:
            messagebox.showerror(APP_NAME, "Émettez d'abord une licence.")
            return
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in item["client"]).strip().replace(" ", "-")
        path = filedialog.asksaveasfilename(defaultextension=".lic", initialfile=f"kubios-{safe}-{item['fin']}.lic",
                                            filetypes=[("Licence Kubios", "*.lic")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(item["cle"] + "\n")
            messagebox.showinfo(APP_NAME, f"Fichier enregistré :\n{path}")

    # ---------- onglet Licences émises
    def _build_list(self):
        f = self.tab_list
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Rechercher :").pack(side="left")
        self.v_q = tk.StringVar()
        self.v_q.trace_add("write", lambda *a: self._refresh_list())
        ttk.Entry(top, textvariable=self.v_q, width=30).pack(side="left", padx=6)
        ttk.Button(top, text="Copier la clé", command=lambda: self._sel_action("copy")).pack(side="right")
        ttk.Button(top, text="Enregistrer .lic…", command=lambda: self._sel_action("save")).pack(side="right", padx=6)
        ttk.Button(top, text="Renouveler…", command=lambda: self._sel_action("renew")).pack(side="right")
        cols = ("id", "client", "debut", "fin", "resp", "etat", "inst", "note")
        heads = ("N°", "Client", "Début", "Fin", "Resp.", "État", "Installation", "Note")
        widths = (130, 200, 90, 90, 50, 110, 170, 160)
        self.tree = ttk.Treeview(f, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center" if c in ("debut", "fin", "resp") else "w")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("exp", foreground="#b91c1c")
        self.tree.tag_configure("soon", foreground="#b45309")
        self.tree.bind("<Double-1>", lambda e: self._sel_action("copy"))
        self._refresh_list()

    def _refresh_list(self):
        if not hasattr(self, "tree"):
            return
        q = self.v_q.get().lower().strip() if hasattr(self, "v_q") else ""
        self.tree.delete(*self.tree.get_children())
        today = date.today()
        for i, it in enumerate(reversed(self.items)):
            if q and q not in json.dumps(it, ensure_ascii=False).lower():
                continue
            left = (date.fromisoformat(it["fin"]) - today).days
            etat, tag = ("Expirée", "exp") if left < 0 else (f"{left} j restants", "soon") if left <= 30 else ("Active", "")
            self.tree.insert("", "end", iid=str(len(self.items) - 1 - i), tags=(tag,),
                             values=(it["id"], it["client"], it["debut"], it["fin"], it["responsables"], etat,
                                     it.get("installation") or "—", it.get("note", "")))

    def _sel_action(self, action):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Sélectionnez une licence dans la liste.")
            return
        it = self.items[int(sel[0])]
        if action == "copy":
            self.clipboard_clear()
            self.clipboard_append(it["cle"])
            messagebox.showinfo(APP_NAME, f"Clé de la licence {it['id']} copiée.")
        elif action == "save":
            self.save_lic(it)
        elif action == "renew":
            self.v_client.set(it["client"])
            self.v_email.set(it.get("courriel", ""))
            self.v_resp.set(it["responsables"])
            self.v_inst.set(it.get("installation", ""))
            nxt = max(date.today(), date.fromisoformat(it["fin"]) + timedelta(days=1))
            self.v_start.set(nxt.isoformat())
            self.v_note.set(f"Renouvellement de {it['id']}")
            self.nametowidget(self.tab_new.winfo_parent()).select(self.tab_new)

    # ---------- onglet Clé de signature
    def _build_key(self):
        f = self.tab_key
        for w in f.winfo_children():
            w.destroy()
        ttk.Label(f, text="Clé de signature de Groupe SRB", style="H.TLabel").pack(anchor="w")
        if self.key:
            ttk.Label(f, text=f"✔  Clé chargée : {KEY_PATH}").pack(anchor="w", pady=(8, 2))
            ttk.Label(f, text="Clé publique (celle inscrite dans Kubios) :", style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
            e = ttk.Entry(f, width=60, font=("Consolas", 10))
            e.insert(0, public_b64(self.key))
            e.configure(state="readonly")
            e.pack(anchor="w", pady=2)
        else:
            ttk.Label(f, text="✖  Aucune clé sur ce poste.", foreground="#b91c1c").pack(anchor="w", pady=8)
        msg = ("IMPORTANT — La clé privée est ce qui permet de signer les licences.\n"
               "•  Gardez une copie de sauvegarde en lieu sûr (clé USB, coffre de mots de passe). Si elle est perdue, "
               "vous ne pourrez plus émettre de licences acceptées par les Kubios déjà installés.\n"
               "•  Ne l'envoyez jamais à un client et ne la mettez pas sur GitHub.\n"
               "•  Pour utiliser ce logiciel sur un autre ordinateur : « Exporter une copie » ici, puis « Importer » là-bas.")
        ttk.Label(f, text=msg, wraplength=820, justify="left").pack(anchor="w", pady=14)
        b = ttk.Frame(f)
        b.pack(anchor="w")
        ttk.Button(b, text="Importer une clé (.pem)…", command=self.import_key).pack(side="left", padx=(0, 8))
        if self.key:
            ttk.Button(b, text="Exporter une copie de sauvegarde…", command=self.export_key).pack(side="left", padx=(0, 8))
            ttk.Button(b, text="Ouvrir le dossier", command=lambda: os.startfile(DATA_DIR)).pack(side="left")
        else:
            ttk.Button(b, text="Créer une NOUVELLE clé…", command=self.new_key).pack(side="left")

    def import_key(self):
        p = filedialog.askopenfilename(filetypes=[("Clé privée", "*.pem"), ("Tous", "*.*")])
        if not p:
            return
        try:
            with open(p, "rb") as fh:
                k = S.load_pem_private_key(fh.read(), None)
            if not isinstance(k, Ed25519PrivateKey):
                raise ValueError
        except Exception:  # noqa: BLE001
            messagebox.showerror(APP_NAME, "Ce fichier n'est pas une clé de signature Kubios valide.")
            return
        if self.key and public_b64(self.key) != public_b64(k):
            if not messagebox.askyesno(APP_NAME, "Une autre clé est déjà installée. La remplacer ?\n\n"
                                                 "(L'ancienne est conservée en .bak)"):
                return
            shutil.copy2(KEY_PATH, KEY_PATH + f".{datetime.now():%Y%m%d%H%M%S}.bak")
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copy2(p, KEY_PATH)
        self.key = k
        self._build_key()
        messagebox.showinfo(APP_NAME, "Clé importée.")

    def export_key(self):
        p = filedialog.asksaveasfilename(defaultextension=".pem", initialfile="kubios-cle-privee-SAUVEGARDE.pem",
                                         filetypes=[("Clé privée", "*.pem")])
        if p:
            shutil.copy2(KEY_PATH, p)
            messagebox.showinfo(APP_NAME, f"Copie enregistrée :\n{p}\n\nRangez-la en lieu sûr.")

    def new_key(self):
        if not messagebox.askyesno(APP_NAME, "Créer une nouvelle clé ?\n\nLes Kubios déjà installés n'accepteront PAS "
                                             "les licences signées avec cette nouvelle clé, sauf si sa clé publique est "
                                             "ajoutée dans Kubios (fichier app/licence.py) et publiée en mise à jour.\n\n"
                                             "Normalement, il faut plutôt IMPORTER la clé existante."):
            return
        k = Ed25519PrivateKey.generate()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(KEY_PATH, "wb") as fh:
            fh.write(k.private_bytes(S.Encoding.PEM, S.PrivateFormat.PKCS8, S.NoEncryption()))
        self.key = k
        self._build_key()


def cli_sign(args):
    """Mode ligne de commande (tests) : kubios_licences.py --signer client debut fin responsables [installation]"""
    key = load_key()
    client, debut, fin, resp = args[:4]
    data = {"v": 1, "id": "L-TEST", "client": client, "courriel": "", "debut": debut, "fin": fin,
            "responsables": int(resp), "installation": args[4] if len(args) > 4 else "",
            "emis": datetime.now().isoformat(timespec="seconds"), "note": "test"}
    print(sign(key, data))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--signer":
        cli_sign(sys.argv[2:])
    else:
        App().mainloop()
