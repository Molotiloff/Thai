// Monetary preview uses integer arithmetic; the server remains authoritative.
const scale=1000000n;
function fraction(text:string):[bigint,bigint] {
 const value=text.trim().replace(',','.');
 if(!/^\d+(\.\d{1,18})?$/.test(value))throw new Error('Заполните сумму, курс и доли');
 const [whole,part='']=value.split('.');const denominator=10n**BigInt(part.length);
 return [BigInt(whole)*denominator+BigInt(part||'0'),denominator];
}
function rounded(n:bigint,d:bigint){return (n*2n+d)/(2n*d);}
function allocate(total:bigint,weights:bigint[]){
 const sum=weights.reduce((a,b)=>a+b,0n);if(sum<=0n)throw new Error('Укажите доли');
 const result=weights.map(w=>total*w/sum);
 let left=total-result.reduce((a,b)=>a+b,0n);
 const order=weights.map((w,i)=>({i,remainder:total*w%sum})).sort((a,b)=>a.remainder===b.remainder?a.i-b.i:a.remainder>b.remainder?-1:1);
 for(const {i} of order){if(!left)break;result[i]++;left--;}
 return result;
}
function display(value:bigint){const sign=value<0n?'−':'';value=value<0n?-value:value;return sign+String(value/scale)+'.'+String(value%scale).padStart(6,'0');}
export function expensePreview(values:Record<string,string>):string {
 try {
  const [amount,amountScale]=fraction(values.amount??'');const instrument=values.instrument??'THB_CASH';
  const [rate,rateScale]=instrument==='USDT'?[1n,1n]:fraction(values.quoted_rate??'');
  if(amount<=0n||rate<=0n)throw new Error('Сумма и курс должны быть положительными');
  const quoteScale=10n**18n;
  const quote=instrument.startsWith('THB')?rounded(rateScale*quoteScale,rate):rounded(rate*quoteScale,rateScale);
  const value8=rounded(amount*quote*100000000n,amountScale*quoteScale);
  const total=rounded(value8,100n);
  const nativeScale=instrument==='USDT'?scale:100n;
  if(amount*nativeScale%amountScale!==0n)throw new Error('Недопустимая точность суммы');
  const native=amount*nativeScale/amountScale;
  const percentage=(key:string)=>{const [n,d]=fraction(values['shares.'+key]??'0');return n*scale/d;};
  const d=percentage('daniyar'),pe=percentage('pe'),sky=percentage('skyex'),common=percentage('common');
  if(!values.equal_split&&d+pe+sky+common!==100n*scale)throw new Error('Сумма долей должна составлять 100%');
  // Seeded participant order is SkyEX, PE, Daniyar; ties match server allocation.
  const weights=values.equal_split?[1n,1n,1n]:[sky*2n+common,pe*2n+common,d*2n];
  const nativeShares=allocate(native,weights);
  const [,,personal]=allocate(total,values.equal_split?weights:nativeShares);
  const payer=values.payer??'business';const label={skyex:'SkyEX',pe:'ПЕ',daniyar:'Данияру'}[payer as 'skyex'|'pe'|'daniyar'];
  return `Всего: ${display(total)} USDT · Расход бизнеса: ${display(total-personal)} USDT · Доля Данияра: −${display(personal)} USDT. `+
   (payer==='business'?`Из кассы: −${values.amount} ${instrument}.`:`${label}: +${display(total)} USDT за оплату.`)+
   (payer==='daniyar'?` Итого Данияру: +${display(total-personal)} USDT.`:'');
 }catch(error){return error instanceof Error?error.message:'Проверьте значения расхода';}
}
