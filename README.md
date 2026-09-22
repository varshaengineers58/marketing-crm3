# Marketing CRM v2

A browser-based CRM for two users: Owner (Baroda) and Marketing (Mumbai). Works on Windows and macOS through a browser.

## Included
- Two-user login and role/location separation
- Customer database and searchable inquiries
- Pipeline: Inquiry → Quotation → Proforma Invoice → Payment Advice → PO Received → Invoice Sent → Closed/Lost
- Daily activity log for calls, emails, meetings, WhatsApp and follow-ups
- Follow-up dates, due/overdue dashboard
- PO, payment and invoice tracking
- File attachments for quotations, drawings, technical documents, PO and invoices
- PDF generation for quotation, proforma invoice, payment advice and invoice
- One-click customer email with selected attachments
- Email history
- WhatsApp follow-up shortcut
- Owner dashboard and daily summary
- Manual “Email me now” daily summary
- Company settings

## Demo users
- Owner: `owner` / `owner123`
- Mumbai Marketing: `mumbai` / `mumbai123`

Change passwords before production use.

## Run
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows
# or cp .env.example .env
python app.py
```
Open http://localhost:5000

## Email
Fill SMTP credentials in `.env`. For Gmail/Google Workspace, use an App Password where required. The CRM stores email history and can send the daily summary to the configured company/owner email.

## Daily automatic email
The app includes the daily-summary function, but the production deployment should run it once daily using the hosting platform's scheduler/cron at the configured time (default 21:00 IST). This avoids relying on a browser or local PC being switched on.

## Production recommendation
Deploy the app to a cloud server with HTTPS, a managed PostgreSQL database, private object/file storage, daily backups and scheduled jobs. Both users then access the same web URL from Mumbai or Baroda on Windows/Mac.
