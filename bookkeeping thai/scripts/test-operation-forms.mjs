import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const project=Module.createRequire(path.join(root,'package.json'));
const ts=project('typescript');
const {NextRequest}=project('next/server');
const cache=new Map();
let authenticated=true;
function load(file){
  if(cache.has(file))return cache.get(file).exports;
  const compiled=new Module(file);cache.set(file,compiled);compiled.filename=file;
  compiled.require=name=>{
    if(name==='@/thai/server')return {baseUrl:()=> 'http://backend/api/v1',COOKIE:'thai_access_token'};
    if(name==='next/headers')return {cookies:async()=>({get:()=>authenticated?{value:'mock-auth'}:undefined})};
    if(name==='@/thai/origin')return load(path.join(root,'src/thai/origin.ts'));
    if(name.startsWith('.'))return load(path.resolve(path.dirname(file),name+'.ts'));
    return project(name);
  };
  compiled._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}
  }).outputText,file);
  return compiled.exports;
}
process.env.THAI_FRONTEND_ORIGIN='https://crm.example.org';
const config=load(path.join(root,'next.config.ts')).default;
assert(config.allowedDevOrigins.includes('crm.example.org'));
assert(config.allowedDevOrigins.includes('127.0.0.1'));
assert(!config.allowedDevOrigins.includes('*.run.pinggy-free.link'));
const route=load(path.join(root,'src/app/api/thai/[...path]/route.ts'));
const calls=[];
globalThis.fetch=async(url,init)=>{
  calls.push({url,init});
  return new Response(JSON.stringify({id:123}),{status:200});
};
function request(pathname,{method='POST',origin='https://crm.example.org'}={}){
  return new NextRequest('https://crm.example.org/api/thai'+pathname,{
    method,headers:{host:'crm.example.org',origin,'content-type':'application/json'},
    body:JSON.stringify({idempotency_key:'test-only',quantity:'1000.25'})
  });
}
async function forward(pathname,options){
  return route[options?.method??'POST'](request(pathname,options),{params:Promise.resolve({path:pathname.slice(1).split('/')})});
}
for(const pathname of ['/deals','/deals/profit','/deals/1/profit-correction','/deals/1/profit-cancel','/deals/1/ready','/deals/1/post','/cash-requests',
  '/cash-requests/1/settle','/capital/agreements','/capital/agreements/1/contributions',
  '/capital/agreements/1/withdrawals','/expenses','/expenses/participants/skyex/binding','/profit/payments']){
  assert.equal((await forward(pathname)).status,200,pathname);
  assert.equal(calls.at(-1).url,'http://backend/api/v1'+pathname);
  assert.equal(calls.at(-1).init.headers.Authorization,'Bearer mock-auth');
  assert.equal(JSON.parse(calls.at(-1).init.body).quantity,'1000.25');
}
assert.equal((await forward('/cash-requests/1',{method:'PUT'})).status,200);
assert.equal(calls.at(-1).init.method,'PUT');
let count=calls.length;
assert.equal((await forward('/capital/agreements',{origin:'https://attacker.example'})).status,403);
assert.equal((await forward('/unknown')).status,404);
assert.equal(calls.length,count);
authenticated=false;
assert.equal((await forward('/cash-requests')).status,401);
assert.equal(calls.length,count);
console.log('PASS: deal lifecycle, cash requests/create/edit/settle, capital contributions/withdrawals, expenses, profit, forwarded body/auth, CSRF, unknown routes, expired session. No real API writes.');


const {calculateExchangeForm}=load(path.join(root,'src/thai/deals/model.ts'));
function calculate(values,changed='exchange.rate',anchor='receive',phase='input') {
  return calculateExchangeForm({'receive.instrument':'USDT','pay.instrument':'USD_WH',...values},changed,anchor,phase).updates;
}
let calculated=calculate({'receive.quantity':'10000','exchange.rate':'0,96'});
assert.equal(calculated['pay.quantity'],'9600.00');
assert.equal(calculated['exchange.inverse_rate'],'1.041666666666666667');
assert.equal(calculate({'pay.quantity':'9600','exchange.rate':'0.96'})['receive.quantity'],'10000.000000');
assert.equal(calculate({'receive.instrument':'THB_CASH','pay.instrument':'USDT','receive.quantity':'3500','exchange.inverse_rate':'35'},'exchange.inverse_rate')['pay.quantity'],'100.000000');
assert.equal(calculate({'receive.instrument':'USD_WH','pay.instrument':'EUR','receive.quantity':'100','exchange.rate':'0.9'})['pay.quantity'],'90.00');
assert.equal(calculate({'receive.quantity':'200','exchange.rate':'0.9'},'receive.quantity')['pay.quantity'],'180.00');
assert.equal(calculate({'receive.quantity':'100','pay.quantity':'96'},'pay.quantity','receive','blur')['exchange.rate'],'0.96');
assert.equal(calculate({'receive.quantity':'9007199254740993.01','exchange.rate':'1'})['pay.quantity'],'9007199254740993.01');
assert.equal(calculate({'receive.quantity':'1','exchange.rate':'0.005'})['pay.quantity'],'0.01');
assert.equal(calculate({'receive.quantity':'100','exchange.rate':'0'})['pay.quantity'],'');
assert.equal(calculate({'receive.quantity':'100','pay.quantity':'96','exchange.rate':'0.96'},'pay.instrument')['pay.quantity'],'');
console.log('PASS: direct/inverse pair rates, either amount as input, THB/USDT/fiat precision, comma, cross exchange, HALF_UP, large exact amounts, invalid rates and currency changes.');

const {expensePreview}=load(path.join(root,'src/thai/expense-preview.ts'));
const expense={amount:'33000',instrument:'THB_CASH',quoted_rate:'33',payer:'skyex',equal_split:'on'};
assert.match(expensePreview(expense),/Всего: 1000\.000000 USDT/);
assert.match(expensePreview(expense),/Расход бизнеса: 666\.666667 USDT/);
assert.match(expensePreview(expense),/SkyEX: \+1000\.000000 USDT/);
assert.match(expensePreview({...expense,payer:'business'}),/Из кассы: −33000 THB_CASH/);
assert.match(expensePreview({...expense,payer:'daniyar'}),/Итого Данияру: \+666\.666667 USDT/);
assert.match(expensePreview({...expense,instrument:'USDT',amount:'1000',quoted_rate:''}),/Всего: 1000\.000000 USDT/);
assert.match(expensePreview({...expense,equal_split:'','shares.daniyar':'20','shares.common':'80'}),/Расход бизнеса: 800\.000000 USDT/);
assert.match(expensePreview({...expense,quoted_rate:'0'}),/положительными/);
assert.match(expensePreview({...expense,equal_split:'','shares.common':'99'}),/100%/);
console.log('PASS: expense USDT valuation, exact thirds, cash/personal payers, Daniyar net balance, percentages and invalid rates.');

const {dealSpec}=load(path.join(root,'src/thai/deals/specs.ts'));
const {entryAmount,entryDirection}=load(path.join(root,'src/thai/deals/model.ts'));
const profitSpec=dealSpec('profit',[['1','Роджер']],'2026-11-17');
assert.equal(profitSpec.path,'/deals/profit');
assert.deepEqual(profitSpec.fields.filter(f=>f.required!==false).map(f=>f.name),['amount_usdt']);
assert.equal(profitSpec.fields.find(f=>f.name==='business_date').value,'2026-11-17');
assert.equal(profitSpec.fields.find(f=>f.name==='client_id').value,'');
assert(!profitSpec.fields.some(f=>f.name.includes('rate')||f.name==='instrument'||f.name==='mode'));
const loss={kind:'profit',attributes:{amount_usdt:'-12.50',thb_amount:'1000'}};
assert.match(entryAmount(loss),/-12/);
assert.match(entryAmount(loss),/USDT/);
assert.match(entryAmount(loss),/THB/);
assert.match(entryDirection(loss),/USDT/);
console.log('PASS: profit entry fields, optional client/THB/comment, accounting date and signed USDT display.');

const {calculateCounterparty}=load(path.join(root,'src/thai/deals/model.ts'));
const ktReceiveThb={'receive.instrument':'THB_CASH','receive.quantity':'33100','pay.instrument':'USDT','pay.quantity':'1000',counterparty_client_id:'42',counterparty_spread:'0,15'};
assert.deepEqual(calculateCounterparty(ktReceiveThb),{amount:'4.531722',rate:'33.25'});
assert.deepEqual(calculateCounterparty({...ktReceiveThb,'receive.instrument':'USDT','receive.quantity':'1000','pay.instrument':'THB_BANK','pay.quantity':'33100'}),{amount:'4.531722',rate:'32.95'});
assert.equal(calculateCounterparty({...ktReceiveThb,counterparty_client_id:''}),null);
assert.equal(calculateCounterparty({...ktReceiveThb,'receive.instrument':'USDT','receive.quantity':'1000','pay.instrument':'THB_CASH','pay.quantity':'33100',counterparty_spread:'34'}),null);
const ktFields=dealSpec('sale',[],undefined,[['42','КТ чат']]).fields;
assert.deepEqual(ktFields.find(f=>f.name==='counterparty_client_id').options,[['42','КТ чат']]);
assert.equal(ktFields.find(f=>f.name==='counterparty_spread').required,false);
assert.equal(dealSpec('conversion',[]).fields.some(f=>f.name==='counterparty_client_id'),false);
console.log('PASS: counterparty sale/purchase spread, wallet precision, optional fields and invalid rate.');

// Reproduce input/blur events across successive keystrokes, retaining rate provenance.
for(const pair of [
  {'receive.instrument':'USDT','pay.instrument':'THB_CASH','receive.quantity':'1000','pay.quantity':'33100'},
  {'receive.instrument':'THB_BANK','pay.instrument':'USDT','receive.quantity':'33100','pay.quantity':'1000'},
])for(const order of [['receive.quantity','pay.quantity'],['pay.quantity','receive.quantity']]){
  const state={values:{'receive.instrument':pair['receive.instrument'],'pay.instrument':pair['pay.instrument']},anchor:'receive',rateSource:'amounts'};
  function edit(name,value,phase='input'){
    state.values[name]=value;
    const result=calculateExchangeForm(state.values,name,state.anchor,phase,state.rateSource);
    state.anchor=result.anchor;state.rateSource=result.rateSource;Object.assign(state.values,result.updates);
    return result.updates;
  }
  const [first,second]=order;
  edit(first,pair[first]);edit(first,pair[first],'blur');
  for(let n=1;n<=pair[second].length;n++){
    edit(second,pair[second].slice(0,n));
    assert.equal(state.values[first],pair[first],'typing the second amount must preserve the first');
    assert.equal(state.values[second],pair[second].slice(0,n));
    assert.equal(state.values['exchange.rate'],'','infer the rate only after leaving the amount');
  }
  const committed=edit(second,pair[second],'blur');
  assert.ok(state.values['exchange.rate']);assert.ok(state.values['exchange.inverse_rate']);
  assert.equal(committed[first],undefined);assert.equal(committed[second],undefined);
  const savedFirst=state.values[first];
  edit(second,'2');edit(second,'2000');
  assert.equal(state.values[first],savedFirst,'an inferred rate must not overwrite either amount on later edits');
  edit(second,'2000','blur');assert.equal(state.values[first],savedFirst);
  edit('exchange.rate','2');assert.equal(state.rateSource,'manual');
  edit('receive.quantity','10');assert.equal(state.values['pay.quantity'],pair['pay.instrument']==='USDT'?'20.000000':'20.00');
  edit('receive.quantity','');assert.equal(state.values['receive.quantity'],'');assert.equal(state.values['pay.quantity'],'');
}
assert.equal(calculate({'receive.quantity':'100','pay.quantity':'96'},'pay.quantity')['exchange.rate'],'');
const amountsOnly=calculate({'receive.quantity':'100.00','pay.quantity':'96.00'},'pay.quantity','receive','blur');
assert.equal(amountsOnly['exchange.rate'],'0.96');assert.equal(amountsOnly['exchange.inverse_rate'],'1.041666666666666667');
assert.equal(amountsOnly['receive.quantity'],undefined);assert.equal(amountsOnly['pay.quantity'],undefined);
console.log('PASS: manual amounts wait for blur, preserve both amounts and support either input order, repeated edits and explicit rates.');

for(const [kind,receive,pay] of [['sale','USDT','THB_CASH'],['purchase','THB_CASH','USDT']]){
  const spec=dealSpec(kind,[]);
  const incoming=spec.fields.find(f=>f.name==='receive.instrument');
  const outgoing=spec.fields.find(f=>f.name==='pay.instrument');
  assert.equal(incoming.value,receive);assert.equal(outgoing.value,pay);
  assert.ok(incoming.options.some(([code])=>code===receive));
  assert.ok(outgoing.options.some(([code])=>code===pay));
  assert.equal(incoming.options.some(([code])=>code===pay),false);
  assert.equal(outgoing.options.some(([code])=>code===receive),false);
  assert.match(spec.note,kind==='sale'?/Продаём баты/:/Покупаем баты/);
}
const {bestChangeForms}=load(path.join(root,'src/thai/form-specs.ts'));
assert.deepEqual(bestChangeForms[0].fields.find(f=>f.name==='operation').options,[['purchase','Продажа батов'],['sale','Покупка батов']]);
console.log('PASS: sale receives USDT/pays THB, purchase receives THB/pays USDT; BestChange labels retain financial codes.');
