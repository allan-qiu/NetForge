// Run the production request lifecycle against controlled out-of-order responses.
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dist/v2.js'),'utf8');
const start=source.indexOf('  let personalAnalysisRequest=0'),end=source.indexOf('  function installLearningUpgrade()',start);
assert(start>=0&&end>start);
const nodes=Object.fromEntries(['generatePersonalAnalysis','personalAnalysisStatus','personalAnalysisBody'].map(id=>[id,{textContent:'',disabled:false}]));
const pending=[],state={user:{id:1}};
const context={AbortController,state,$:id=>nodes[id],formatTime:value=>String(value),window:{NFDocument:{render(node,text){node.textContent=text;}}},api:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}))};
vm.createContext(context);vm.runInContext(source.slice(start,end)+'\nglobalThis.load=loadPersonalAnalysis;',context);
(async()=>{
  const first=context.load();assert(nodes.generatePersonalAnalysis.disabled);
  const second=context.load(true);assert(pending[0].options.signal.aborted);
  assert.equal(pending[1].options.method,'POST');assert.equal(pending[1].options.body.confirm,true);
  pending[1].resolve({ready:true,text:'current learner',generatedAt:123,stale:true});await second;
  assert.equal(nodes.personalAnalysisBody.textContent,'current learner');assert(!nodes.generatePersonalAnalysis.disabled);
  assert(nodes.personalAnalysisStatus.textContent.includes('建议更新'));
  pending[0].resolve({ready:true,text:'stale response'});await first;
  assert.equal(nodes.personalAnalysisBody.textContent,'current learner');
  const other=context.load();state.user={id:2};pending[2].resolve({ready:true,text:'private learner 1'});await other;
  assert.notEqual(nodes.personalAnalysisBody.textContent,'private learner 1');
  const failure=context.load(true);pending[3].reject(new Error('provider unavailable'));await failure;
  assert(nodes.personalAnalysisStatus.textContent.includes('可点击按钮重试'));assert(!nodes.generatePersonalAnalysis.disabled);
  const empty=context.load();pending[4].resolve({ready:false,text:''});await empty;
  assert.equal(nodes.personalAnalysisBody.textContent,'');assert(nodes.personalAnalysisStatus.textContent.includes('尚未生成'));
  console.log('Personal analysis: consent payload, stale response isolation, account switch, retry and empty state passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
