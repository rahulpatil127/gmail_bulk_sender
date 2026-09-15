import base64
import os
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import mimetypes
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
]
TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"
DEFAULT_RANGE = "Sheet1!A:D"


def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(
                "credentials.json not found. Download a Desktop OAuth client from Google Cloud and put it beside app.py."
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def extract_sheet_id(value):
    value = value.strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
    return m.group(1) if m else value


def get_rows(service, spreadsheet_id, range_name):
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_name
    ).execute()
    values = result.get("values", [])
    if not values:
        return []
    headers = [str(x).strip().lower() for x in values[0]]
    required = ["email", "subject", "body"]
    missing = [h for h in required if h not in headers]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing) + ". Required: Email, Subject, Body, Status")
    idx = {h: headers.index(h) for h in headers}
    rows = []
    for sheet_row, raw in enumerate(values[1:], start=2):
        def cell(name):
            i = idx[name]
            return str(raw[i]).strip() if i < len(raw) else ""
        status = cell("status") if "status" in idx else ""
        rows.append({
            "sheet_row": sheet_row,
            "email": cell("email"),
            "subject": cell("subject"),
            "body": cell("body"),
            "status": status,
        })
    return rows


def send_one(gmail, to, subject, body, attachment_path=None):
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment_path:
        with open(attachment_path, "rb") as f:
            data = f.read()
        mime_type, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(attachment_path))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail.users().messages().send(userId="me", body={"raw": raw}).execute()


class App:
    def __init__(self, root):
        self.root = root
        root.title("Gmail Bulk Sender")
        root.geometry("760x600")
        root.minsize(700, 520)
        self.service = None
        self.gmail = None
        self.rows = []
        self.running = False
        self.attach_resume_var = tk.BooleanVar(value=False)
        self.resume_path_var = tk.StringVar()

        pad = {"padx": 10, "pady": 6}
        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Gmail Bulk Sender", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Send individual Gmail messages from rows in Google Sheets.").pack(anchor="w", pady=(0, 12))

        form = ttk.Frame(frame)
        form.pack(fill="x")
        ttk.Label(form, text="Google Sheet URL / ID").grid(row=0, column=0, sticky="w", **pad)
        self.sheet_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.sheet_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(form, text="Range").grid(row=1, column=0, sticky="w", **pad)
        self.range_var = tk.StringVar(value=DEFAULT_RANGE)
        ttk.Entry(form, textvariable=self.range_var).grid(row=1, column=1, sticky="ew", **pad)
        form.columnconfigure(1, weight=1)

        attach_frame = ttk.Frame(frame)
        attach_frame.pack(fill="x", pady=(2, 4))
        ttk.Checkbutton(attach_frame, text="Attach resume to every email", variable=self.attach_resume_var, command=self.toggle_resume).pack(side="left", padx=5)
        self.resume_entry = ttk.Entry(attach_frame, textvariable=self.resume_path_var, state="disabled")
        self.resume_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.browse_btn = ttk.Button(attach_frame, text="Browse Resume", command=self.browse_resume, state="disabled")
        self.browse_btn.pack(side="left", padx=5)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=8)
        self.connect_btn = ttk.Button(buttons, text="1. Connect Gmail", command=self.connect)
        self.connect_btn.pack(side="left", padx=5)
        self.load_btn = ttk.Button(buttons, text="2. Load Sheet", command=self.load_sheet, state="disabled")
        self.load_btn.pack(side="left", padx=5)
        self.send_btn = ttk.Button(buttons, text="3. SEND", command=self.confirm_send, state="disabled")
        self.send_btn.pack(side="left", padx=5)

        self.info = tk.StringVar(value="Not connected")
        ttk.Label(frame, textvariable=self.info).pack(anchor="w", pady=5)

        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(fill="x", pady=5)
        self.count = tk.StringVar(value="0 loaded")
        ttk.Label(frame, textvariable=self.count).pack(anchor="w")

        log_frame = ttk.Frame(frame)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(log_frame, height=18, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

        ttk.Label(frame, text="Sheet columns: Email | Subject | Body | Status. Rows with Status=Sent are skipped.", foreground="#555").pack(anchor="w", pady=(8, 0))

    def toggle_resume(self):
        state = "normal" if self.attach_resume_var.get() else "disabled"
        self.resume_entry.config(state=state)
        self.browse_btn.config(state=state)
        if self.attach_resume_var.get() and not self.resume_path_var.get():
            self.browse_resume()

    def browse_resume(self):
        path = filedialog.askopenfilename(
            title="Select resume",
            filetypes=[("PDF files", "*.pdf"), ("Word files", "*.docx"), ("All files", "*.*")]
        )
        if path:
            self.resume_path_var.set(path)

    def write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def connect(self):
        def work():
            try:
                self.info.set("Opening Google authorization...")
                creds = get_credentials()
                self.service = build("sheets", "v4", credentials=creds)
                self.gmail = build("gmail", "v1", credentials=creds)
                # Do not call Gmail users.getProfile here. The app only requests gmail.send scope,
                # and getProfile requires a different Gmail scope. Successful API client creation
                # is enough; the actual send call will verify the Gmail permission.
                self.info.set("Gmail connected successfully")
                self.write_log("Gmail connected successfully.")
                self.load_btn.config(state="normal")
            except Exception as e:
                self.info.set("Connection failed")
                messagebox.showerror("Connection error", str(e))
        threading.Thread(target=work, daemon=True).start()

    def load_sheet(self):
        try:
            if not self.service:
                raise RuntimeError("Connect Gmail first.")
            sid = extract_sheet_id(self.sheet_var.get())
            if not sid:
                raise ValueError("Enter a Google Sheet URL or ID.")
            self.rows = get_rows(self.service, sid, self.range_var.get().strip())
            pending = [r for r in self.rows if r["status"].strip().lower() != "sent"]
            self.count.set(f"{len(self.rows)} rows loaded • {len(pending)} pending")
            self.progress["maximum"] = max(1, len(pending))
            self.progress["value"] = 0
            self.write_log(f"Loaded {len(self.rows)} rows; {len(pending)} pending.")
            self.send_btn.config(state="normal" if pending else "disabled")
        except Exception as e:
            messagebox.showerror("Sheet error", str(e))

    def confirm_send(self):
        pending = [r for r in self.rows if r["status"].strip().lower() != "sent"]
        valid = [r for r in pending if r["email"] and r["subject"]]
        if self.attach_resume_var.get() and not self.resume_path_var.get():
            messagebox.showwarning("Resume required", "Please select your resume before sending.")
            return
        if self.attach_resume_var.get() and not os.path.isfile(self.resume_path_var.get()):
            messagebox.showwarning("Resume not found", "The selected resume file could not be found.")
            return
        if not valid:
            messagebox.showinfo("Nothing to send", "No pending rows with an email and subject were found.")
            return
        attachment_note = "\nResume attachment: " + os.path.basename(self.resume_path_var.get()) if self.attach_resume_var.get() else "\nResume attachment: None"
        ok = messagebox.askyesno("Confirm", f"Send {len(valid)} individual emails from your connected Gmail account?\n\nOnly rows not marked Sent will be attempted.{attachment_note}")
        if ok:
            self.running = True
            self.send_btn.config(state="disabled")
            self.load_btn.config(state="disabled")
            threading.Thread(target=self.send_all, args=(valid,), daemon=True).start()

    def update_status(self, sheet_row, status):
        # Find the Status column dynamically from the header.
        sid = extract_sheet_id(self.sheet_var.get())
        header = self.service.spreadsheets().values().get(
            spreadsheetId=sid, range=self.range_var.get().strip().split("!")[0] + "!1:1"
        ).execute().get("values", [[]])[0]
        headers = [str(x).strip().lower() for x in header]
        if "status" not in headers:
            return
        col = headers.index("status")
        col_letter = ""
        n = col + 1
        while n:
            n, rem = divmod(n - 1, 26)
            col_letter = chr(65 + rem) + col_letter
        tab = self.range_var.get().strip().split("!")[0]
        self.service.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{tab}!{col_letter}{sheet_row}",
            valueInputOption="RAW",
            body={"values": [[status]]},
        ).execute()

    def send_all(self, rows):
        sent = 0
        failed = 0
        delay = 1.5
        for i, row in enumerate(rows, start=1):
            if not self.running:
                break
            try:
                attachment = self.resume_path_var.get() if self.attach_resume_var.get() else None
                send_one(self.gmail, row["email"], row["subject"], row["body"], attachment)
                self.update_status(row["sheet_row"], "Sent")
                sent += 1
                self.write_log(f"✓ {i}/{len(rows)} Sent → {row['email']}")
            except Exception as e:
                failed += 1
                try:
                    self.update_status(row["sheet_row"], "Failed: " + str(e)[:180])
                except Exception:
                    pass
                self.write_log(f"✗ {i}/{len(rows)} Failed → {row['email']} | {e}")
            self.progress["value"] = i
            self.root.update_idletasks()
            if i < len(rows):
                time.sleep(delay)
        self.running = False
        self.send_btn.config(state="normal")
        self.load_btn.config(state="normal")
        self.info.set(f"Finished • Sent: {sent} • Failed: {failed}")
        messagebox.showinfo("Finished", f"Finished.\n\nSent: {sent}\nFailed: {failed}")


if __name__ == "__main__":
    root = tk.Tk()
    try:
        root.iconname("Gmail Bulk Sender")
    except Exception:
        pass
    App(root)
    root.mainloop()
