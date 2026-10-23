"""Project-owned configuration, kept apart from plans and deployment archives."""
import ast
import hashlib
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values

from .project import EXCLUDED_DIRS, SENSITIVE
from .security import redact

NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,99}$')
RESERVED = {'PORT','SERVER_PORT','HOST','NODE_ENV','PATH','PYTHONPATH','PYTHONHOME','NODE_OPTIONS','LD_PRELOAD','LD_LIBRARY_PATH','HOME','USERPROFILE','COMSPEC','SHELL','BASH_ENV','ENV'}

def allowed_name(name):
    return isinstance(name,str) and bool(NAME.fullmatch(name)) and name.upper() not in RESERVED and not name.upper().startswith(('LYARA_','LIARA_'))


def usable(value):
    return isinstance(value,str) and bool(value.strip()) and len(value)<=32000 and not re.search(
        r'\$\{|^(?:your[_ -]|replace[_ -]|change[_ -]?me|<[^>]+>$|\[redacted\]$)',value,re.I)


def read_source_env(root):
    root=Path(root).resolve()
    values={}
    for name in ['.env','.env.local','.env.production','.env.production.local']:
        path=root/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
            continue
        if not path.resolve().is_relative_to(root):
            continue
        # Interpolation never consults the host environment.
        values.update({key:value for key,value in dotenv_values(path,interpolate=False).items() if allowed_name(key) and usable(value)})
    return values


def input_label(name):
    upper=name.upper()
    if any(token in upper for token in ('DATABASE_URL','DB_URL','MONGO_URI','REDIS_URL','CONNECTION_STRING')):
        return {'name':name,'label':'آدرس اتصال '+('حافظهٔ موقت' if 'REDIS' in upper else 'دیتابیس'),'hint':'آدرس اتصال را از سرویس دیتابیس کپی کن.','kind':'connection'}
    if any(token in upper for token in ('PASSWORD','PASSWD','SECRET','TOKEN','API_KEY')):
        return {'name':name,'label':'کلید دسترسی '+re.sub(r'[_-]+',' ',re.sub(r'(?i)(_?API_?KEY|_?TOKEN|_?SECRET|_?PASSWORD|_?PASSWD)$','',name)).strip(),'hint':'این مقدار فقط برای اجرای سایت ذخیره می‌شود.','kind':'secret'}
    return {'name':name,'label':'مقدار '+name,'hint':'این اطلاعات در تنظیمات سایت استفاده می‌شود.','kind':'text'}


def discover_inputs(root, secrets=(), excluded_roots=()):
    root=Path(root)
    excluded_roots=[Path(p).resolve() for p in excluded_roots]
    declared=set()
    for name in ['.env.example','.env.sample','.env.template']:
        path=root/name
        if not path.is_symlink() and path.is_file() and path.stat().st_size<=65536:
            declared.update(key for key in dotenv_values(path,interpolate=False) if allowed_name(key))
    candidates={}
    defaults=set()
    total=0
    # ponytail: bounded lexical scan identifies candidate names; the planner decides whether code-only reads are required.
    for directory,dirs,files in os.walk(root,followlinks=False):
        path=Path(directory)
        dirs[:]=sorted(d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith('.') and not (path/d).is_symlink() and not any((path/d).resolve().is_relative_to(private) for private in excluded_roots))
        for name in sorted(files):
            file=path/name
            if file.is_symlink() or SENSITIVE.search(name) or file.suffix not in {'.py','.js','.mjs','.cjs','.ts','.tsx','.jsx','.php','.rb'}:
                continue
            size=file.stat().st_size
            if size>100000 or total+size>1000000:
                continue
            total+=size
            content=file.read_text(encoding='utf-8-sig',errors='replace')
            if file.suffix=='.py':
                try:
                    for call in ast.walk(ast.parse(content)):
                        if (isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute)
                                and ast.unparse(call.func) in {'os.getenv','os.environ.get'} and len(call.args)>1
                                and isinstance(call.args[0],ast.Constant) and isinstance(call.args[0].value,str)
                                and isinstance(call.args[1],ast.Constant) and call.args[1].value not in (None,'')):
                            defaults.add(call.args[0].value)
                except SyntaxError:
                    pass
            for fallback in re.finditer(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|\||\?\?)\s*(?:[\"'][^\"']+[\"']|[0-9]+|true|false)",content):
                defaults.add(fallback[1])
            for line in content.splitlines():
                names=re.findall(r'(?:process\.env\.|import\.meta\.env\.)([A-Za-z_][A-Za-z0-9_]*)|(?:process\.env|os\.environ)\s*\[\s*[\'"]([A-Za-z_][A-Za-z0-9_]*)[\'"]\s*\]|(?:os\.getenv|os\.environ\.get|env)\s*\(\s*[\'"]([A-Za-z_][A-Za-z0-9_]*)[\'"]',line)
                for match in names:
                    key=next(value for value in match if value)
                    if allowed_name(key) and len(candidates)<100:
                        safe=redact(line.strip(),secrets)[:500]
                        candidates.setdefault(key,{'name':key,'path':file.relative_to(root).as_posix(),'usage':safe})
    return sorted(declared-defaults),list(candidates.values())


class InputVault:
    def __init__(self,root):
        root=Path(root)
        self.path=root/'inputs.sqlite'
        keypath=root/'inputs.key'
        if not keypath.exists():
            try:
                fd=os.open(keypath,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as handle:handle.write(Fernet.generate_key())
            except FileExistsError:
                pass
        if keypath.is_symlink():raise ValueError('محل ذخیرهٔ اطلاعات سایت معتبر نیست.')
        self.cipher=Fernet(keypath.read_bytes())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS inputs (project TEXT, name TEXT, encrypted BLOB, source TEXT, PRIMARY KEY(project,name))')
            db.execute('CREATE TABLE IF NOT EXISTS retired (project TEXT, encrypted BLOB)')
        os.chmod(self.path,0o600)

    @staticmethod
    def reference(project,name):
        return 'LYARA_DEPLOY_INPUT_'+hashlib.sha256((project+'\0'+name).encode()).hexdigest()[:24].upper()

    def put(self,project,values,source):
        if len(values)>100 or any(not allowed_name(k) or not usable(v) for k,v in values.items()):
            raise ValueError('اطلاعات واردشده کامل یا معتبر نیست.')
        with closing(sqlite3.connect(self.path)) as db, db:
            for name,value in values.items():
                previous=db.execute('SELECT encrypted FROM inputs WHERE project=? AND name=?',(project,name)).fetchone()
                if previous and self.cipher.decrypt(previous[0]).decode()!=value:
                    db.execute('INSERT INTO retired VALUES (?,?)',(project,previous[0]))
                db.execute('INSERT OR REPLACE INTO inputs VALUES (?,?,?,?)',(project,name,self.cipher.encrypt(value.encode()),source))

    def values(self,project):
        with closing(sqlite3.connect(self.path)) as db, db:rows=db.execute('SELECT name,encrypted,source FROM inputs WHERE project=?',(project,)).fetchall()
        try:
            return {name:{'value':self.cipher.decrypt(value).decode(),'source':source} for name,value,source in rows}
        except (InvalidToken,UnicodeError):
            raise ValueError('اطلاعات ذخیره‌شده قابل خواندن نیست؛ فایل کلید ذخیره‌سازی را بازیابی کن.') from None

    def secrets(self,project=None):
        with closing(sqlite3.connect(self.path)) as db:
            if project is None:
                rows=db.execute('SELECT encrypted FROM inputs UNION ALL SELECT encrypted FROM retired').fetchall()
            else:
                rows=db.execute('SELECT encrypted FROM inputs WHERE project=? UNION ALL SELECT encrypted FROM retired WHERE project=?',(project,project)).fetchall()
        return [self.cipher.decrypt(row[0]).decode() for row in rows]

    def resolve(self,project,reference):
        for name,item in self.values(project).items():
            if self.reference(project,name)==reference:return item['value']
        if reference.startswith('LYARA_DEPLOY_INPUT_'):
            raise ValueError('اطلاعات لازم برای سایت پیدا نشد؛ دوباره وارد کن.')
        if reference not in os.environ:raise ValueError('اطلاعات لازم برای اجرای سایت هنوز وارد نشده است.')
        return os.environ[reference]
