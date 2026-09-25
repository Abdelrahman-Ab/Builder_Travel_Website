import os, json, sqlite3, hashlib, hmac, secrets, base64, io, subprocess, tempfile, re, mimetypes, zipfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from PIL import Image
import fitz

def tesseract_exe():
 env=os.environ.get('TESSERACT_CMD','').strip('\"')
 candidates=[env, r'C:\\Program Files\\Tesseract-OCR\\tesseract.exe', str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs'/'Tesseract-OCR'/'tesseract.exe'), 'tesseract']
 for x in candidates:
  if not x: continue
  if x=='tesseract' or Path(x).exists(): return x
 return 'tesseract'
TESSERACT=tesseract_exe()

ROOT=Path(__file__).parent; DB=ROOT/'buildertravel.db'; UP=ROOT/'uploads'; UP.mkdir(exist_ok=True)
SESS={}
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def hashpw(p,s=None):
 s=s or secrets.token_hex(16); h=hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),180000).hex(); return s+'$'+h
def checkpw(p,x):
 s,h=x.split('$'); return hmac.compare_digest(hashpw(p,s).split('$')[1],h)
def init():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,email TEXT UNIQUE,password TEXT,role TEXT);
CREATE TABLE IF NOT EXISTS packages(id INTEGER PRIMARY KEY,title_ar TEXT,title_en TEXT,kind TEXT,days INTEGER,start_date TEXT,end_date TEXT,airline TEXT,seats_total INTEGER,seats_booked INTEGER,price_double INTEGER,price_triple INTEGER,price_quad INTEGER,rooms_double INTEGER,rooms_triple INTEGER,rooms_quad INTEGER,status TEXT,image TEXT,hotel_makkah TEXT,hotel_madinah TEXT);
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY,code TEXT UNIQUE,package_id INTEGER,customer_name TEXT,phone TEXT,email TEXT,room_type TEXT,passengers INTEGER,status TEXT,payment TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS passengers(id INTEGER PRIMARY KEY,booking_id INTEGER,name TEXT,passport_no TEXT,nationality TEXT,birth_date TEXT,expiry_date TEXT,gender TEXT,passport_file TEXT,ocr_status TEXT,visa_status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS waitlist(id INTEGER PRIMARY KEY,package_id INTEGER,name TEXT,phone TEXT,passengers INTEGER,room_type TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user TEXT,action TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);''')
 if not c.execute('select 1 from users').fetchone(): c.execute('insert into users(name,email,password,role) values(?,?,?,?)',('مدير النظام','admin@buildertravel.com',hashpw('Builder@2026'),'owner'))
 if not c.execute('select 1 from packages').fetchone():
  rows=[('عمرة جمادى الأولى','Jumada Al-Awwal Umrah','umrah',10,'2026-10-18','2026-10-28','مصر للطيران',20,11,58000,54000,51000,5,7,8,'active','kaaba','فندق 5 نجوم قريب من الحرم','فندق 5 نجوم'),('عمرة رجب','Rajab Umrah','umrah',8,'2026-12-15','2026-12-23','السعودية',24,20,62000,57500,54000,3,5,6,'active','medina','سويس أوتيل المقام','أنوار المدينة موڤنبيك'),('عمرة شعبان','Shaaban Umrah','umrah',10,'2027-01-20','2027-01-30','مصر للطيران',30,30,68000,63000,59000,0,0,0,'full','kaaba2','فندق مطل على الحرم','فندق بالمنطقة المركزية'),('الحج المميز 2027','Premium Hajj 2027','hajj',14,'2027-05-10','2027-05-24','السعودية',40,7,245000,225000,210000,8,10,12,'active','hajj','إقامة مميزة بمكة','إقامة مميزة بالمدينة')]
  c.executemany('''insert into packages(title_ar,title_en,kind,days,start_date,end_date,airline,seats_total,seats_booked,price_double,price_triple,price_quad,rooms_double,rooms_triple,rooms_quad,status,image,hotel_makkah,hotel_madinah) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',rows)
 c.commit(); c.close()

def fmt_mrz_date(v, kind='birth'):
 v=re.sub(r'\D','',v or '')[:6]
 if len(v)!=6: return v
 yy,mm,dd=int(v[:2]),int(v[2:4]),int(v[4:6])
 # Passport MRZ dates are YYMMDD. Birth years are resolved to the past;
 # expiry years are resolved to the current/future century for modern passports.
 from datetime import date
 now=date.today().year
 if kind=='birth':
  year=2000+yy
  if year>now: year-=100
 else:
  year=2000+yy
  if year < now-10: year+=100
 try: return f'{dd:02d}/{mm:02d}/{year:04d}'
 except: return v

def printed_name(text):
 # Egyptian passports print the English full name directly under "Full Name".
 # Prefer that over the MRZ because the MRZ name field can be reordered/truncated.
 lines=[re.sub(r'\s+',' ',x).strip() for x in text.splitlines()]
 for i,line in enumerate(lines):
  if re.search(r'full\s*na(?:me|ge)',line,re.I):
   vals=[]
   for q in lines[i+1:i+4]:
    q=re.sub(r'[^A-Z <\-]',' ',q.upper()); q=re.sub(r'\s+',' ',q).strip(' <')
    q=re.sub(r'^SE\s+(?=[A-Z]{4,})','',q)
    if len(q)>=8 and sum(ch.isalpha() for ch in q)>=7 and not any(k in q for k in ['DATE OF','PLACE OF','NATIONAL','PASSPORT']): vals.append(q.replace('<',' '))
    else:
     if vals: break
   if vals: return re.sub(r'\s+',' ',' '.join(vals)).strip()
 return ''

def parse_mrz(text):
 rawlines=[re.sub(r'\s','',x.upper()) for x in text.splitlines() if '<' in x]
 lines=[]
 for x in rawlines:
  # Keep likely TD3 passport MRZ rows; OCR may add a few characters around them.
  m=re.search(r'(P<[A-Z0-9<]{35,})',x)
  if m: lines.append(m.group(1)[:44])
  elif re.search(r'[A-Z0-9<]{40,}',x): lines.append(re.search(r'[A-Z0-9<]{40,}',x).group(0)[:44])
 out={}
 for i,l in enumerate(lines):
  if len(l)>=40 and l.startswith('P<'):
   # TD3 line 1: P< + issuing state (3) + name field (39)
   names=l[5:44].split('<<',1)
   mrz_name=' '.join([x.replace('<',' ').strip() for x in names if x]).strip()
   q=lines[i+1] if i+1<len(lines) else ''
   if len(q)>=27:
    out['passport_no']=q[:9].replace('<','')
    out['nationality']=q[10:13].replace('<','')
    out['birth_date']=fmt_mrz_date(q[13:19],'birth')
    out['gender']=q[20:21].replace('<','')
    out['expiry_date']=fmt_mrz_date(q[21:27],'expiry')
   out['mrz_name']=mrz_name
   break
 pn=printed_name(text)
 out['name']=pn or out.get('mrz_name','')
 return out

def ocr_bytes(raw,mime):
 imgs=[]
 if mime=='application/pdf':
  doc=fitz.open(stream=raw,filetype='pdf')
  for p in doc:
   pix=p.get_pixmap(matrix=fitz.Matrix(2.5,2.5)); imgs.append(Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB'))
 else: imgs=[Image.open(io.BytesIO(raw)).convert('RGB')]
 texts=[]
 for im in imgs[:2]:
  # Upscale smaller scans. This materially improves MRZ character recognition.
  if im.width<1600:
   scale=1600/im.width; im=im.resize((int(im.width*scale),int(im.height*scale)))
  with tempfile.NamedTemporaryFile(suffix='.png',delete=False) as f: im.save(f.name); path=f.name
  try:
   full=subprocess.check_output([TESSERACT,path,'stdout','-l','eng','--psm','6'],stderr=subprocess.DEVNULL,timeout=35).decode('utf-8','ignore')
   texts.append(full)
  finally: os.unlink(path)
 text='\n'.join(texts); data=parse_mrz(text); data['raw_text']=text[:5000]; return data

class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def sendj(self,o,code=200):
  b=json.dumps(o,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',len(b)); self.end_headers(); self.wfile.write(b)
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
  except:return {}
 def user(self):
  ck=self.headers.get('Cookie',''); m=re.search(r'bt_session=([^;]+)',ck); return SESS.get(m.group(1)) if m else None
 def do_GET(self):
  p=urlparse(self.path).path
  if p.startswith('/api/'):
   c=db()
   if p=='/api/health':
    try:
     v=subprocess.check_output([TESSERACT,'--version'],stderr=subprocess.STDOUT,timeout=5).decode('utf-8','ignore').splitlines()[0]
     self.sendj({'ok':True,'ocr':True,'tesseract':v})
    except Exception as e: self.sendj({'ok':False,'ocr':False,'error':str(e)},500)
    return
   if p=='/api/packages': self.sendj([dict(x) for x in c.execute('select * from packages order by start_date')]); return
   if p=='/api/admin/summary':
    if not self.user(): self.sendj({'error':'unauthorized'},401); return
    self.sendj({'packages':c.execute("select count(*) n from packages where status='active'").fetchone()['n'],'bookings':c.execute('select count(*) n from bookings').fetchone()['n'],'passengers':c.execute('select coalesce(sum(passengers),0) n from bookings').fetchone()['n'],'waitlist':c.execute('select count(*) n from waitlist').fetchone()['n'],'recent':[dict(x) for x in c.execute('select b.*,p.title_ar from bookings b left join packages p on p.id=b.package_id order by b.id desc limit 8')],'packs':[dict(x) for x in c.execute('select * from packages order by start_date')]}); return
   if p=='/api/admin/bookings':
    if not self.user(): self.sendj({'error':'unauthorized'},401); return
    rows=[dict(x) for x in c.execute('select b.*,p.title_ar from bookings b left join packages p on p.id=b.package_id order by b.id desc')]
    for b in rows: b['passenger_data']=[dict(x) for x in c.execute('select * from passengers where booking_id=? order by id',(b['id'],))]
    self.sendj(rows); return
   if p=='/api/admin/passengers':
    if not self.user(): self.sendj({'error':'unauthorized'},401); return
    self.sendj([dict(x) for x in c.execute('select ps.*,b.code,b.phone,b.email,p.title_ar from passengers ps join bookings b on b.id=ps.booking_id left join packages p on p.id=b.package_id order by ps.id desc')]); return
   if p=='/api/admin/waitlist':
    if not self.user(): self.sendj({'error':'unauthorized'},401); return
    self.sendj([dict(x) for x in c.execute('select w.*,p.title_ar from waitlist w left join packages p on p.id=w.package_id order by w.id desc')]); return
   if p=='/api/admin/users':
    if not self.user(): self.sendj({'error':'unauthorized'},401); return
    self.sendj([dict(x) for x in c.execute('select id,name,email,role from users order by id')]); return
   if p.startswith('/api/admin/passenger-download/'):
    if not self.user(): self.sendj({'error':'unauthorized'},401); return
    try:
     pid=int(p.rsplit('/',1)[-1]); ps=c.execute('select ps.*,b.code,b.phone,b.email,p.title_ar from passengers ps join bookings b on b.id=ps.booking_id left join packages p on p.id=b.package_id where ps.id=?',(pid,)).fetchone()
     if not ps: self.sendj({'error':'not found'},404); return
     info=dict(ps); safe=re.sub(r'[^A-Za-z0-9._ -]+','_',info.get('name') or f'passenger-{pid}').strip() or f'passenger-{pid}'
     out=io.BytesIO()
     with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
      txt='BUILDER TRAVEL - PASSENGER FILE\n\n'+ '\n'.join(f'{k}: {v or ""}' for k,v in info.items() if k!='passport_file')
      z.writestr(f'{safe}/Passenger-Data.txt',txt)
      z.writestr(f'{safe}/Passenger-Data.json',json.dumps(info,ensure_ascii=False,indent=2))
      pf=info.get('passport_file')
      if pf and (ROOT/pf).exists(): z.write(ROOT/pf,arcname=f'{safe}/Passport{Path(pf).suffix}')
     data=out.getvalue(); self.send_response(200); self.send_header('Content-Type','application/zip'); self.send_header('Content-Disposition',f'attachment; filename="{safe}.zip"'); self.send_header('Content-Length',len(data)); self.end_headers(); self.wfile.write(data)
    except Exception as e: self.sendj({'error':str(e)},400)
    return
   if p=='/api/me': self.sendj(self.user() or {}); return
   self.sendj({'error':'not found'},404); return
  fp=ROOT/('index.html' if p in ['/','/admin','/admin/','/en','/en/','/packages','/offers','/services','/about','/contact','/my-trip'] or p.startswith('/package/') else p.lstrip('/'))
  if fp.exists() and fp.is_file():
   b=fp.read_bytes(); self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(fp)[0] or 'application/octet-stream'); self.send_header('Content-Length',len(b)); self.end_headers(); self.wfile.write(b); return
  self.send_response(302); self.send_header('Location','/'); self.end_headers()
 def do_POST(self):
  p=urlparse(self.path).path; d=self.body(); c=db()
  if p=='/api/login':
   u=c.execute('select * from users where email=?',(d.get('email',''),)).fetchone()
   if u and checkpw(d.get('password',''),u['password']):
    s=secrets.token_urlsafe(32); SESS[s]={'id':u['id'],'name':u['name'],'email':u['email'],'role':u['role']}; self.send_response(200); self.send_header('Set-Cookie',f'bt_session={s}; HttpOnly; SameSite=Lax; Path=/'); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'{"ok":true}'); return
   self.sendj({'error':'بيانات الدخول غير صحيحة'},401); return
  if p=='/api/logout':
   self.send_response(200); self.send_header('Set-Cookie','bt_session=; Max-Age=0; Path=/'); self.end_headers(); return
  if p=='/api/ocr':
   try:
    raw=base64.b64decode(d['data'].split(',')[-1]); info=ocr_bytes(raw,d.get('mime','image/jpeg')); self.sendj(info)
   except Exception as e:self.sendj({'error':'تعذر قراءة الملف','detail':str(e)},400)
   return
  if p=='/api/book':
   try:
    c.execute('BEGIN IMMEDIATE'); pack=c.execute('select * from packages where id=?',(d['package_id'],)).fetchone(); n=int(d['passengers']); rt=d['room_type']; roomcol={'double':'rooms_double','triple':'rooms_triple','quad':'rooms_quad'}[rt]
    capacity={'double':2,'triple':3,'quad':4}[rt]
    # One selected room = its exact maximum occupancy. Never silently put 3 people in a double, etc.
    if n>capacity:
     c.rollback(); self.sendj({'error':f'نوع الغرفة المختار يسمح بحد أقصى {capacity} مسافر/مسافرين. اختر نوع غرفة أكبر أو اعمل حجزاً منفصلاً لغرفة إضافية.','room_capacity':capacity},400); return
    rooms=1
    if not pack or pack['seats_total']-pack['seats_booked']<n or pack[roomcol]<1:
     c.execute('insert into waitlist(package_id,name,phone,passengers,room_type) values(?,?,?,?,?)',(d['package_id'],d['name'],d['phone'],n,rt)); c.commit(); self.sendj({'waitlist':True}); return
    code='BT-'+secrets.token_hex(3).upper(); cur=c.execute('insert into bookings(code,package_id,customer_name,phone,email,room_type,passengers,status,payment) values(?,?,?,?,?,?,?,?,?)',(code,d['package_id'],d['name'],d['phone'],d.get('email',''),rt,n,'reserved','pending')); bid=cur.lastrowid
    c.execute(f'update packages set seats_booked=seats_booked+?, {roomcol}={roomcol}-? where id=?',(n,rooms,d['package_id']))
    for idx,x in enumerate(d.get('passenger_data',[]),1):
     pf=''
     if x.get('passport_data'):
      try:
       raw=base64.b64decode(x['passport_data'].split(',')[-1]); ext=Path(x.get('passport_name','passport.pdf')).suffix.lower() or ('.pdf' if x.get('passport_mime')=='application/pdf' else '.jpg')
       folder=UP/code; folder.mkdir(parents=True,exist_ok=True); dest=folder/f'passenger-{idx}-passport{ext}'; dest.write_bytes(raw); pf=str(dest.relative_to(ROOT)).replace('\\','/')
      except Exception: pf=''
     c.execute('insert into passengers(booking_id,name,passport_no,nationality,birth_date,expiry_date,gender,passport_file,ocr_status) values(?,?,?,?,?,?,?,?,?)',(bid,x.get('name',''),x.get('passport_no',''),x.get('nationality',''),x.get('birth_date',''),x.get('expiry_date',''),x.get('gender',''),pf,'reviewed'))
    c.commit(); self.sendj({'ok':True,'code':code})
   except Exception as e: c.rollback(); self.sendj({'error':str(e)},400)
   return
  if p=='/api/admin/passenger-update':
   if not self.user(): self.sendj({'error':'unauthorized'},401); return
   try:
    c.execute('update passengers set visa_status=? where id=?',(d.get('visa_status','pending'),int(d['id']))); c.execute('insert into audit(user,action) values(?,?)',(self.user()['email'],f"Updated passenger {d['id']} visa to {d.get('visa_status')}")); c.commit(); self.sendj({'ok':True})
   except Exception as e:self.sendj({'error':str(e)},400)
   return
  if p=='/api/admin/booking-update':
   if not self.user(): self.sendj({'error':'unauthorized'},401); return
   try:
    c.execute('update bookings set status=?,payment=? where id=?',(d.get('status','reserved'),d.get('payment','pending'),int(d['id']))); c.commit(); self.sendj({'ok':True})
   except Exception as e:self.sendj({'error':str(e)},400)
   return
  if p=='/api/admin/user':
   if not self.user(): self.sendj({'error':'unauthorized'},401); return
   try:
    if not d.get('name') or not d.get('email') or not d.get('password'): raise ValueError('أكمل بيانات المستخدم')
    c.execute('insert into users(name,email,password,role) values(?,?,?,?)',(d['name'],d['email'],hashpw(d['password']),d.get('role','reservations'))); c.commit(); self.sendj({'ok':True})
   except Exception as e:self.sendj({'error':str(e)},400)
   return
  if p=='/api/admin/package':
   if not self.user(): self.sendj({'error':'unauthorized'},401); return
   try:
    if d.get('id'):
     c.execute('update packages set seats_total=?,rooms_double=?,rooms_triple=?,rooms_quad=?,status=? where id=?',(d['seats_total'],d['rooms_double'],d['rooms_triple'],d['rooms_quad'],d['status'],d['id']))
     action='Updated package inventory'
    else:
     required=['title_ar','title_en','kind','days','start_date','end_date','airline','seats_total']
     if any(not str(d.get(k,'')).strip() for k in required): raise ValueError('أكمل بيانات البرنامج الأساسية')
     image=d.get('image') or secrets.choice(['kaaba','medina','kaaba2','hajj'])
     vals=(d['title_ar'],d['title_en'],d['kind'],int(d['days']),d['start_date'],d['end_date'],d['airline'],int(d['seats_total']),0,int(d.get('price_double',0)),int(d.get('price_triple',0)),int(d.get('price_quad',0)),int(d.get('rooms_double',0)),int(d.get('rooms_triple',0)),int(d.get('rooms_quad',0)),d.get('status','active'),image,d.get('hotel_makkah',''),d.get('hotel_madinah',''))
     c.execute('''insert into packages(title_ar,title_en,kind,days,start_date,end_date,airline,seats_total,seats_booked,price_double,price_triple,price_quad,rooms_double,rooms_triple,rooms_quad,status,image,hotel_makkah,hotel_madinah) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)
     action='Created package '+d['title_ar']
    c.execute('insert into audit(user,action) values(?,?)',(self.user()['email'],action)); c.commit(); self.sendj({'ok':True,'id':c.execute('select last_insert_rowid()').fetchone()[0]})
   except Exception as e:self.sendj({'error':str(e)},400)
   return
  self.sendj({'error':'not found'},404)
init();
if __name__=='__main__':
 print('Builder Travel running on http://localhost:8787'); ThreadingHTTPServer(('0.0.0.0',8787),H).serve_forever()
