"""Public GitHub imports resolve to one commit and reuse the bounded ZIP importer."""
import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .security import public_https
from .uploads import MAX_UPLOAD, import_zip, upload_id


def _repository(url, ref):
    if not isinstance(url,str) or len(url)>600 or any(c in url for c in '\\%\r\n\t\x00'):
        raise ValueError('لینک مخزن عمومی GitHub معتبر نیست.')
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.query or parsed.fragment):
        raise ValueError('فقط لینک مخزن عمومی در github.com پذیرفته می‌شود.')
    parts=parsed.path.strip('/').split('/')
    if len(parts)<2 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}',parts[0]):
        raise ValueError('لینک مخزن عمومی GitHub معتبر نیست.')
    owner,repo=parts[:2]
    if repo.endswith('.git'):repo=repo[:-4]
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',repo) or repo in {'.','..'}:
        raise ValueError('نام مخزن GitHub معتبر نیست.')
    if len(parts)>2:
        if len(parts)<4 or parts[2]!='tree' or ref is not None:
            raise ValueError('لینک ریشهٔ مخزن یا شاخهٔ آن را وارد کن.')
        ref='/'.join(parts[3:])
    ref='HEAD' if ref is None else ref
    if (not isinstance(ref,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}',ref)
            or any(part in {'','.','..'} for part in ref.split('/')) or '..' in ref or ref.endswith('.lock')):
        raise ValueError('شاخه یا نسخهٔ مخزن معتبر نیست.')
    return owner,repo,ref


def _status(response,expected):
    if response.status_code in {403,429}:
        raise ValueError('GitHub فعلاً اجازهٔ دریافت نمی‌دهد؛ کمی بعد دوباره تلاش کن یا ZIP را بارگذاری کن.')
    if response.status_code==404:
        raise ValueError('مخزن یا نسخه پیدا نشد؛ مخزن باید عمومی باشد. می‌توانی ZIP را بارگذاری کنی.')
    if response.status_code!=expected:
        raise ValueError('دریافت از GitHub کامل نشد؛ دوباره تلاش کن یا ZIP را بارگذاری کن.')


def _request(client,url):
    destination=public_https(url)
    # Pin the validated public IP while preserving TLS certificate/SNI and virtual host.
    pinned=httpx.URL(url).copy_with(host=destination['ip'])
    return client.stream('GET',pinned,headers={'Host':destination['hostname']},
                         extensions={'sni_hostname':destination['hostname']},follow_redirects=False)


def _chunks(response,limit,deadline):
    length=response.headers.get('content-length')
    if length is not None and (not length.isdecimal() or int(length)>limit):
        raise ValueError('حجم دریافت از GitHub بیش از حد مجاز است.')
    total=0
    for chunk in response.iter_bytes(65536):
        total+=len(chunk)
        if total>limit:raise ValueError('حجم دریافت از GitHub بیش از حد مجاز است.')
        if time.monotonic()>deadline:raise ValueError('مهلت دریافت از GitHub تمام شد؛ ZIP را بارگذاری کن.')
        yield chunk


def import_github(url,data_dir,vault,transport=None,ref=None):
    """Import public source without credentials, cloning, installing, or executing code."""
    owner,repo,ref=_repository(url,ref)
    api=f'https://api.github.com/repos/{owner}/{repo}'
    deadline=time.monotonic()+45
    data_dir=Path(data_dir)
    result=None
    try:
        with httpx.Client(timeout=httpx.Timeout(12,connect=8),trust_env=False,follow_redirects=False,
                          transport=transport,headers={'Accept':'application/vnd.github+json',
                          'X-GitHub-Api-Version':'2022-11-28','User-Agent':'Lyara-Public-Source'}) as client:
            with _request(client,api+'/commits/'+quote(ref,safe='')) as response:
                _status(response,200)
                try:payload=json.loads(b''.join(_chunks(response,1024**2,deadline)))
                except (json.JSONDecodeError,UnicodeError):
                    raise ValueError('پاسخ GitHub برای شناسایی نسخه قابل خواندن نبود.') from None
            sha=payload.get('sha') if isinstance(payload,dict) else None
            if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-fA-F]{40}',sha):
                raise ValueError('GitHub نسخهٔ ثابت و معتبری برای این مخزن برنگرداند.')
            sha=sha.lower()
            with _request(client,api+'/zipball/'+sha) as response:
                _status(response,302)
                location=response.headers.get('location','')
            parsed=urlsplit(location)
            if (parsed.scheme!='https' or parsed.netloc!='codeload.github.com' or parsed.query or parsed.fragment
                    or parsed.path.casefold() not in {f'/{owner}/{repo}/legacy.zip/{sha}'.casefold(),
                                                    f'/{owner}/{repo}/zip/{sha}'.casefold()}):
                raise ValueError('آدرس بستهٔ GitHub با نسخهٔ ثابت مخزن مطابقت ندارد.')
            # A private temporary directory also makes NamedTemporaryFile reopening portable on Windows.
            data_dir.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='github-',dir=data_dir) as temporary:
                archive=Path(temporary)/'source.zip'
                with _request(client,location) as response:
                    _status(response,200)
                    with archive.open('xb') as output:
                        for chunk in _chunks(response,MAX_UPLOAD,deadline):output.write(chunk)
                result=import_zip(archive,repo+'.zip',data_dir,vault)
            provenance={'repository':f'https://github.com/{owner}/{repo}','commit':sha,'ref':ref}
            metadata=data_dir/'uploads'/upload_id(result['source'])/'upload.json'
            saved=json.loads(metadata.read_text(encoding='utf-8'))
            saved['github']=provenance
            pending=metadata.with_suffix('.tmp')
            pending.write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
            pending.replace(metadata)
            return {**result,'github':provenance}
    except (OSError,httpx.HTTPError,ValueError) as error:
        if result:
            directory=(data_dir/'uploads'/upload_id(result['source'])).resolve()
            if directory.parent==(data_dir/'uploads').resolve():shutil.rmtree(directory)
        if isinstance(error,ValueError):raise
        raise ValueError('ارتباط با GitHub کامل نشد؛ دوباره تلاش کن یا ZIP را بارگذاری کن.') from None
