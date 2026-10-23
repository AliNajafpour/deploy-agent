import {createServer} from 'node:http';

const port = Number(process.env.PORT || 3000);
const page = '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>انتشار با لیارا</title><body style="font-family:Tahoma,sans-serif;max-width:650px;margin:15vh auto;padding:30px;color:#172943;background:#eef4fb"><p style="color:#195dcc">LYARA / DEPLOYMENT VERIFIED</p><h1>پروژهٔ شما منتشر شد.</h1><p>این صفحه از برنامهٔ مستقر پاسخ می‌دهد.</p><a href="/health">بررسی سلامت</a></body></html>';
createServer((request, response) => {
  if (request.url === '/health') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({status: 'ok', app: 'lyara-demo'}));
  } else {
    response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
    response.end(page);
  }
}).listen(port, '0.0.0.0', () => console.log('Listening on ' + port));
