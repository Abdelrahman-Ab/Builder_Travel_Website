import os,re,json,base64,hashlib,hmac,secrets,io,zipfile,requests
from flask import Flask,request,jsonify,session,Response
import psycopg
from psycopg.rows import dict_row
from vercel.blob import BlobClient

app=Flask(__name__); app.secret_key=os.environ.get('SECRET_KEY','change-me-before-production')
DB=os.environ.get('DATABASE_URL','')
INIT_DONE=False

def blob_configured():
    # On Vercel, connected Blob stores use OIDC by default. The SDK pairs
    # BLOB_STORE_ID with VERCEL_OIDC_TOKEN automatically. A static
    # BLOB_READ_WRITE_TOKEN remains supported only as a fallback/local option.
    return bool(os.environ.get('BLOB_STORE_ID') and (os.environ.get('VERCEL_OIDC_TOKEN') or os.environ.get('BLOB_READ_WRITE_TOKEN')))

def blob_put_bytes(pathname, raw, mime):
    with BlobClient() as client:
        result=client.put(pathname,raw,access='private',content_type=mime,add_random_suffix=True)
        return result.pathname

def blob_get_bytes(pathname):
    if not pathname: return None
    with BlobClient() as client:
        result=client.get(pathname,access='private')
        if result is None or result.status_code!=200 or result.stream is None: return None
        return b''.join(result.stream)

def conn():
    if not DB: raise RuntimeError('DATABASE_URL is not configured')
    return psycopg.connect(DB,row_factory=dict_row)
def hashpw(p,s=None):
    s=s or secrets.token_hex(16); h=hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),180000).hex(); return s+'$'+h
def checkpw(p,x):
    s,h=x.split('$'); return hmac.compare_digest(hashpw(p,s).split('$')[1],h)
def auth(): return session.get('user')
def init():
 global INIT_DONE
 if INIT_DONE: return
 with conn() as c:
  with c.cursor() as q:
   # Serialize bootstrap work across concurrent Vercel cold starts.
   q.execute('SELECT pg_advisory_xact_lock(%s)', (726421937,))
   q.execute('''CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY,name TEXT,email TEXT UNIQUE,password TEXT,role TEXT);
CREATE TABLE IF NOT EXISTS packages(id SERIAL PRIMARY KEY,title_ar TEXT,title_en TEXT,kind TEXT,days INTEGER,start_date TEXT,end_date TEXT,airline TEXT,seats_total INTEGER,seats_booked INTEGER,price_double INTEGER,price_triple INTEGER,price_quad INTEGER,rooms_double INTEGER,rooms_triple INTEGER,rooms_quad INTEGER,status TEXT,image TEXT,hotel_makkah TEXT,hotel_madinah TEXT);
CREATE TABLE IF NOT EXISTS bookings(id SERIAL PRIMARY KEY,code TEXT UNIQUE,package_id INTEGER,customer_name TEXT,phone TEXT,email TEXT,room_type TEXT,passengers INTEGER,status TEXT,payment TEXT,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS passengers(id SERIAL PRIMARY KEY,booking_id INTEGER,name TEXT,passport_no TEXT,nationality TEXT,birth_date TEXT,expiry_date TEXT,gender TEXT,passport_name TEXT,passport_mime TEXT,passport_blob BYTEA,ocr_status TEXT,visa_status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS waitlist(id SERIAL PRIMARY KEY,package_id INTEGER,name TEXT,phone TEXT,passengers INTEGER,room_type TEXT,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS audit(id SERIAL PRIMARY KEY,"user" TEXT,action TEXT,created_at TIMESTAMPTZ DEFAULT NOW());''')
   q.execute('ALTER TABLE passengers ADD COLUMN IF NOT EXISTS passport_blob_path TEXT')
   q.execute('select 1 from users limit 1')
   if not q.fetchone(): q.execute('insert into users(name,email,password,role) values(%s,%s,%s,%s)',('مدير النظام','admin@buildertravel.com',hashpw('Builder@2026'),'owner'))
   q.execute('select 1 from packages limit 1')
   if not q.fetchone():
     rows=[('عمرة جمادى الأولى','Jumada Al-Awwal Umrah','umrah',10,'2026-10-18','2026-10-28','مصر للطيران',20,11,58000,54000,51000,5,7,8,'active','kaaba','فندق 5 نجوم قريب من الحرم','فندق 5 نجوم'),('عمرة رجب','Rajab Umrah','umrah',8,'2026-12-15','2026-12-23','السعودية',24,20,62000,57500,54000,3,5,6,'active','medina','سويس أوتيل المقام','أنوار المدينة موڤنبيك'),('عمرة شعبان','Shaaban Umrah','umrah',10,'2027-01-20','2027-01-30','مصر للطيران',30,30,68000,63000,59000,0,0,0,'full','kaaba2','فندق مطل على الحرم','فندق بالمنطقة المركزية'),('الحج المميز 2027','Premium Hajj 2027','hajj',14,'2027-05-10','2027-05-24','السعودية',40,7,245000,225000,210000,8,10,12,'active','hajj','إقامة مميزة بمكة','إقامة مميزة بالمدينة')]
     q.executemany('''insert into packages(title_ar,title_en,kind,days,start_date,end_date,airline,seats_total,seats_booked,price_double,price_triple,price_quad,rooms_double,rooms_triple,rooms_quad,status,image,hotel_makkah,hotel_madinah) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',rows)
  c.commit()
 INIT_DONE=True

def fmt(v,kind='birth'):
 v=re.sub(r'\D','',v or '')[:6]
 if len(v)!=6:return v
 from datetime import date
 yy,mm,dd=int(v[:2]),int(v[2:4]),int(v[4:6]); now=date.today().year
 year=2000+yy
 if kind=='birth' and year>now: year-=100
 if kind!='birth' and year<now-10: year+=100
 return f'{dd:02d}/{mm:02d}/{year:04d}'
def printed_name(text):
 lines=[re.sub(r'\s+',' ',x).strip() for x in text.splitlines()]
 for i,line in enumerate(lines):
  if re.search(r'full\s*na(?:me|ge)',line,re.I):
   vals=[]
   for z in lines[i+1:i+4]:
    z=re.sub(r'[^A-Z <\-]',' ',z.upper()); z=re.sub(r'\s+',' ',z).strip(' <'); z=re.sub(r'^SE\s+(?=[A-Z]{4,})','',z)
    if len(z)>=8 and sum(ch.isalpha() for ch in z)>=7 and not any(k in z for k in ['DATE OF','PLACE OF','NATIONAL','PASSPORT']): vals.append(z.replace('<',' '))
    elif vals: break
   if vals:return re.sub(r'\s+',' ',' '.join(vals)).strip()
 return ''
def parse_mrz(text):
 raw=[re.sub(r'\s','',x.upper()) for x in text.splitlines() if '<' in x]; lines=[]; out={}
 for x in raw:
  m=re.search(r'(P<[A-Z0-9<]{35,})',x)
  if m:lines.append(m.group(1)[:44])
  else:
   m=re.search(r'[A-Z0-9<]{40,}',x)
   if m:lines.append(m.group(0)[:44])
 for i,l in enumerate(lines):
  if len(l)>=40 and l.startswith('P<'):
   out['mrz_name']=' '.join(x.replace('<',' ').strip() for x in l[5:44].split('<<',1) if x).strip(); q=lines[i+1] if i+1<len(lines) else ''
   if len(q)>=27:
    out.update(passport_no=q[:9].replace('<',''),nationality=q[10:13].replace('<',''),birth_date=fmt(q[13:19]),gender=q[20:21].replace('<',''),expiry_date=fmt(q[21:27],'expiry'))
   break
 out['name']=printed_name(text) or out.get('mrz_name',''); return out
def ocr(raw,mime,name='passport'):
 key=os.environ.get('OCR_SPACE_API_KEY','')
 if not key: raise RuntimeError('OCR_SPACE_API_KEY is not configured')
 # Preserve a real extension and also tell OCR.Space the type explicitly.
 # Browser/Vercel uploads can otherwise arrive with a generic or missing MIME.
 name=os.path.basename(name or 'passport').strip() or 'passport'
 mime=(mime or '').split(';',1)[0].lower().strip()
 ext=os.path.splitext(name)[1].lower()
 by_mime={'application/pdf':('.pdf','PDF'),'image/jpeg':('.jpg','JPG'),'image/jpg':('.jpg','JPG'),'image/png':('.png','PNG')}
 by_ext={'.pdf':('application/pdf','PDF'),'.jpg':('image/jpeg','JPG'),'.jpeg':('image/jpeg','JPG'),'.png':('image/png','PNG')}
 if ext in by_ext:
  safe_mime,filetype=by_ext[ext]
 elif mime in by_mime:
  safe_ext,filetype=by_mime[mime]; safe_mime=mime; name=name+safe_ext
 else:
  raise RuntimeError('Unsupported passport file type. Please upload PDF, JPG, JPEG, or PNG.')
 files={'file':(name,raw,safe_mime)}
 payload={'apikey':key,'language':'eng','isOverlayRequired':'false','OCREngine':'2','scale':'true','filetype':filetype}
 r=requests.post('https://api.ocr.space/parse/image',files=files,data=payload,timeout=55)
 try: j=r.json()
 except Exception: raise RuntimeError(f'OCR service returned HTTP {r.status_code}')
 if r.status_code>=400 or j.get('IsErroredOnProcessing'): raise RuntimeError(str(j.get('ErrorMessage') or j.get('ErrorDetails') or f'OCR failed (HTTP {r.status_code})'))
 text='\n'.join(x.get('ParsedText','') for x in j.get('ParsedResults',[]))
 if not text.strip(): raise RuntimeError('OCR completed but returned no readable text')
 d=parse_mrz(text); d['raw_text']=text[:5000]; return d

@app.before_request
def setup():
 if request.path.startswith('/api/'): init()
@app.route('/api/health')
def health(): return jsonify(ok=True,ocr=bool(os.environ.get('OCR_SPACE_API_KEY')),database=bool(DB),blob=blob_configured())
@app.route('/api/packages')
def packages():
 with conn() as c:
  with c.cursor() as q:q.execute('select * from packages order by start_date'); return jsonify(q.fetchall())
@app.route('/api/login',methods=['POST'])
def login():
 d=request.get_json() or {}
 with conn() as c:
  with c.cursor() as q:q.execute('select * from users where email=%s',(d.get('email',''),)); u=q.fetchone()
 if u and checkpw(d.get('password',''),u['password']): session['user']={k:u[k] for k in ['id','name','email','role']}; return jsonify(ok=True)
 return jsonify(error='بيانات الدخول غير صحيحة'),401
@app.route('/api/logout',methods=['POST'])
def logout(): session.clear(); return jsonify(ok=True)
@app.route('/api/me')
def me(): return jsonify(auth() or {})
@app.route('/api/ocr',methods=['POST'])
def doocr():
 try:
  d=request.get_json() or {}; raw=base64.b64decode(d['data'].split(',')[-1]); return jsonify(ocr(raw,d.get('mime','image/jpeg'),d.get('name','passport')))
 except Exception as e:return jsonify(error='تعذر قراءة الملف',detail=str(e)),400
@app.route('/api/book',methods=['POST'])
def book():
 d=request.get_json() or {}; rt=d.get('room_type'); caps={'double':2,'triple':3,'quad':4}; cols={'double':'rooms_double','triple':'rooms_triple','quad':'rooms_quad'}
 try:
  n=int(d['passengers']); cap=caps[rt]
  if n>cap:return jsonify(error=f'نوع الغرفة المختار يسمح بحد أقصى {cap} مسافر/مسافرين. اختر نوع غرفة أكبر أو اعمل حجزاً منفصلاً لغرفة إضافية.',room_capacity=cap),400
  with conn() as c:
   with c.cursor() as q:
    q.execute('select * from packages where id=%s for update',(d['package_id'],)); p=q.fetchone(); col=cols[rt]
    if not p or p['seats_total']-p['seats_booked']<n or p[col]<1:
     q.execute('insert into waitlist(package_id,name,phone,passengers,room_type) values(%s,%s,%s,%s,%s)',(d['package_id'],d['name'],d['phone'],n,rt)); return jsonify(waitlist=True)
    code='BT-'+secrets.token_hex(3).upper(); q.execute('insert into bookings(code,package_id,customer_name,phone,email,room_type,passengers,status,payment) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id',(code,d['package_id'],d['name'],d['phone'],d.get('email',''),rt,n,'reserved','pending')); bid=q.fetchone()['id']
    q.execute(f'update packages set seats_booked=seats_booked+%s, {col}={col}-1 where id=%s',(n,d['package_id']))
    for x in d.get('passenger_data',[]):
     blob_path=None; pn=''; pm=''
     if x.get('passport_data'):
      raw=base64.b64decode(x['passport_data'].split(',')[-1]); pn=x.get('passport_name','passport.pdf'); pm=x.get('passport_mime','application/octet-stream')
      ext=os.path.splitext(pn)[1][:10] or '.bin'; blob_path=blob_put_bytes(f'passports/{code}/{secrets.token_hex(8)}{ext}',raw,pm)
     q.execute('insert into passengers(booking_id,name,passport_no,nationality,birth_date,expiry_date,gender,passport_name,passport_mime,passport_blob_path,ocr_status) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(bid,x.get('name',''),x.get('passport_no',''),x.get('nationality',''),x.get('birth_date',''),x.get('expiry_date',''),x.get('gender',''),pn,pm,blob_path,'reviewed'))
  return jsonify(ok=True,code=code)
 except Exception as e:return jsonify(error=str(e)),400

def need():
 if not auth(): return (jsonify(error='unauthorized'),401)
@app.route('/api/admin/summary')
def summary():
 x=need()
 if x:return x
 with conn() as c:
  with c.cursor() as q:
   q.execute("select count(*) n from packages where status='active'"); packs=q.fetchone()['n']; q.execute('select count(*) n from bookings'); books=q.fetchone()['n']; q.execute('select coalesce(sum(passengers),0) n from bookings'); ps=q.fetchone()['n']; q.execute('select count(*) n from waitlist'); wl=q.fetchone()['n']; q.execute('select b.*,p.title_ar from bookings b left join packages p on p.id=b.package_id order by b.id desc limit 8'); recent=q.fetchall(); q.execute('select * from packages order by start_date'); pp=q.fetchall()
 return jsonify(packages=packs,bookings=books,passengers=ps,waitlist=wl,recent=recent,packs=pp)
@app.route('/api/admin/bookings')
def bookings():
 x=need()
 if x:return x
 with conn() as c:
  with c.cursor() as q:
   q.execute('select b.*,p.title_ar from bookings b left join packages p on p.id=b.package_id order by b.id desc'); rows=q.fetchall()
   for b in rows:q.execute('select id,booking_id,name,passport_no,nationality,birth_date,expiry_date,gender,ocr_status,visa_status,passport_name from passengers where booking_id=%s order by id',(b['id'],)); b['passenger_data']=q.fetchall()
 return jsonify(rows)
@app.route('/api/admin/passengers')
def passengers():
 x=need()
 if x:return x
 with conn() as c:
  with c.cursor() as q:q.execute('select ps.id,ps.booking_id,ps.name,ps.passport_no,ps.nationality,ps.birth_date,ps.expiry_date,ps.gender,ps.ocr_status,ps.visa_status,ps.passport_name,b.code,b.phone,b.email,p.title_ar from passengers ps join bookings b on b.id=ps.booking_id left join packages p on p.id=b.package_id order by ps.id desc'); return jsonify(q.fetchall())
@app.route('/api/admin/waitlist')
def waitlist():
 x=need()
 if x:return x
 with conn() as c:
  with c.cursor() as q:q.execute('select w.*,p.title_ar from waitlist w left join packages p on p.id=w.package_id order by w.id desc'); return jsonify(q.fetchall())
@app.route('/api/admin/users')
def users():
 x=need()
 if x:return x
 with conn() as c:
  with c.cursor() as q:q.execute('select id,name,email,role from users order by id'); return jsonify(q.fetchall())
@app.route('/api/admin/passenger-download/<int:pid>')
def dl(pid):
 x=need()
 if x:return x
 with conn() as c:
  with c.cursor() as q:q.execute('select ps.*,b.code,b.phone,b.email,p.title_ar from passengers ps join bookings b on b.id=ps.booking_id left join packages p on p.id=b.package_id where ps.id=%s',(pid,)); d=q.fetchone()
 if not d:return jsonify(error='not found'),404
 safe=re.sub(r'[^A-Za-z0-9._ -]+','_',d.get('name') or f'passenger-{pid}').strip(); info={k:v for k,v in d.items() if k not in ('passport_blob','passport_blob_path')}; out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr(f'{safe}/Passenger-Data.txt','BUILDER TRAVEL - PASSENGER FILE\n\n'+'\n'.join(f'{k}: {v or ""}' for k,v in info.items()))
  z.writestr(f'{safe}/Passenger-Data.json',json.dumps(info,ensure_ascii=False,indent=2,default=str))
  passport_bytes=blob_get_bytes(d.get('passport_blob_path')) if d.get('passport_blob_path') else (bytes(d['passport_blob']) if d.get('passport_blob') else None)
  if passport_bytes: z.writestr(f'{safe}/{d.get("passport_name") or "Passport"}',passport_bytes)
 data=out.getvalue(); return Response(data,mimetype='application/zip',headers={'Content-Disposition':f'attachment; filename="{safe}.zip"'})
@app.route('/api/admin/passenger-update',methods=['POST'])
def pup():
 x=need()
 if x:return x
 d=request.get_json() or {}
 with conn() as c:
  with c.cursor() as q:q.execute('update passengers set visa_status=%s where id=%s',(d.get('visa_status','pending'),int(d['id']))); q.execute('insert into audit("user",action) values(%s,%s)',(auth()['email'],f"Updated passenger {d['id']} visa to {d.get('visa_status')}"))
 return jsonify(ok=True)
@app.route('/api/admin/booking-update',methods=['POST'])
def bup():
 x=need()
 if x:return x
 d=request.get_json() or {}
 with conn() as c:
  with c.cursor() as q:q.execute('update bookings set status=%s,payment=%s where id=%s',(d.get('status','reserved'),d.get('payment','pending'),int(d['id'])))
 return jsonify(ok=True)
@app.route('/api/admin/user',methods=['POST'])
def adduser():
 x=need()
 if x:return x
 d=request.get_json() or {}
 try:
  with conn() as c:
   with c.cursor() as q:q.execute('insert into users(name,email,password,role) values(%s,%s,%s,%s)',(d['name'],d['email'],hashpw(d['password']),d.get('role','reservations')))
  return jsonify(ok=True)
 except Exception as e:return jsonify(error=str(e)),400
@app.route('/api/admin/package',methods=['POST'])
def package():
 x=need()
 if x:return x
 d=request.get_json() or {}
 try:
  with conn() as c:
   with c.cursor() as q:
    if d.get('id'):
     q.execute('update packages set seats_total=%s,rooms_double=%s,rooms_triple=%s,rooms_quad=%s,status=%s where id=%s',(d['seats_total'],d['rooms_double'],d['rooms_triple'],d['rooms_quad'],d['status'],d['id'])); action='Updated package inventory'; newid=d['id']
    else:
     image=d.get('image') or secrets.choice(['kaaba','medina','kaaba2','hajj']); vals=(d['title_ar'],d['title_en'],d['kind'],int(d['days']),d['start_date'],d['end_date'],d['airline'],int(d['seats_total']),0,int(d.get('price_double',0)),int(d.get('price_triple',0)),int(d.get('price_quad',0)),int(d.get('rooms_double',0)),int(d.get('rooms_triple',0)),int(d.get('rooms_quad',0)),d.get('status','active'),image,d.get('hotel_makkah',''),d.get('hotel_madinah',''))
     q.execute('''insert into packages(title_ar,title_en,kind,days,start_date,end_date,airline,seats_total,seats_booked,price_double,price_triple,price_quad,rooms_double,rooms_triple,rooms_quad,status,image,hotel_makkah,hotel_madinah) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id''',vals); newid=q.fetchone()['id']; action='Created package '+d['title_ar']
    q.execute('insert into audit("user",action) values(%s,%s)',(auth()['email'],action))
  return jsonify(ok=True,id=newid)
 except Exception as e:return jsonify(error=str(e)),400


@app.route('/api/index.py', methods=['GET','POST'])
def vercel_api_gateway():
    """Vercel Python entrypoint gateway. Public JS sends the intended API path in ?route=."""
    target=request.args.get('route','')
    routes={
      '/api/health':health,'/api/packages':packages,'/api/login':login,'/api/logout':logout,'/api/me':me,
      '/api/ocr':doocr,'/api/book':book,'/api/admin/summary':summary,'/api/admin/bookings':bookings,
      '/api/admin/passengers':passengers,'/api/admin/waitlist':waitlist,'/api/admin/users':users,
      '/api/admin/passenger-update':pup,'/api/admin/booking-update':bup,'/api/admin/user':adduser,'/api/admin/package':package
    }
    if target.startswith('/api/admin/passenger-download/'):
        try: return dl(int(target.rsplit('/',1)[-1]))
        except (TypeError,ValueError): return jsonify(error='invalid passenger id'),400
    fn=routes.get(target)
    if not fn: return jsonify(error='API route not found',route=target),404
    return fn()
