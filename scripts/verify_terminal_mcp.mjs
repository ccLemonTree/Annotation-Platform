// Real MCP + SSH2 transport, using an owned localhost SSH fixture only.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { generateKeyPairSync } from 'node:crypto';
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { resolve } from 'node:path';

const entry=resolve(process.argv[2]);const require=createRequire(entry);const { Server }=require('ssh2');
const { privateKey }=generateKeyPairSync('rsa',{modulusLength:2048,privateKeyEncoding:{type:'pkcs1',format:'pem'},publicKeyEncoding:{type:'pkcs1',format:'pem'}});
const password='owned-terminal-only 密码 "$`\\';const connections=new Set();let ctrlC=false,resized=false,shells=0;
const server=new Server({hostKeys:[privateKey]},connection=>{
  connections.add(connection);connection.on('close',()=>connections.delete(connection));connection.on('error',()=>{});
  connection.on('authentication',context=>{if(context.method==='password'&&context.username==='terminal_fixture'&&context.password===password)context.accept();else context.reject(['password']);});
  connection.on('ready',()=>connection.on('session',accept=>{
    const session=accept();
    session.on('pty',(acceptPty,rejectPty,info)=>{assert.equal(info.term,'xterm-256color');acceptPty();});
    session.on('window-change',(acceptResize,rejectResize,info)=>{resized=info.cols===120&&info.rows===40;acceptResize?.();});
    session.on('shell',acceptShell=>{
      const channel=acceptShell();shells++;channel.write('\x1b[?1049hfixture fullscreen\x1b[?1049l\r\n中文 ready\r\n$ ');
      channel.on('data',data=>{
        if(data.includes(3)){ctrlC=true;channel.write('^C\r\n$ ');}
        else channel.write(data);
      });
    });
  }));
});
await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
const port=server.address().port;
async function run(pass,expectSuccess){
  const child=spawn(process.execPath,[resolve('backend/backend/scripts/terminal_mcp_bridge.mjs'),entry],{windowsHide:true,stdio:['pipe','pipe','pipe']});
  let diagnostics='',allOutput='',waiting;
  const events=[];child.stderr.on('data',data=>{diagnostics+=data;});child.stdin.on('error',()=>{});
  createInterface({input:child.stdout}).on('line',line=>{allOutput+=line;events.push(JSON.parse(line));waiting?.();});
  const ended=once(child,'close');const timer=setTimeout(()=>child.kill(),45000);
  const next=async()=>{while(!events.length)await new Promise(resolveEvent=>{waiting=resolveEvent;});waiting=undefined;return events.shift();};
  const send=value=>child.stdin.write(JSON.stringify(value)+'\n');
  try{
    send({server:'platform_terminal',cols:100,rows:30,connection:{host:'127.0.0.1',port,username:'terminal_fixture',password:pass}});
    const first=await next();
    if(!expectSuccess){assert.equal(first.event,'error');await ended;return;}
    assert.equal(first.event,'ready');let output='';
    while(!output.includes('中文 ready')){const event=await next();if(event.event==='output')output+=Buffer.from(event.data,'base64').toString();}
    assert.ok(output.includes('\x1b[?1049h'));
    send({type:'resize',cols:120,rows:40});send({type:'input',data:Buffer.from('probe中文\r').toString('base64')});
    while(!output.includes('probe中文')){const event=await next();if(event.event==='output')output+=Buffer.from(event.data,'base64').toString();}
    send({type:'input',data:'Aw=='});
    while(!output.includes('^C')){const event=await next();if(event.event==='output')output+=Buffer.from(event.data,'base64').toString();}
    child.stdin.end();await ended;
    assert.ok(ctrlC&&resized);
  }finally{
    clearTimeout(timer);if(child.exitCode===null)child.kill();
    assert.ok(!allOutput.includes(password)&&!diagnostics.includes(password),'Credential leaked');
  }
}
try{
  await run(password,true);await run('incorrect-password',false);
  assert.equal(shells,1);assert.equal(connections.size,0);
  console.log(JSON.stringify({real_mcp:true,real_ssh_transport:true,pty:true,unicode:true,alternate_screen:true,ctrl_c:true,resize:true,wrong_password_rejected:true,credentials_in_logs:false,ssh_connections_closed:true}));
}finally{for(const connection of connections)connection.end();await new Promise(done=>server.close(done));}
