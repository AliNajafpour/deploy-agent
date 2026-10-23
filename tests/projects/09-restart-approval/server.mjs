import {createServer} from 'node:http';
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
const boots = existsSync('boot-count.txt') ? Number(readFileSync('boot-count.txt','utf8'))+1 : 1;
writeFileSync('boot-count.txt',String(boots));
const healthy = boots>1;
console.log(healthy ? 'Transient initialization fault recovered after restart' : 'InitializationError: transient startup state; a controlled restart may recover it');
createServer((request,response)=>{
  response.writeHead(healthy?200:503,{'Content-Type':'application/json'});
  response.end(JSON.stringify({status:healthy?'ok':'initialization failed',boots}));
}).listen(Number(process.env.PORT || 3000),'127.0.0.1');
