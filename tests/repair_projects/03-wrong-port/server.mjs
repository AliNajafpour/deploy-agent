import http from "node:http";
const server=http.createServer((req,res)=>{
  if(req.url==="/__lyara_test_stop"){res.end("bye");server.close();return;}
  res.writeHead(200,{"content-type":"application/json"});res.end(JSON.stringify({healthy:true,fixture:"repair-start"}));
});
server.listen(Number(process.env.WRONG_PORT),"127.0.0.1");
console.log("Startup uses WRONG_PORT; deployment routes PORT");
