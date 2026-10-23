import {createServer} from 'node:http';
// Intentional bug: PORT is ignored. The harness supplies another unused local port.
const port = Number(process.env.WRONG_PORT || 3001);
console.error('PortMismatch: PORT is ignored; listening on WRONG_PORT=' + port);
createServer((request, response) => response.end('wrong port')).listen(port, '127.0.0.1');
