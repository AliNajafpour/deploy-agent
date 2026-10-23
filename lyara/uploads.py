"""Bounded ZIP import. Uploaded code is data and is never executed on this host."""
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from .inputs import read_source_env
from .project import EXCLUDED_DIRS

MAX_UPLOAD=50*1024**2
MAX_TOTAL=250*1024**2
MAX_FILE=25*1024**2
MAX_ENTRIES=20000
ENV_FILES={'.env','.env.local','.env.production','.env.production.local'}
RESERVED=re.compile(r'^(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)',re.I)


def upload_id(source):
    if not isinstance(source,str) or not re.fullmatch(r'upload:[0-9a-f]{32}',source):
        raise ValueError('فایل بارگذاری‌شده پیدا نشد؛ دوباره بارگذاری کن.')
    return source[7:]


def upload_root(data_dir,source):
    identifier=upload_id(source)
    directory=Path(data_dir).resolve()/'uploads'/identifier
    try:
        meta=json.loads((directory/'upload.json').read_text(encoding='utf-8'))
        root=(directory/'files'/meta['root']).resolve()
        if not root.is_relative_to((directory/'files').resolve()) or not root.is_dir():raise ValueError()
        return root
    except (OSError,KeyError,TypeError,ValueError):
        raise ValueError('فایل بارگذاری‌شده پیدا نشد؛ دوباره بارگذاری کن.') from None


def list_uploads(data_dir):
    result=[]
    parent=Path(data_dir)/'uploads'
    if not parent.is_dir():return result
    for directory in sorted(parent.iterdir(),key=lambda p:p.name):
        if directory.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}',directory.name):continue
        try:
            meta=json.loads((directory/'upload.json').read_text(encoding='utf-8'))
            upload_root(data_dir,'upload:'+directory.name)
            result.append({key:meta[key] for key in ['source','label','files','bytes']})
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return result


def upload_modes(data_dir,source):
    upload_root(data_dir,source)
    return json.loads((Path(data_dir)/'uploads'/upload_id(source)/'upload.json').read_text(encoding='utf-8')).get('file_modes',{})


def import_zip(archive_path,filename,data_dir,vault):
    filename=unquote(filename or '')
    if (not filename.lower().endswith('.zip') or len(filename)>200 or any(c in filename for c in '/\\\x00\r\n')):
        raise ValueError('یک فایل ZIP بدون رمز انتخاب کن.')
    identifier=uuid.uuid4().hex
    directory=Path(data_dir).resolve()/'uploads'/identifier
    files_root=directory/'files'
    files_root.mkdir(parents=True,mode=0o700)
    os.chmod(directory,0o700)
    count=total=0
    modes={}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members=archive.infolist()
            if not members or len(members)>MAX_ENTRIES:raise ValueError('فایل ZIP خالی است یا تعداد فایل‌هایش بیش از حد مجاز است.')
            names=set()
            for info in members:
                name=info.orig_filename
                path=PurePosixPath(name)
                parts=path.parts
                if (not parts or path.is_absolute() or '\\' in name or '\x00' in name
                        or len(name)>500 or len(parts)>20 or any(part in {'.','..'} or ':' in part
                        or part.endswith((' ','.')) or RESERVED.match(part) for part in parts)):
                    raise ValueError('یکی از مسیرهای داخل ZIP معتبر نیست.')
                normalized='/'.join(parts).casefold()
                if normalized in names:raise ValueError('فایل‌های تکراری یا هم‌نام داخل ZIP وجود دارند.')
                names.add(normalized)
                kind=stat.S_IFMT(info.external_attr>>16)
                if kind not in (0,stat.S_IFREG,stat.S_IFDIR) or info.flag_bits&1:
                    raise ValueError('ZIP نباید لینک یا فایل رمزدار داشته باشد.')
                if any(part in EXCLUDED_DIRS or part=='__MACOSX' or part.startswith('.venv') for part in parts):continue
                if info.is_dir():continue
                if info.file_size>MAX_FILE or total+info.file_size>MAX_TOTAL:
                    raise ValueError('حجم فایل‌های داخل ZIP بیش از حد مجاز است.')
                target=files_root.joinpath(*parts)
                target.parent.mkdir(parents=True,exist_ok=True)
                actual=0
                with archive.open(info) as source,target.open('xb') as output:
                    while chunk:=source.read(65536):
                        actual+=len(chunk)
                        if actual>MAX_FILE or total+actual>MAX_TOTAL:raise ValueError('حجم فایل‌های داخل ZIP بیش از حد مجاز است.')
                        output.write(chunk)
                if actual!=info.file_size:raise ValueError('فایل ZIP کامل نیست.')
                modes[target]=0o755 if info.external_attr>>16&0o111 else 0o644
                total+=actual;count+=1
        if not count:raise ValueError('فایل ZIP باید کد سایت را داشته باشد.')
        root=files_root
        # A conventional single project wrapper is removed; no script or package hook runs.
        while True:
            entries=list(root.iterdir())
            if len(entries)!=1 or not entries[0].is_dir():break
            root=entries[0]
        values=read_source_env(root)
        if values:vault.put('upload-'+identifier,values,'env-file')
        for path in files_root.rglob('*'):
            if path.is_file() and path.name.casefold() in ENV_FILES:path.unlink()
        if not any(path.is_file() for path in root.rglob('*')):
            raise ValueError('فایل ZIP باید کد سایت را داشته باشد.')
        meta={'source':'upload:'+identifier,'label':filename[:-4].strip() or 'سایت من','files':count,
              'bytes':total,'root':root.relative_to(files_root).as_posix(),
              'file_modes':{path.relative_to(root).as_posix():mode for path,mode in modes.items() if path.is_file()}}
        (directory/'upload.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
        return {key:meta[key] for key in ['source','label','files','bytes']}
    except Exception as error:
        if directory.resolve().parent != (Path(data_dir).resolve()/'uploads'):raise RuntimeError('Invalid upload cleanup path') from error
        shutil.rmtree(directory)
        if isinstance(error,ValueError):raise
        raise ValueError('فایل ZIP قابل خواندن نیست؛ دوباره آن را بساز و بارگذاری کن.') from None
