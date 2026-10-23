import {createServer} from 'node:http';
// Deliberately no default: the deployment agent must configure the port.
const port = Number(process.env.PORT);
if (!Number.isInteger(port) || port < 1) throw new Error('PORT must be supplied');
createServer((request, response) => {
  response.writeHead(200, {'Content-Type':'application/json'});
  response.end(JSON.stringify({status:'ok'}));
}).listen(port, '127.0.0.1', () => console.log('Ready on configured port ' + port));
