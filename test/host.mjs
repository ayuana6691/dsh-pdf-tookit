/** Real DSH host smoke, isolated profile and a loopback scripted model. */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'
import { root, pythonPath } from '../runner.js'

const temp = await mkdtemp(join(tmpdir(), 'dsh-pdf-host-'))
const home = join(temp, 'home'); await mkdir(home)
const out = join(temp, 'host-created.pdf')
const installPackage = process.env.DSH_PDF_TEST_PACKAGE
if (installPackage) {
  const installed = spawnSync(process.env.DSH_BIN || 'dsh', ['plugin', '--profile', 'headless', 'add', `file:${installPackage}`], {
    cwd: temp, env: { PATH: process.env.PATH, DSH_HOME: home, DSH_TELEMETRY_MODE: 'OFF' }, encoding: 'utf8', timeout: 45000,
  })
  assert.equal(installed.status, 0, installed.stderr || installed.error?.message)
}
const guard = join(temp, 'guard.mjs')
await writeFile(guard, `import net from 'node:net';
const original=net.Socket.prototype.connect;
net.Socket.prototype.connect=function(...args){const a=Array.isArray(args[0])?args[0]:args;const f=a[0];const h=f&&typeof f==='object'?f.host:typeof f==='number'&&typeof a[1]==='string'?a[1]:undefined;if(h&&h!=='localhost'&&h!=='::1'&&!/^127\\./.test(h))throw Error('Host smoke blocks non-loopback network: '+h);return original.apply(this,args)};
export const name='pdf-host-loopback-guard';export function apply(){};
`)
const requests=[]; const errors=[]
const operations=[
  ['pdf_doctor',{}],
  ['skill',{name:'pdf-toolkit'}],
  ['pdf_process',{action:'create_text',options:{output:out,text:'Harness PDF integration'}}],
  ['pdf_inspect',{input:out}],
]
const server=createServer(async(req,res)=>{
  try{
    assert.equal(req.url,'/v1/chat/completions')
    let raw='';for await(const chunk of req)raw+=chunk
    const data=JSON.parse(raw);requests.push(data)
    assert.ok(requests.length<=operations.length+1)
    const i=requests.length-1
    const wire=(delta,reason=null)=>({id:`test-${i}`,object:'chat.completion.chunk',created:1,model:'pdf-local',choices:[{index:0,delta,finish_reason:reason}]})
    res.writeHead(200,{'content-type':'text/event-stream'})
    res.write(`data: ${JSON.stringify(wire({role:'assistant'}))}\n\n`)
    if(i<operations.length){
      const [name,args]=operations[i]
      res.write(`data: ${JSON.stringify(wire({tool_calls:[{index:0,id:`call-${i}`,type:'function',function:{name,arguments:JSON.stringify(args)}}]}))}\n\n`)
      res.write(`data: ${JSON.stringify(wire({},'tool_calls'))}\n\n`)
    }else{
      res.write(`data: ${JSON.stringify(wire({content:'PDF_HOST_OK'}))}\n\n`)
      res.write(`data: ${JSON.stringify(wire({},'stop'))}\n\n`)
    }
    res.end('data: [DONE]\n\n')
  }catch(error){errors.push(String(error));res.statusCode=500;res.end(String(error))}
})
await new Promise(r=>server.listen(0,'127.0.0.1',r))
try {
  const patch=[
    {insert:[{id:'pdf-host-guard',name:guard}]},
    {id:'tools',config:{mode:'native'}},
    {id:'agent-default-model',config:{provider:'pdf-local',model:'pdf-local'}},
    {id:'llm-pi-ai',config:{providers:{'pdf-local':{apiKeyEnv:'PDF_TEST_API_KEY',api:'openai-completions',
      baseURL:`http://127.0.0.1:${server.address().port}/v1`,models:[{id:'pdf-local',name:'PDF local model',contextWindow:1000000,maxTokens:4096,input:['text'],reasoningEfforts:false}],retryPolicy:{mode:'normal',maxRetries:0}}}}},
    ...['session-title-llm','session-telemetry-otel','llm-deepseek','llm-deepseek-account','web-search-deepseek','web-deepseek-api','agent-instructions','skill-filesystem'].map(id=>({id,disabled:true})),
    {id:'session-persistence-jsonl',config:{root:join(home,'sessions'),compression:'none'}},
    ...installPackage ? [] : [{insert:[{id:'dsh-pdf-toolkit',name:process.env.DSH_PDF_TEST_ENTRY || join(root,'index.js'),inject:['tools','skills'],config:{python:pythonPath(),timeoutMs:30000}}]}],
  ]
  const patchPath=join(temp,'patch.json');await writeFile(patchPath,JSON.stringify(patch))
  const executable=process.env.DSH_BIN || 'dsh'
  const child=spawn(executable,['--profile','headless','--patch',patchPath,'--json','Run the PDF tools and load the pdf-toolkit skill.'],{
    cwd:temp,env:{PATH:process.env.PATH,DSH_HOME:home,DSH_TELEMETRY_MODE:'OFF',DSH_TOOLS_MODE:'native',PDF_TEST_API_KEY:'local-test-key',DSH_PDF_PYTHON:pythonPath()},stdio:['ignore','pipe','pipe'],
  })
  let stdout='',stderr=''
  child.stdout.on('data',x=>stdout+=x);child.stderr.on('data',x=>stderr+=x)
  const timer=setTimeout(()=>child.kill('SIGKILL'),45000)
  const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',resolve)}).finally(()=>clearTimeout(timer))
  await writeFile(join(temp,'stdout.jsonl'),stdout);await writeFile(join(temp,'stderr.txt'),stderr)
  await writeFile(join(temp,'requests.json'),JSON.stringify(requests,null,2))
  assert.equal(code,0,stderr);assert.deepEqual(errors,[])
  assert.equal(requests.length,operations.length+1,`Tool dispatch did not complete. Logs: ${temp}\n${stderr}`)
  const firstNames=requests[0].tools?.map(t=>t.function.name)||[]
  for(const n of ['pdf_doctor','pdf_inspect','pdf_extract','pdf_render','pdf_compare','pdf_process','skill'])assert.ok(firstNames.includes(n),`Missing tool ${n}`)
  const returned=requests.at(-1).messages.filter(m=>m.role==='tool')
  assert.ok(returned.some(m=>JSON.stringify(m).includes('"ready": true')||JSON.stringify(m).includes('\\"ready\\": true')),'doctor result missing')
  assert.ok(returned.some(m=>JSON.stringify(m).includes('PDF Toolkit')),'skill not loaded')
  assert.ok(returned.some(m=>JSON.stringify(m).includes('page_details')),'inspect result missing')
  assert.equal((await readFile(out)).subarray(0,5).toString(),'%PDF-')
  console.log(JSON.stringify({ok:true,hostLogDirectory:temp,registeredTools:6,skillLoaded:true,toolCalls:operations.length,externalModelCalls:0},null,2))
} finally {server.closeAllConnections();await new Promise(r=>server.close(r))}
