import {createServer} from 'node:http';
const greeting = process.env.APP_GREETING;
if (!greeting) console.error('ConfigurationError: required APP_GREETING is missing. Set it and restart.');
createServer((request, response) => {
  response.writeHead(greeting ? 200 : 503, {'Content-Type':'application/json'});
  response.end(JSON.stringify(greeting ? {status:'ok',greeting} : {error:'APP_GREETING missing'}));
}).listen(Number(process.env.PORT || 3000), '127.0.0.1');
