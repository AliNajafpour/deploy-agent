# Lyara | دستیار انتشار و نگهداری لیارا

Lyara پروژه را بررسی می‌کند، برای دیپلوی یا تغییر زیرساخت طرح می‌دهد، منتظر
تأیید انسان می‌ماند و پس از اجرا نتیجه را مستقل بررسی می‌کند. رابط فارسی، CLI
و MCP از یک هسته استفاده می‌کنند. انتخاب مدل از عملیات لیارا جداست.

## وضعیت فعلی

- بررسی پروژه و ساخت بستهٔ immutable با فهرست فایل‌ها و SHA-256.
- ایجاد برنامه، آپلود سورس، ایجاد release و انتظار برای همان release ID.
- خواندن لاگ، رویداد، نسخه‌ها و دادهٔ CPU/RAM، با نمایش خطا و نبود دسترسی.
- ری‌استارت، روشن/خاموش‌کردن، تغییر پلن و به‌روزرسانی امن متغیرهای محیطی.
- اتصال دامنه، ایجاد zone/رکورد DNS و درخواست SSL از APIهای مستند.
- توقف LangGraph برای تأیید، ادامه پس از restart و ثبت اقدامات در SQLite.
- مدل API سازگار با OpenAI Chat Completions یا ورود موجود Codex/Claude Code.
- ابزارهای MCP بدون نیاز به مدل داخلی و بدون ابزار دورزدن تأیید انسان.
- پاسخ به سؤال ایجنت با حفظ درخواست، پروژه و ارتباط با بررسی قبلی؛ طرح تازه تأیید مستقل می‌گیرد.
- پیش‌نمایش اثر، ریسک و راه بازیابی هر اقدام؛ طرح بازگشت فقط سورس قبلی با تأیید تازه.
- گزارش JSON/Markdown از شواهد قبل/بعد، هش سورس، تأیید و دفتر اقدامات.
- بررسی دوبارهٔ release با شناسهٔ ثبت‌شده، بدون تکرار عملیات نوشتنی پس از timeout.

طبق تصمیم پروژه، تست و ارائه به‌صورت محلی انجام می‌شود و آزمون روی حساب لیارا
جزو کارهای باقی‌مانده نیست. برنامه‌های آزمایشی واقعاً اجرا می‌شوند؛ API ابری
شبیه‌سازی می‌شود و سازگاری با حساب زنده ادعا نمی‌شود.

ایجنت می‌تواند مانع انتشار را در فایل‌های استقرار و نقطهٔ شروع بک‌اند اصلاح کند:
فرمان شروع، پورت یا آدرس bind، خطای نحو فایل شروع و وابستگی جاافتاده در manifest.
در حالت «رفع مانع انتشار»، فایل‌های مجاز و لاگ بررسی می‌شوند؛ بستهٔ اصلاحی، diff
و بررسی ساختاری پیش از تأیید نمایش داده می‌شوند. پس از تأیید، همان بسته ساخته،
منتشر و سلامت آن بررسی می‌شود. سورس اصلی تغییر نمی‌کند؛ اصلاح داخل بستهٔ جداست.
نصب وابستگی بر عهدهٔ builder انتشار است؛ محیط Lyara پکیج پروژه را نصب نمی‌کند.
تغییر قابلیت سایت و رابط کاربری در این مسیر مجاز نیست. اصلاح وابستگی در پروژه‌های
دارای lockfile نیازمند بازتولید معتبر فایل قفل است و فعلاً خودکار انجام نمی‌شود.
[جزئیات و آزمون‌های اصلاح انتشار](docs/deployment-repair.md).

## مدل پیش‌فرض این تحویل

مدل روی `openai/gpt-6-luna` از BoxAPI، با provider برابر `api` و آدرس پایهٔ
`https://ai.boxapi.ir/ai/v1` تنظیم شده است. کلید فقط در `LYARA_LLM_API_KEY`
داخل `.env` محلی قرار می‌گیرد و همراه کد منتشر نمی‌شود. اتصال Codex و Claude
همچنان از طریق تغییر تنظیمات provider در دسترس است. مدل هوش مصنوعی و سرویس
استقرار لیارا دو تنظیم مستقل‌اند.

در آخرین بررسی این تحویل، فقط یک سناریوی سادهٔ انتشار استاتیک و یک سناریوی
سختِ اصلاح وابستگی بک‌اند با Luna بررسی شدند؛ هر دو پاس شدند. بقیهٔ سناریوها
دوباره اجرا نشدند. [گزارش این دو آزمون](docs/boxapi-verification.json).

## اجرای سریع در همین کامپیوتر

محیط `.venv` از wheelهای پروژهٔ همسایه ساخته شده؛ دانلود دوباره لازم نیست.

```powershell
.\.venv\Scripts\python.exe -m lyara --demo serve --port 8791
```

رابط را در `http://127.0.0.1:8791` باز کنید. حالت آفلاین در بالای صفحه مشخص است.
پروژهٔ نمونهٔ بدون وابستگی در `examples/hello-node` قرار دارد. درخواست، بازبینی
طرح، تأیید و بررسی نتیجه از همان هستهٔ واقعی عبور می‌کنند؛ پاسخ‌های ابری و مدل
در این حالت شبیه‌سازی شده‌اند. دمو هیچ حساب ابری را تغییر نمی‌دهد.

در کامپیوتری دیگر، Python 3.11 یا بالاتر و `pip install -r requirements.txt`
یا `pip install -e .` کافی است. برای نصب آفلاین با wheelhouse موجود:

```powershell
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
uv pip install --python .venv\Scripts\python.exe --no-index --find-links ..\wallex\runs\portable-wheels -r requirements.txt
```

## اتصال واقعی

`.env.example` را به `.env` کپی کنید و کلید را فقط در همان فایل قرار دهید:

```dotenv
LIARA_API_TOKEN=your-liara-token
LYARA_WORKSPACE=.
LYARA_DATA_DIR=.lyara
LYARA_PROVIDER=api
LYARA_MODEL=openai/gpt-6-luna
LYARA_LLM_BASE_URL=https://ai.boxapi.ir/ai/v1
LYARA_LLM_API_KEY=your-boxapi-key
```

```powershell
.\.venv\Scripts\python.exe -m lyara projects
.\.venv\Scripts\python.exe -m lyara serve --port 8792
```

برای اولین انتشار، نام برنامه و شناسهٔ واقعی پلن را مشخص کنید. قیمت و شناسهٔ
پلن حدس زده نمی‌شود. پلتفرم از manifestها و `liara.json` تشخیص داده می‌شود؛ پورت
مبهم از کاربر پرسیده می‌شود. برنامهٔ جدید ممکن است هزینه داشته باشد؛ تغییر فقط
پس از بازبینی طرح و تأیید در رابط یا CLI اجرا می‌شود.

```powershell
.\.venv\Scripts\python.exe -m lyara deploy my-app --source examples/hello-node --plan-id ACTUAL_PLAN_ID --port 3000
.\.venv\Scripts\python.exe -m lyara approve RUN_ID
.\.venv\Scripts\python.exe -m lyara status RUN_ID
.\.venv\Scripts\python.exe -m lyara resume RUN_ID
.\.venv\Scripts\python.exe -m lyara answer RUN_ID --message "پورت 3000 را انتخاب می‌کنم" --port 3000
.\.venv\Scripts\python.exe -m lyara recheck RUN_ID
.\.venv\Scripts\python.exe -m lyara recovery RUN_ID
.\.venv\Scripts\python.exe -m lyara report RUN_ID --format markdown
.\.venv\Scripts\python.exe -m lyara repair my-app --source examples/hello-node --port 3000 --message "فقط مانع انتشار را اصلاح کن"
.\.venv\Scripts\python.exe -m lyara diagnose my-app --message "سایتم خطای ۵۰۰ می‌دهد"
.\.venv\Scripts\python.exe -m lyara monitor my-app --interval 60
```

تأیید CLI به ترمینال تعاملی و واردکردن ابتدای digest طرح نیاز دارد. `--yes`
وجود ندارد. پایش CLI فقط می‌خواند؛ هیچ تغییری را خودکار اجرا نمی‌کند.

## انتخاب مدل

### API key

```dotenv
LYARA_PROVIDER=api
LYARA_LLM_API_KEY=your-model-key
LYARA_LLM_BASE_URL=https://your-provider.example/v1
LYARA_MODEL=the-model-you-selected
```

سرویس باید Chat Completions و `response_format: json_object` را پشتیبانی کند.
پاسخ با Pydantic اعتبارسنجی می‌شود. API بومی Anthropic در این adapter نیست؛
برای Claude از Claude Code یا یک endpoint سازگار استفاده کنید. آدرس HTTP فقط
برای localhost مجاز است. کلید مدل و کلید زیرساخت مستقل‌اند.

### Codex و Claude Code به‌عنوان مدل

```dotenv
LYARA_PROVIDER=codex
# یا
LYARA_PROVIDER=claude
# اختیاری؛ در صورت خالی‌بودن، پیش‌فرض خود CLI استفاده می‌شود
LYARA_MODEL=
```

CLI انتخاب‌شده باید قبلاً نصب و وارد حساب شده باشد. Lyara آن را در پوشهٔ موقت،
بدون ابزارهای اجرایی و بدون کلید لیارا اجرا می‌کند. مسیر استاندارد npm روی ویندوز
پشتیبانی می‌شود؛ برای نصب دیگر `LYARA_CODEX_BIN` یا `LYARA_CLAUDE_BIN` را روی فایل
اجرایی اصلی قرار دهید. Lyara تنظیمات سراسری این برنامه‌ها را تغییر نمی‌دهد.

### اتصال Lyara به Codex یا Claude Code با MCP

این مسیر از مدل خود میزبان استفاده می‌کند و به کلید LLM در Lyara نیاز ندارد.
یک MCP محلی stdio اضافه کنید. نمونه برای همین پوشه:

```powershell
$pythonPath = (Resolve-Path .\.venv\Scripts\python.exe).Path
codex mcp add lyara -- $pythonPath -m lyara mcp
claude mcp add --transport stdio lyara -- $pythonPath -m lyara mcp
```

نصب editable در محیط فعلی انجام شده و اجرای MCP از پوشه‌های دیگر هم ممکن است.
برای نصب دیگر از `pip install -e .` یا cwd/PYTHONPATH مناسب استفاده کنید.
فایل تنظیمات را می‌توان با `LYARA_ENV_FILE` مشخص کرد؛ مسیرهای نسبی فضای کاری
و داده نسبت به محل همان فایل تفسیر می‌شوند. متغیرهای `LIARA_API_TOKEN`،
`LYARA_WORKSPACE` و `LYARA_DATA_DIR` را در محیط سرور MCP تنظیم کنید. وب و MCP باید
همان مسیر مطلق `LYARA_DATA_DIR` را استفاده کنند تا طرح و تأیید مشترک بماند.
برای تست بدون کلید، فرمان MCP را با `-m lyara --demo mcp` اجرا کنید.

ابزارها: `list_projects`، `inspect_source`، `collect_evidence`، `list_domains`،
`list_dns_zones`، `list_dns_records`، `proposal_schema`، `propose` و
`operation_status`، `operation_report` و `recheck_operation`. ابتدا `proposal_schema` را بخوانید. `propose` فقط طرح می‌سازد
و متوقف می‌شود؛ تأیید در رابط یا ترمینال انسان انجام می‌شود.

## معماری و اتصال لیارا

```text
Persian web / CLI / MCP
          │
   shared Assistant service
          │
 inspect → observe → plan → human approval → execute → verify
                      │                        │
           API / Codex / Claude           Liara HTTP API
          │
 SQLite checkpoints + independent action journal
```

APIهای PaaS از `https://api.liara.ir` و DNS از
`https://dns-service.liara.ir` فراخوانی می‌شوند. احراز هویت در header
`Authorization: Bearer ...` است؛ در صورت تنظیم `LIARA_TEAM_ID`، پارامتر
`teamID` فرستاده می‌شود. پاسخ خام خطای سرویس که ممکن است اطلاعات محرمانه داشته
باشد در خطاها نمایش داده نمی‌شود.

نسخهٔ محلی قراردادهای رسمی در `docs/reference/paas.yaml` و `dns.yaml` قرار دارد.
منابع: [API لیارا](https://developers.liara.ir/)،
[وقفه و ادامه در LangGraph](https://docs.langchain.com/oss/python/langgraph/interrupts)،
[MCP در Codex](https://developers.openai.com/codex/mcp/)،
[اجرای برنامه‌ای Claude Code](https://code.claude.com/docs/en/headless).

## امنیت و محدودیت‌های واقعی

- خواندن اطلاعات با تغییر زیرساخت جداست. تأیید به برنامه، آرگومان‌ها، هش سورس
  و fingerprint مقادیر محرمانهٔ ارجاعی متصل است.
- بستهٔ سورس `.env`، کلیدها، پوشه‌های وابستگی، VCS، محیط مجازی و symlinkها را
  کنار می‌گذارد. فهرست فایل‌ها را پیش از انتشار مرور کنید؛ تشخیص همهٔ انواع
  اطلاعات محرمانه در هر فایل دلخواه تضمین نمی‌شود.
- `.liaraignore` الگوهای حذف glob را می‌پذیرد؛ negation پشتیبانی نمی‌شود.
- برای تغییر env از `env_refs` استفاده کنید. مثلاً مقدار `DB_PASSWORD` از متغیر
  سرور `LYARA_DEPLOY_DB_PASSWORD` خوانده می‌شود. مقدار وارد طرح یا مدل نمی‌شود.
- بررسی سلامت به IP عمومی اعتبارسنجی‌شده متصل می‌شود و نام دامنه را برای TLS
  و Host نگه می‌دارد؛ redirect دنبال نمی‌شود. خاموشی عمدی با وضعیت scale بررسی می‌شود.
- پاسخ نامشخص عملیات نوشتنی خودکار تکرار نمی‌شود. ابتدا باید وضعیت واقعی
  لیارا بررسی شود؛ تضمین عمومی exactly-once ادعا نمی‌شود.
- بازگشت خودکار نداریم. دکمهٔ «طرح بازگشت به سورس پیشین» فقط برای انتشارهایی
  ظاهر می‌شود که سورس فعال قبلی از API یا تاریخچهٔ خود برنامه شناخته شده باشد.
  اقدام `redeploy` تأیید جدید می‌خواهد؛ داده، env، پلن و DNS را برنمی‌گرداند.
- پذیرش درخواست ری‌استارت به معنی اثبات اجرای آن نیست. سلامت سایت جدا نمایش
  داده می‌شود؛ اگر API شناسهٔ اجرای قابل تأیید ندهد، نتیجهٔ آن اقدام نامشخص می‌ماند.
  DNS و دامنه با خواندن وضعیت نهایی بررسی می‌شوند؛ انتشار عمومی DNS جداست.
- DNS لیارا قابل مدیریت است؛ تغییر nameserver در ایرنیک یا ثبت‌کنندهٔ دیگر
  ممکن است به اقدام دستی نیاز داشته باشد. صدور TLS و انتشار DNS فوری فرض نمی‌شود.
- دادهٔ منابع با واحد نامشخص به درصد یا مگابایت تبدیل نمی‌شود. نبود داده به
  معنی سلامت نیست و فرضیهٔ مدل علت قطعی محسوب نمی‌شود.
- اجرای محلی برای یک اپراتور است. اجرای عمومی به `LYARA_ACCESS_TOKEN`، مقدار
  `LYARA_PUBLIC_HOST`، HTTPS در reverse proxy و کنترل دسترسی مناسب نیاز دارد؛
  این نسخه یک سامانهٔ چندمشتری نیست.
- SQLite و فایل artifact باید روی دیسک پایدار و محافظت‌شده بمانند. checkpoint
  فقط وضعیت نرم‌افزار را ثبت می‌کند و تراکنش ابری را اتمی نمی‌کند.

## بررسی و دمو

برای سناریوهای محلی از ساده تا چندفرایندی، خطاهای عمدی و تعامل/تأیید کاربر،
[پروژه‌های آزمایشی](tests/projects/README.md) را ببینید. این مسیر به توکن لیارا
نیاز ندارد و برنامه‌ها را واقعاً روی همین کامپیوتر اجرا می‌کند:

```powershell
.\.venv\Scripts\python.exe -m tests.lab
.\.venv\Scripts\python.exe -m tests.lab --serve --provider codex --fixture 09-restart-approval
```

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
node --check lyara/static/app.js
```

تست‌ها شامل جریان کامل دیپلوی، تأیید و رد، ادامه پس از restart، عدم تکرار نوشتن،
حفظ env، عدم افشای secret، احراز هویت/Origin وب و handshake واقعی MCP هستند.
این‌ها از transport محلی استفاده می‌کنند و جای تست زندهٔ لیارا را نمی‌گیرند.

[مقایسهٔ هشت نمونهٔ جهانی](docs/research-global.md) الگوهای اقتباس‌شده و منابع را
ثبت می‌کند. [سناریوی ویدئو](docs/demo-script.md) برای دموی پنج‌دقیقه‌ای است.
فایل ویدئو هنوز ضبط نشده و مطابق چالش، تحویل آن اجباری است.
