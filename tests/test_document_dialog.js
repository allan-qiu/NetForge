// Exercise the production dialog lifecycle with controlled pending responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../dist/v2.js'), 'utf8');
const start = source.indexOf('  let documentRequest=0');
const end = source.indexOf('  let rankingRequest=0', start);
assert(start >= 0 && end > start);
function fixture() {
  let focused=null;
  const classes=new Set();
  const pending=[],renders=[];
  const nodes=Object.fromEntries(['learningDocumentTitle','learningDocumentBody','learningDocumentSource','downloadLearningDocument','closeLearningDocument'].map(id=>[id,{textContent:'',disabled:false,append(){},focus(){focused=id;}}]));
  const content={scrollTop:999};
  nodes.learningDocument={open:false,showModal(){this.open=true;},close(){this.open=false;},querySelector(){return content;}};
  const context={AbortController,console,$:id=>nodes[id],document:{documentElement:{classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)}},createElement(){return {}; }},window:{NFDocument:{render(node,text){renders.push(text);node.textContent=text;}}},api:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}))};
  vm.createContext(context);
  vm.runInContext(source.slice(start,end)+'\nglobalThis.dialogApi={openLearningDocument,closeLearningDocument};',context);
  return {api:context.dialogApi,nodes,pending,renders,classes,content,get focused(){return focused;},button(){return {disabled:false,isConnected:true,focus(){focused='opener';}};}};
}
(async()=>{
  const f=fixture(),button=f.button();
  const opening=f.api.openLearningDocument('AI','/api/v2/ai/explain',{},button);
  assert(f.nodes.learningDocument.open);
  assert.equal(f.content.scrollTop,0);
  assert.equal(f.focused,'closeLearningDocument');
  assert(button.disabled);
  f.api.closeLearningDocument();
  assert(!f.nodes.learningDocument.open);
  assert(f.pending[0].options.signal.aborted);
  assert(!button.disabled);
  assert(!f.classes.has('document-open'));
  assert.equal(f.focused,'opener');
  f.pending[0].resolve({text:'late result'});
  await opening;
  assert.deepEqual(f.renders,[]);

  for(let index=0;index<3;index++){
    const promise=f.api.openLearningDocument('参考'+index,'/api/v2/reference-document',{index},button);
    f.pending[index+1].resolve({text:'reference '+index,source:'https://www.rfc-editor.org/'});
    await promise;
    assert.equal(f.nodes.learningDocumentBody.textContent,'reference '+index);
    assert(!f.nodes.downloadLearningDocument.disabled);
    f.api.closeLearningDocument();
    assert(!f.nodes.learningDocument.open);
  }
  const old=f.api.openLearningDocument('old','/api/v2/ai/explain',{},button);
  f.api.closeLearningDocument();
  const next=f.api.openLearningDocument('new','/api/v2/reference-document',{},button);
  f.pending[4].resolve({text:'stale'});await old;
  assert.notEqual(f.nodes.learningDocumentBody.textContent,'stale');
  f.pending[5].resolve({text:'current'});await next;
  assert.equal(f.nodes.learningDocumentBody.textContent,'current');
  f.api.closeLearningDocument();
  console.log('Dialog lifecycle: pending cancel, focus restoration, 3 reference documents, stale response isolation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
