# Gmail Bulk Sender (Python + Google Sheets)

A small Windows-friendly Python GUI that reads recipients from Google Sheets and sends individual messages through the Gmail API.

## Sheet format

Use row 1 as headers:

| Email | Subject | Body | Status |
|---|---|---|---|
| person@example.com | Internship Opportunity | Hello... | Pending |
| another@example.com | Job Opportunity | Hi... | Pending |

Rows whose Status is exactly `Sent` are skipped. The tool writes `Sent` or `Failed: ...` back to the Status column.

## First-time Google setup

1. Install Python 3.10+.
2. Create a Google Cloud project.
3. Enable **Gmail API** and **Google Sheets API**.
4. Configure Google Auth Platform / OAuth consent screen.
5. Create an OAuth client with application type **Desktop app**.
6. Download the JSON file and rename it `credentials.json`.
7. Put `credentials.json` in this folder beside `app.py`.
8. Open Command Prompt in this folder and run:

```bat
python -m pip install -r requirements.txt
python app.py
```

9. Click **Connect Gmail** and authorize the Gmail account that should actually send the messages.
10. Paste the Google Sheet URL/ID and click **Load Sheet**.
11. Click **SEND** and confirm.

A local `token.json` is created after authorization. Do not share `credentials.json` or `token.json` with anyone.

## Notes

- The app sends one separate email per row.
- It does not ask for your Gmail password.
- A modest delay is included between messages.
- Use only for recipients you are permitted to contact and follow Gmail/Google policies.
