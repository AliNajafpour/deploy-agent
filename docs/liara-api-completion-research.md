# تکمیل قراردادهای API و بررسی پروژهٔ مرجع

بررسی در ۲۰۲۶-۱۰-۲۳ انجام شد؛ فقط مستندات و سورس عمومی خوانده شدند. هیچ کلید، حساب زنده، ساخت شبکه، خرید، استقرار یا اجرای کد پروژهٔ مرجع استفاده نشد. این یادداشت قراردادهای مشاهده‌شده را ثبت می‌کند؛ موفقیت عملی API در حساب مشخص را تضمین نمی‌کند.

## مبنای بررسی

- [CLI رسمی لیارا، commit ثابت `14ec35f79d703a09a5e3abbb22ba59239cdde80d`](https://github.com/liara-cloud/cli/tree/14ec35f79d703a09a5e3abbb22ba59239cdde80d).
- [فهرست رسمی API](https://developers.liara.ir/llms.txt)، [قرارداد حساب](https://developers.liara.ir/spec/me.yaml)، [قرارداد مالی](https://developers.liara.ir/spec/billing.yaml) و [قرارداد PaaS](https://developers.liara.ir/spec/paas.yaml).
- [پروژهٔ مرجع، commit ثابت `828efd6d7b86c5509719a2faa659fc397053c501`](https://github.com/ssepehrnoush/liara-copilot/tree/828efd6d7b86c5509719a2faa659fc397053c501). این پروژه منبع ایده است؛ README، شبیه‌ساز و ادعاهای آن مرجع قرارداد لیارا یا اثبات امنیت نیستند.

SHA-256 فایل‌های رسمی دریافت‌شده: `me.yaml`: `01d8f1a20c1c31c51f6f683ebee54a4747e3039918f9d156e1f7579a030d44d8`؛ `billing.yaml`: `28b9404d1698a0e12e8078daf862cb97eaf7f343a37a27c3243588750d85c0f9`؛ `paas.yaml`: `a8b8ab24c0b2decf8a67a93a9fc935f93846a991f999d0c4ebb7478a6fc4d56d`. قرارداد PaaS با snapshot موجود در `docs/reference/paas.yaml` یکسان است.

## حساب، کاتالوگ و هزینه

مبنای درخواست‌ها `https://api.liara.ir` و احراز هویت Bearer است. برای تیم، `teamID` باید ارسال شود؛ CLI رسمی آن را به query اضافه می‌کند. [راهنمای احراز هویت و تیم](https://docs.liara.ir/references/api/about/)، [پیکربندی HTTP در CLI](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/base.ts)

`GET /v1/me` شامل `plans.projects` و `plans.projectBundlePlans` است. هر دو object هستند؛ شناسهٔ پلن و بسته کلید object است، نه فیلد فرضی `id`. بسته‌ها زیر پلن سخت‌افزاری قرار دارند:

```text
plans.projects[planID] = {
  available, region, RAM: {amount}, CPU: {amount}, volume, price, storageClass?, ...
}
plans.projectBundlePlans[planID][bundlePlanID] = {
  available, displayPrice, ...
}
```

CLI برای سخت‌افزار، RAM را GB، CPU را Core و volume را GB نمایش می‌دهد. قیمت ماهانهٔ سخت‌افزار را `price * 720` محاسبه می‌کند؛ بنابراین ۷۲۰ ساعت یک برآورد ماهانه است و نباید هزینهٔ قطعی صورتحساب نامیده شود. `displayPrice` بسته را تومان/ماه نمایش می‌دهد. هزینهٔ ترکیب باید سخت‌افزار و بستهٔ همان پلن را در بر بگیرد؛ نبود هر جزء عددی، هزینهٔ کل را نامعلوم می‌کند. صفر تنها وقتی معتبر است که API صریحاً صفر داده باشد. CLI فقط گزینه‌های `available` را پیشنهاد می‌کند؛ شناسه، ظرفیت و موجودی نباید از نام تجاری ساخته شوند. [ساخت برنامه در CLI](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/commands/app/create.ts)، [جدول پلن‌ها در CLI](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/commands/plan/list.ts)

فیلتر CLI برای `free`/`g2` و منطقهٔ ایران، انتخاب رابط همان نسخه است؛ محدودیت عمومی تمام API محسوب نمی‌شود. قرارداد رسمی `Plans` ویژگی‌های پلن را باز گذاشته است. ترتیب object نیز مجوز فرض‌کردن دقیقاً سه بسته یا نگاشت موقعیتی برنزی/نقره‌ای/طلایی نیست. در Lyara فقط مقادیر دریافت‌شده و اطلاعات دستیِ مشخصاً برچسب‌خورده قابل اتکا هستند. [قرارداد حساب](https://developers.liara.ir/spec/me.yaml)

موجودی حساب شخصی از `GET /v1/billing`، مسیر `user.balance` می‌آید؛ واحد مالی طبق مستندات تومان است. در حالت تیمی، `GET /v1/me?teamID=...` بخش `team` با `balance` دارد. قرارداد Billing دربارهٔ کاربر فعلی است؛ نبود `team.balance` مجوز استفاده از موجودی شخصی برای تیم نیست. `minCreditAmount` حد هشدار است، نه موجودی. [قرارداد Billing](https://developers.liara.ir/spec/billing.yaml)، [واحد مالی](https://docs.liara.ir/references/api/billing/)، [تعریف Team در قرارداد حساب](https://developers.liara.ir/spec/me.yaml)

پاسخ `/v1/me` می‌تواند اطلاعات هویتی و `storage.accessKey`/`storage.secretKey` داشته باشد. پاسخ خام نباید وارد context مدل، لاگ یا گزارش عمومی شود؛ فقط کاتالوگ و فیلدهای ضروری با allowlist استخراج شوند. [هشدار رسمی دربارهٔ اطلاعات حساس حساب](https://docs.liara.ir/references/api/get-info/)

## شبکه و ساخت برنامه

| عملیات | قرارداد مشاهده‌شده | منبع رسمی |
| --- | --- | --- |
| `GET /v1/networks` | `{networks: [{_id: string, name: string, createdAt: string}]}` | [فرمان فهرست](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/commands/network/list.ts)، [نوع Network](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/types/network.ts) |
| `POST /v1/networks` | بدنهٔ `{networkName}`؛ CLI پاسخ موفق را برای استخراج شناسه parse نمی‌کند | [فرمان ساخت شبکه](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/commands/network/create.ts) |
| `POST /v1/projects` | بدنهٔ `{name, planID, platform, bundlePlanID, network, readOnlyRootFilesystem?}`؛ `network` شناسهٔ `_id` است | [فرمان ساخت برنامه](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/commands/app/create.ts) |

OpenAPI عمومی PaaS فیلد `bundlePlanID` را در `Create-app` ذکر نمی‌کند، ولی سورس رسمی CLI آن را صریحاً ارسال و از کاتالوگ انتخاب می‌کند. این اختلاف مستندات دلیل حذف فیلد از آداپتر نیست. برای پلن `free`، CLI فقط بستهٔ `free` را قبول می‌کند. [قرارداد عمومی](https://developers.liara.ir/spec/paas.yaml)، [اعتبارسنجی رسمی CLI](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/commands/app/create.ts)

CLI در نبود شبکه، ساخت برنامه را متوقف می‌کند و ساخت شبکه را مرحله‌ای جدا می‌داند. پس از ایجاد شبکه، شناسه را با فهرست مجدد و تطبیق دقیق نام بگیرید؛ پاسخ فرضی `networkID` نسازید. نتیجهٔ نامشخص نوشتن مجوز تکرار خودکار POST نیست. شبکه پس از ساخت برنامه قابل تغییر نیست و برای ارتباط خصوصی با دیتابیس باید هر دو در شبکهٔ مشترک باشند. قیمت مستقلی برای ساخت شبکه در منابع بررسی‌شده یافت نشد؛ نامعلوم‌بودن قیمت به معنای رایگان‌بودن نیست. ساخت شبکه باید در طرح قابل تأیید باشد. [انتخاب شبکه در CLI](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/base.ts)، [رفتار شبکهٔ خصوصی](https://docs.liara.ir/paas/details/private-network/)

## آپلود، صف، ساخت و راه‌اندازی

قراردادهای زیر از سورس رسمی CLI تأیید شدند:

| مرحله | درخواست و پاسخ مورد استفاده |
| --- | --- |
| آپلود | `POST /v2/projects/{name}/sources`، multipart با فیلد `file`؛ پاسخ `sourceID` |
| ثبت انتشار | `POST /v2/projects/{name}/releases` با `sourceID` و پیکربندی اجرا؛ پاسخ `releaseID` |
| صف | `GET /v1/releases/{releaseID}` → `{release: {queue?, state, status, failReason?}}`؛ CLI وجود تعداد غیرصفر `queue` را انتظار ساخت نمایش می‌دهد |
| لاگ ساخت | `GET /v2/releases/{releaseID}/build-logs` با `since` اختیاری → `{release, buildOutput: [{_id, line, stream, releaseID, createdAt}]}` |

منابع: [آپلود رسمی](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/services/upload.ts)، [فرمان deploy](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/commands/deploy.ts)، [دریافت لاگ ساخت](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/services/build-logs.ts)، [نوع release](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/types/release.ts)، [نوع خروجی ساخت](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/types/build-output.ts)

CLI آخرین `createdAt` لاگ را برای `since` بعدی نگه می‌دارد. `DEPLOYING` را راه‌اندازی و بررسی کانتینر نمایش می‌دهد، `READY` پایان انتظار است و `FAILED`، `UNHEALTHY`، `CANCELED`، `TIMEDOUT` شکست یا توقف را مشخص می‌کنند. متن خطوط برای برچسب محلی BUILDING/PUSHING استفاده می‌شود؛ این برچسب‌ها لزوماً state دریافتی API نیستند. تعداد صف یا درصد آپلود نباید در نبود داده ساخته شود. [منطق رسمی build logs](https://github.com/liara-cloud/cli/blob/14ec35f79d703a09a5e3abbb22ba59239cdde80d/src/services/build-logs.ts)

`GET /v1/projects/{name}/releases` شامل `currentRelease` و `releases[]` است. آماده‌شدن یک release با پاسخ موفق صفحهٔ اصلی اثبات نمی‌کند که همان نسخه همهٔ درخواست‌ها را پاسخ می‌دهد. بررسی پایداری باید شناسهٔ دقیق release، فعال‌بودن نسخه و چند دور درخواست محدود به مسیرهای امن را جدا ثبت کند؛ نبود active confirmation باید در نتیجه باقی بماند. این الزام طراحی Lyara است، نه تضمین اضافهٔ API. [قرارداد releases](https://developers.liara.ir/pass/apps/get-releases-of-app)

## واحدها و حدود شواهد منابع

قراردادهای `GET /v1/projects/{name}/metrics/cpu` و `.../memory` پارامتر عددی `since` و پاسخ `result[].values` همراه `applet` را تعریف می‌کنند. مقدارها آرایه‌ای از عدد یا رشته‌اند؛ واحد CPU و RAM، فاصلهٔ نمونه‌برداری و تضمین تازگی در این schema تصریح نشده‌اند. از مقیاس ظاهری عدد، CPU درصدی یا RAM بایتی نتیجه نگیرید. حد منابع پلن با واحد رسمی ظرفیت قابل نمایش است، ولی تبدیل سری خام به درصد ظرفیت نیازمند قرارداد واحد مستقل است. [CPU](https://developers.liara.ir/pass/reports/get-cpu-reports-of-app)، [حافظه](https://developers.liara.ir/pass/reports/get-memory-reports-of-app)

روند عددی، تعداد نمونه، مدت بازه، کمینه/بیشینه، شیب و تازگی را می‌توان روی دادهٔ معتبر محاسبه کرد؛ واحد نامعلوم باید همان نامعلوم بماند. رشد حافظه، OOM، خطای وابستگی و هم‌زمانی با انتشار شواهد جدا هستند؛ رشد به‌تنهایی نشت یا رابطهٔ علّی قطعی را اثبات نمی‌کند. فیلد گمشده و سری خالی صفر یا سالم نیستند.

## ایده‌های قابل استفاده از مرجع و اصلاحات لازم

| مسیر مرجع در commit ثابت | ایدهٔ مفید | چیزی که نباید منتقل شود |
| --- | --- | --- |
| [src/probe.js](https://github.com/ssepehrnoush/liara-copilot/blob/828efd6d7b86c5509719a2faa659fc397053c501/src/probe.js) | تعداد محدود مسیر و نمونه، شمارش status و میانهٔ latency، الزام هم‌مبدأبودن مسیر | تمام 4xxها را موفق حساب می‌کند؛ هم‌مبدأبودن به‌تنهایی کنترل SSRF یا مجوز GET دارای اثر جانبی نیست |
| [src/diagnose.js](https://github.com/ssepehrnoush/liara-copilot/blob/828efd6d7b86c5509719a2faa659fc397053c501/src/diagnose.js) | گروه‌بندی لاگ، محاسبهٔ روند، مقایسهٔ قبل/بعد و دورهای بررسی | درصدهای اطمینان ساخته‌شده، عنوان قطعی نشت از شیب، فرض واحد CPU/RAM، اثبات نسخهٔ جدید از چند پاسخ HTTP |
| [src/watcher.js](https://github.com/ssepehrnoush/liara-copilot/blob/828efd6d7b86c5509719a2faa659fc397053c501/src/watcher.js) | خط مبنا، چند مشاهده، جلوگیری از هشدار پشت سر هم و ثبت بازیابی | وضعیت در Map حافظه می‌ماند؛ فقدان مقدار گاهی به صفر تبدیل می‌شود؛ یک مشاهدهٔ فشار منابع کافی نیست |
| [src/scope.js](https://github.com/ssepehrnoush/liara-copilot/blob/828efd6d7b86c5509719a2faa659fc397053c501/src/scope.js) | ثبت سایت‌های تحت مدیریت و پیش‌فرض خواندنی برای بقیه | registry یا تشخیص متن خطرناک، جای تأیید دقیق و کنترل متمرکز تمام مسیرهای نوشتن را نمی‌گیرد |
| [src/llm.js](https://github.com/ssepehrnoush/liara-copilot/blob/828efd6d7b86c5509719a2faa659fc397053c501/src/llm.js) | صف فراخوانی، Retry-After، usage مدل و تفکیک قیمت گزارش‌شده/محاسبه‌شده | محدودیت ادعایی حساب خاص را عمومی نکنید؛ هزینهٔ نامعلوم را در جمع صفر نکنید؛ fallback بدون تنظیم صریح اپراتور مجاز نیست |
| [src/codefix.js](https://github.com/ssepehrnoush/liara-copilot/blob/828efd6d7b86c5509719a2faa659fc397053c501/src/codefix.js) | تغییر محدود با diff و بررسی نحو؛ ارجاع تغییر بزرگ به ایجنت کدنویس | فیلتر regex کد را امن اثبات نمی‌کند؛ Lyara باید بستهٔ ثابت و حفظ سورس اصلی خودش را نگه دارد |
| [src/liara/client.js](https://github.com/ssepehrnoush/liara-copilot/blob/828efd6d7b86c5509719a2faa659fc397053c501/src/liara/client.js) | کنار هم آوردن کاتالوگ، شبکه، لاگ ساخت و موجودی | retry عمومی شامل نوشتن، موجودی پیش‌فرض صفر و فرض‌های واحد/زمان/کانتینر قرارداد رسمی نیستند |

هیچ کد مرجع در Lyara کپی نشد. ویژگی‌ها باید با کنترل دسترسی، دفتر اقدامات، رمزنگاری ورودی‌ها، تأیید digest و بستهٔ ثابت موجود یکپارچه شوند. تنظیم دستیِ کاتالوگ فقط مسیر جایگزین اپراتور با برچسب منبع و محدودیت معلوم است؛ قطع API مجوز ساخت قیمت و شناسه نیست.
