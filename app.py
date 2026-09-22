import os, sqlite3, smtplib, urllib.parse
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

load_dotenv()
BASE=Path(__file__).resolve().parent
DB=BASE/'crm.db'; UPLOADS=BASE/'static'/'uploads'; GENERATED=BASE/'static'/'generated'
UPLOADS.mkdir(parents=True,exist_ok=True); GENERATED.mkdir(parents=True,exist_ok=True)
app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','change-me')
STATUSES=['Inquiry','Quotation','Proforma Invoice','Payment Advice','PO Received','Invoice Sent','Closed','Lost']
DOC_TYPES=['Quotation','Proforma Invoice','Payment Advice','PO','Invoice','Drawing','Technical Document','Other']
ACTIVITY_TYPES=['Call','Email','Meeting','WhatsApp','Follow-up','Quotation Sent','PO Received','Payment','Invoice Sent','Note']


def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def init_db():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,location TEXT NOT NULL,email TEXT);
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY,company TEXT NOT NULL,contact_name TEXT,email TEXT,phone TEXT,city TEXT,notes TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inquiries(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,title TEXT NOT NULL,value REAL DEFAULT 0,status TEXT NOT NULL,owner_id INTEGER NOT NULL,inquiry_date TEXT NOT NULL,next_followup TEXT,notes TEXT,created_at TEXT NOT NULL,po_number TEXT,po_date TEXT,payment_status TEXT DEFAULT 'Pending',payment_date TEXT,invoice_number TEXT,FOREIGN KEY(customer_id) REFERENCES customers(id),FOREIGN KEY(owner_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS activities(id INTEGER PRIMARY KEY,inquiry_id INTEGER,user_id INTEGER NOT NULL,activity_date TEXT NOT NULL,kind TEXT NOT NULL,notes TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(inquiry_id) REFERENCES inquiries(id) ON DELETE CASCADE,FOREIGN KEY(user_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,inquiry_id INTEGER NOT NULL,user_id INTEGER NOT NULL,doc_type TEXT NOT NULL,filename TEXT NOT NULL,original_name TEXT NOT NULL,uploaded_at TEXT NOT NULL,FOREIGN KEY(inquiry_id) REFERENCES inquiries(id) ON DELETE CASCADE,FOREIGN KEY(user_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS emails(id INTEGER PRIMARY KEY,inquiry_id INTEGER,customer_id INTEGER,to_email TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,sent_at TEXT NOT NULL,status TEXT NOT NULL,FOREIGN KEY(inquiry_id) REFERENCES inquiries(id) ON DELETE SET NULL,FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS counters(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
    ''')
    if c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']==0:
        c.execute('INSERT INTO users(username,password_hash,role,location,email) VALUES (?,?,?,?,?)',('owner',generate_password_hash('owner123'),'Owner','Baroda',os.getenv('OWNER_EMAIL','')))
        c.execute('INSERT INTO users(username,password_hash,role,location,email) VALUES (?,?,?,?,?)',('mumbai',generate_password_hash('mumbai123'),'Marketing','Mumbai',''))
    defaults={'company_name':'Your Company Name','company_address':'Vadodara, Gujarat, India','company_email':os.getenv('OWNER_EMAIL',''),'currency':'INR','daily_summary_time':'21:00'}
    for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)',(k,v))
    for k in ['QUO','PI','INV','PA']: c.execute('INSERT OR IGNORE INTO counters(key,value) VALUES (?,0)',(k,))
    c.commit(); c.close()

def setting(k):
    c=db(); r=c.execute('SELECT value FROM settings WHERE key=?',(k,)).fetchone(); c.close(); return r['value'] if r else ''

def next_number(prefix):
    c=db(); c.execute('UPDATE counters SET value=value+1 WHERE key=?',(prefix,)); n=c.execute('SELECT value FROM counters WHERE key=?',(prefix,)).fetchone()['value']; c.commit(); c.close(); return f'{prefix}-{datetime.now().year}-{n:04d}'

def login_required(f):
    @wraps(f)
    def w(*a,**kw):
        if 'user_id' not in session:return redirect(url_for('login'))
        return f(*a,**kw)
    return w

@app.context_processor
def inject(): return {'statuses':STATUSES,'doc_types':DOC_TYPES,'activity_types':ACTIVITY_TYPES,'today':date.today().isoformat(),'company_name':setting('company_name')}

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        c=db(); u=c.execute('SELECT * FROM users WHERE username=?',(request.form['username'],)).fetchone(); c.close()
        if u and check_password_hash(u['password_hash'],request.form['password']):
            session.update(user_id=u['id'],username=u['username'],role=u['role'],location=u['location']); return redirect(url_for('dashboard'))
        flash('Invalid username or password.','error')
    return render_template('login.html')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    c=db(); total=c.execute('SELECT COUNT(*) n FROM inquiries').fetchone()['n']; open_count=c.execute("SELECT COUNT(*) n FROM inquiries WHERE status NOT IN ('Closed','Lost')").fetchone()['n']; overdue=c.execute("SELECT COUNT(*) n FROM inquiries WHERE next_followup<? AND status NOT IN ('Closed','Lost')",(date.today().isoformat(),)).fetchone()['n']; due=c.execute("SELECT COUNT(*) n FROM inquiries WHERE next_followup=? AND status NOT IN ('Closed','Lost')",(date.today().isoformat(),)).fetchone()['n']; pipeline=c.execute('SELECT status,COUNT(*) count,COALESCE(SUM(value),0) value FROM inquiries GROUP BY status').fetchall(); recent=c.execute('SELECT i.*,c.company FROM inquiries i JOIN customers c ON c.id=i.customer_id ORDER BY i.created_at DESC LIMIT 10').fetchall(); pending=c.execute("SELECT COALESCE(SUM(value),0) v FROM inquiries WHERE status='Invoice Sent' AND payment_status!='Paid'").fetchone()['v']; c.close(); return render_template('dashboard.html',total=total,open_count=open_count,overdue=overdue,due=due,pipeline=pipeline,recent=recent,pending=pending)

@app.route('/customers')
@login_required
def customers():
    q=request.args.get('q','').strip(); c=db(); rows=c.execute('SELECT * FROM customers WHERE company LIKE ? OR contact_name LIKE ? OR email LIKE ? ORDER BY company',('%'+q+'%','%'+q+'%','%'+q+'%')).fetchall(); c.close(); return render_template('customers.html',customers=rows,q=q)
@app.route('/customers/new',methods=['GET','POST'])
@login_required
def new_customer():
    if request.method=='POST':
        c=db(); c.execute('INSERT INTO customers(company,contact_name,email,phone,city,notes,created_at) VALUES (?,?,?,?,?,?,?)',(request.form['company'],request.form.get('contact_name'),request.form.get('email'),request.form.get('phone'),request.form.get('city'),request.form.get('notes'),now())); c.commit(); c.close(); flash('Customer added.'); return redirect(url_for('customers'))
    return render_template('customer_form.html')

@app.route('/inquiries')
@login_required
def inquiries():
    status=request.args.get('status'); q=request.args.get('q','').strip(); c=db(); sql='SELECT i.*,c.company,c.email,u.username FROM inquiries i JOIN customers c ON c.id=i.customer_id JOIN users u ON u.id=i.owner_id WHERE (c.company LIKE ? OR i.title LIKE ? OR COALESCE(i.po_number,\'\') LIKE ?)'; p=['%'+q+'%','%'+q+'%','%'+q+'%'];
    if status: sql+=' AND i.status=?'; p.append(status)
    sql+=' ORDER BY COALESCE(i.next_followup,i.inquiry_date) DESC'; rows=c.execute(sql,p).fetchall(); c.close(); return render_template('inquiries.html',inquiries=rows,selected=status,q=q)

@app.route('/inquiries/new',methods=['GET','POST'])
@login_required
def new_inquiry():
    c=db(); customers=c.execute('SELECT * FROM customers ORDER BY company').fetchall(); c.close()
    if request.method=='POST':
        c=db(); c.execute('INSERT INTO inquiries(customer_id,title,value,status,owner_id,inquiry_date,next_followup,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(request.form['customer_id'],request.form['title'],float(request.form.get('value') or 0),'Inquiry',session['user_id'],request.form.get('inquiry_date') or date.today().isoformat(),request.form.get('next_followup') or None,request.form.get('notes'),now())); iid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO activities(inquiry_id,user_id,activity_date,kind,notes,created_at) VALUES (?,?,?,?,?,?)',(iid,session['user_id'],date.today().isoformat(),'Follow-up',request.form.get('notes') or 'New customer inquiry',now())); c.commit(); c.close(); flash('Inquiry created.'); return redirect(url_for('inquiry_detail',iid=iid))
    return render_template('inquiry_form.html',customers=customers)

@app.route('/inquiries/<int:iid>')
@login_required
def inquiry_detail(iid):
    c=db(); i=c.execute('SELECT i.*,c.company,c.contact_name,c.email,c.phone,c.city,u.username FROM inquiries i JOIN customers c ON c.id=i.customer_id JOIN users u ON u.id=i.owner_id WHERE i.id=?',(iid,)).fetchone(); acts=c.execute('SELECT a.*,u.username FROM activities a JOIN users u ON u.id=a.user_id WHERE a.inquiry_id=? ORDER BY a.activity_date DESC,a.id DESC',(iid,)).fetchall(); docs=c.execute('SELECT d.*,u.username FROM documents d JOIN users u ON u.id=d.user_id WHERE d.inquiry_id=? ORDER BY d.uploaded_at DESC',(iid,)).fetchall(); emails=c.execute('SELECT * FROM emails WHERE inquiry_id=? ORDER BY sent_at DESC',(iid,)).fetchall(); c.close(); return render_template('inquiry_detail.html',inquiry=i,activities=acts,documents=docs,emails=emails,whatsapp=whatsapp_link(i))

def whatsapp_link(i):
    phone=(i['phone'] or '').replace('+','').replace(' ','').replace('-','').replace('(','').replace(')',''); text=f"Hello {i['contact_name'] or i['company']}, following up regarding {i['title']}."; return 'https://wa.me/'+phone+'?text='+urllib.parse.quote(text) if phone else ''

@app.route('/inquiries/<int:iid>/update',methods=['POST'])
@login_required
def update_inquiry(iid):
    status=request.form['status']; po=request.form.get('po_number') or None; inv=request.form.get('invoice_number') or None
    if status=='Quotation' and not inv: pass
    if status=='PO Received' and not po: po='PO-PENDING'
    if status=='Invoice Sent' and not inv: inv=next_number('INV')
    c=db(); c.execute('UPDATE inquiries SET status=?,next_followup=?,notes=?,po_number=?,po_date=?,payment_status=?,payment_date=?,invoice_number=? WHERE id=?',(status,request.form.get('next_followup') or None,request.form.get('notes'),po,request.form.get('po_date') or None,request.form.get('payment_status') or 'Pending',request.form.get('payment_date') or None,inv,iid)); c.execute('INSERT INTO activities(inquiry_id,user_id,activity_date,kind,notes,created_at) VALUES (?,?,?,?,?,?)',(iid,session['user_id'],date.today().isoformat(),status,request.form.get('activity_notes') or f'Status changed to {status}',now())); c.commit(); c.close(); flash('Inquiry updated.'); return redirect(url_for('inquiry_detail',iid=iid))

@app.route('/inquiries/<int:iid>/activity',methods=['POST'])
@login_required
def add_activity(iid):
    c=db(); c.execute('INSERT INTO activities(inquiry_id,user_id,activity_date,kind,notes,created_at) VALUES (?,?,?,?,?,?)',(iid,session['user_id'],request.form.get('activity_date') or date.today().isoformat(),request.form['kind'],request.form['notes'],now())); c.commit(); c.close(); flash('Activity logged.'); return redirect(url_for('inquiry_detail',iid=iid))

@app.route('/inquiries/<int:iid>/upload',methods=['POST'])
@login_required
def upload_doc(iid):
    f=request.files.get('file');
    if not f or not f.filename: flash('Choose a file.','error'); return redirect(url_for('inquiry_detail',iid=iid))
    safe=secure_filename(f.filename); name=f'{iid}_{datetime.now().strftime("%Y%m%d%H%M%S%f")}_{safe}'; f.save(UPLOADS/name); c=db(); c.execute('INSERT INTO documents(inquiry_id,user_id,doc_type,filename,original_name,uploaded_at) VALUES (?,?,?,?,?,?)',(iid,session['user_id'],request.form['doc_type'],name,safe,now())); c.commit(); c.close(); flash('Document attached.'); return redirect(url_for('inquiry_detail',iid=iid))
@app.route('/uploads/<path:name>')
@login_required
def uploaded(name): return send_from_directory(UPLOADS,name,as_attachment=True)

@app.route('/inquiries/<int:iid>/generate/<kind>')
@login_required
def generate_doc(iid,kind):
    c=db(); i=c.execute('SELECT i.*,c.company,c.contact_name,c.email,c.phone,c.city FROM inquiries i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(iid,)).fetchone(); c.close()
    if not i:return ('Not found',404)
    labels={'quotation':('QUO','Quotation'),'proforma':('PI','Proforma Invoice'),'payment':('PA','Payment Advice'),'invoice':('INV','Invoice')}; key,title=labels.get(kind,('QUO','Quotation')); number=next_number(key)
    if kind=='invoice':
        c=db(); c.execute('UPDATE inquiries SET invoice_number=? WHERE id=?',(number,iid)); c.commit(); c.close()
    elif kind=='quotation':
        pass
    filename=f'{kind}_{number}.pdf'; path=GENERATED/filename; pdf=canvas.Canvas(str(path),pagesize=A4); w,h=A4; y=h-55
    pdf.setFont('Helvetica-Bold',16); pdf.drawString(45,y,setting('company_name')); y-=22; pdf.setFont('Helvetica',9); pdf.drawString(45,y,setting('company_address')); y-=12; pdf.drawString(45,y,setting('company_email')); y-=30
    pdf.setFont('Helvetica-Bold',14); pdf.drawString(45,y,title); pdf.drawRightString(w-45,y,number); y-=28; pdf.setFont('Helvetica',10); pdf.drawString(45,y,f'Customer: {i["company"]}'); y-=16; pdf.drawString(45,y,f'Contact: {i["contact_name"] or ""}'); y-=16; pdf.drawString(45,y,f'Inquiry: {i["title"]}'); y-=25; pdf.line(45,y,w-45,y); y-=22; pdf.drawString(45,y,'Description'); pdf.drawRightString(w-45,y,'Amount'); y-=18; pdf.drawString(45,y,i['title']); pdf.drawRightString(w-45,y,f'{setting("currency")} {i["value"]:,.2f}'); y-=25; pdf.line(45,y,w-45,y); y-=20; pdf.setFont('Helvetica-Bold',11); pdf.drawRightString(w-45,y,f'Total: {setting("currency")} {i["value"]:,.2f}'); y-=40; pdf.setFont('Helvetica',9); pdf.drawString(45,y,'Generated by Marketing CRM'); pdf.save();
    c=db(); c.execute('INSERT INTO documents(inquiry_id,user_id,doc_type,filename,original_name,uploaded_at) VALUES (?,?,?,?,?,?)',(iid,session['user_id'],title,filename,filename,now())); c.commit(); c.close(); return send_file(path,as_attachment=True,download_name=filename)

@app.route('/inquiries/<int:iid>/email',methods=['POST'])
@login_required
def email_customer(iid):
    c=db(); i=c.execute('SELECT i.*,c.company,c.email,c.contact_name FROM inquiries i JOIN customers c ON c.id=i.customer_id WHERE i.id=?',(iid,)).fetchone(); c.close();
    if not i or not i['email']: flash('Customer email is missing.','error'); return redirect(url_for('inquiry_detail',iid=iid))
    subject=request.form.get('subject') or f'Follow-up – {i["title"]}'; body=request.form.get('body') or f'Hello {i["contact_name"] or i["company"]},\\n\\nPlease find the requested documents/details regarding {i["title"]}.\\n\\nRegards,\\n{setting("company_name")}'
    files=[]
    for did in request.form.getlist('document_ids'):
        c=db(); d=c.execute('SELECT * FROM documents WHERE id=? AND inquiry_id=?',(did,iid)).fetchone(); c.close();
        if d:
            p=UPLOADS/d['filename']; p=GENERATED/d['filename'] if not p.exists() else p
            if p.exists(): files.append((d['original_name'],p.read_bytes()))
    try:
        host=os.getenv('SMTP_HOST'); user=os.getenv('SMTP_USERNAME'); pwd=os.getenv('SMTP_PASSWORD'); port=int(os.getenv('SMTP_PORT','587'))
        if not host or not user or not pwd: raise RuntimeError('SMTP settings are not configured')
        msg=EmailMessage(); msg['Subject']=subject; msg['From']=user; msg['To']=i['email']; msg.set_content(body)
        for name,data in files: msg.add_attachment(data,maintype='application',subtype='octet-stream',filename=name)
        with smtplib.SMTP(host,port) as s:
            if os.getenv('SMTP_USE_TLS','true').lower()=='true': s.starttls()
            s.login(user,pwd); s.send_message(msg)
        status='Sent'; flash('Email sent to customer.')
    except Exception as e: status='Failed'; flash(f'Email failed: {e}','error')
    c=db(); c.execute('INSERT INTO emails(inquiry_id,customer_id,to_email,subject,body,sent_at,status) VALUES (?,?,?,?,?,?,?)',(iid,i['customer_id'],i['email'],subject,body,now(),status)); c.commit(); c.close(); return redirect(url_for('inquiry_detail',iid=iid))

@app.route('/settings',methods=['GET','POST'])
@login_required
def settings():
    if session.get('role')!='Owner': return ('Forbidden',403)
    if request.method=='POST':
        c=db();
        for k in ['company_name','company_address','company_email','currency','daily_summary_time']: c.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,request.form.get(k,'')))
        c.commit(); c.close(); flash('Settings saved.'); return redirect(url_for('settings'))
    return render_template('settings.html',settings={k:setting(k) for k in ['company_name','company_address','company_email','currency','daily_summary_time']})

@app.route('/daily-summary')
@login_required
def daily_summary_page(): return render_template('summary.html',summary=build_summary())
def build_summary():
    c=db(); d=date.today().isoformat(); acts=c.execute('SELECT a.*,u.username,i.title,c.company FROM activities a JOIN users u ON u.id=a.user_id LEFT JOIN inquiries i ON i.id=a.inquiry_id LEFT JOIN customers c ON c.id=i.customer_id WHERE a.activity_date=? ORDER BY a.id DESC',(d,)).fetchall(); due=c.execute("SELECT i.*,c.company FROM inquiries i JOIN customers c ON c.id=i.customer_id WHERE i.next_followup=? AND i.status NOT IN ('Closed','Lost') ORDER BY i.id",(d,)).fetchall(); overdue=c.execute("SELECT i.*,c.company FROM inquiries i JOIN customers c ON c.id=i.customer_id WHERE i.next_followup<? AND i.status NOT IN ('Closed','Lost') ORDER BY i.next_followup",(d,)).fetchall(); c.close(); return {'date':d,'activities':acts,'due':due,'overdue':overdue}

def send_daily_summary():
    s=build_summary(); to=setting('company_email') or os.getenv('OWNER_EMAIL');
    if not to: raise RuntimeError('Owner email is not configured')
    lines=[f'{setting("company_name")} – CRM Daily Summary – {s["date"]}','',f'Activities today: {len(s["activities"])}',f'Follow-ups due today: {len(s["due"])}',f'Overdue follow-ups: {len(s["overdue"])}','']
    lines+=['TODAY\'S WORK']+[f'- {a["username"]} | {a["company"] or ""} | {a["kind"]} | {a["notes"]}' for a in s['activities']]
    lines+=['','FOLLOW-UPS DUE TODAY']+[f'- {i["company"]} | {i["title"]} | {i["status"]}' for i in s['due']]
    lines+=['','OVERDUE FOLLOW-UPS']+[f'- {i["company"]} | {i["title"]} | due {i["next_followup"]} | {i["status"]}' for i in s['overdue']]
    msg=EmailMessage(); msg['Subject']=f'CRM Daily Summary – {s["date"]}'; msg['From']=os.getenv('SMTP_USERNAME'); msg['To']=to; msg.set_content('\n'.join(lines)); host=os.getenv('SMTP_HOST'); port=int(os.getenv('SMTP_PORT','587'))
    with smtplib.SMTP(host,port) as smtp:
        if os.getenv('SMTP_USE_TLS','true').lower()=='true': smtp.starttls()
        smtp.login(os.getenv('SMTP_USERNAME'),os.getenv('SMTP_PASSWORD')); smtp.send_message(msg)

@app.route('/admin/send-daily-summary')
@login_required
def send_summary_route():
    if session.get('role')!='Owner': return ('Forbidden',403)
    try: send_daily_summary(); flash('Daily summary emailed.')
    except Exception as e: flash(f'Email failed: {e}','error')
    return redirect(url_for('daily_summary_page'))

init_db()
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=os.getenv('DEBUG','false').lower()=='true')
