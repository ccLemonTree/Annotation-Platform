import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { registerTerminalTools } from '../backend/backend/scripts/terminal_mcp_extension.mjs';

const entry=process.argv[2];
const require=createRequire(resolve(entry));
const { z }=await import(pathToFileURL(require.resolve('zod')).href);
class Shell extends EventEmitter {
  constructor(){super();this.stderr=new EventEmitter();this.inputs=[];this.sizes=[];this.paused=false;}
  write(data){this.inputs.push(data);return true;}
  setWindow(...size){this.sizes.push(size);}
  pause(){this.paused=true;}
  resume(){this.paused=false;}
  end(){this.emit('close');}
  destroy(){this.emit('close');}
}
const handlers={};let mode='readonly',opens=0;
const shell=new Shell();
registerTerminalTools({registerTool:(name,config,callback)=>{handlers[name]=callback;}},{z,
  getServerConfig:async()=>({mode,platform:'linux'}),touch:()=>{},
  getConnection:async()=>({requestShell:async options=>{opens++;assert.equal(options.term,'xterm-256color');assert.equal(options.modes.ECHO,1);return shell;}})});
const call=async(name,args={})=>{
  const result=await handlers[name](args);const body=JSON.parse(result.content[0].text);
  if(result.isError)throw Object.assign(new Error(body.error),{code:body.code});return body;
};
await assert.rejects(()=>call('ssh_terminal_open',{server:'test',cols:100,rows:30}),/限制|只读/);
assert.equal(opens,0);mode='unrestricted';
await call('ssh_terminal_open',{server:'test',cols:100,rows:30});
const raw=Buffer.from('中文\x1b[?1049hTOP\x1b[?1049l');shell.emit('data',raw);
assert.deepEqual(Buffer.from((await call('ssh_terminal_read')).data,'base64'),raw);
await call('ssh_terminal_write',{data:Buffer.from('\x03').toString('base64')});
assert.equal(shell.inputs[0][0],3);
await call('ssh_terminal_resize',{cols:120,rows:40});assert.deepEqual(shell.sizes[0],[40,120,0,0]);
shell.emit('data',Buffer.alloc(600000,65));assert.equal(shell.paused,true);
let total=0;while(total<600000)total+=Buffer.from((await call('ssh_terminal_read')).data,'base64').length;
assert.equal(total,600000);assert.equal(shell.paused,false);
await call('ssh_terminal_close');assert.equal((await call('ssh_terminal_read')).closed,true);
console.log(JSON.stringify({policy:true,raw_pty:true,unicode:true,ctrl_c:true,resize:true,backpressure:true,cleanup:true}));
