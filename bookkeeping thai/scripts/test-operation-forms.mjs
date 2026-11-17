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
function calculate(values,changed='exchange.rate',anchor='receive') {
  return calculateExchangeForm({'receive.instrument':'USDT','pay.instrument':'USD_WH',...values},changed,anchor).updates;
}
let calculated=calculate({'receive.quantity':'10000','exchange.rate':'0,96'});
assert.equal(calculated['pay.quantity'],'9600.00');
assert.equal(calculated['exchange.inverse_rate'],'1.041666666666666667');
assert.equal(calculate({'pay.quantity':'9600','exchange.rate':'0.96'})['receive.quantity'],'10000.000000');
assert.equal(calculate({'receive.instrument':'THB_CASH','pay.instrument':'USDT','receive.quantity':'3500','exchange.inverse_rate':'35'},'exchange.inverse_rate')['pay.quantity'],'100.000000');
assert.equal(calculate({'receive.instrument':'USD_WH','pay.instrument':'EUR','receive.quantity':'100','exchange.rate':'0.9'})['pay.quantity'],'90.00');
assert.equal(calculate({'receive.quantity':'200','exchange.rate':'0.9'},'receive.quantity')['pay.quantity'],'180.00');
assert.equal(calculate({'receive.quantity':'100','pay.quantity':'96'},'pay.quantity')['exchange.rate'],'0.96');
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
