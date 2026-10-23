import {createServer} from 'node:http';
const port = Number(process.env.PORT || 3000);
createServer((request, response) => {
  response.writeHead(200, {'Content-Type':'application/json'});
  response.end(JSON.stringify({status:'ok', app:'local-node', path:request.url,
    hasCloudToken:Boolean(process.env.LIARA_API_TOKEN), hasModelKey:Boolean(process.env.LYARA_LLM_API_KEY)}));
}).listen(port, '127.0.0.1', () => console.log('Ready on port ' + port));
