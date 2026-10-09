// Optional visual regression preview: node tests/preview_frontend.cjs (requires local Chrome).
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const previewDir = '/private/tmp/gymcast-polish-preview';
fs.mkdirSync(previewDir, {recursive:true});
const fixture = `
const previewNow = Date.now();
// Forecast targets originate from hour buckets, never the wall-clock minute.
const previewOrigin = Math.floor(previewNow / 3600000) * 3600000;
const names = ["Marino Center — Second Floor Cardio and Strength Training", "Marino Center — Weight Room", "SquashBusters Courts", "Other recreation location"];
const previewData = {generated_at:new Date(previewNow).toISOString(), locations:{}, forecast_origin:{}};
for (const name of names) {
 previewData.forecast_origin[name] = new Date(previewOrigin).toISOString();
 previewData.locations[name] = Array.from({length:24},(_,i)=>({time:new Date(previewOrigin+(i+1)*3600000).toISOString(),predicted_count:Math.round(25+20*Math.cos(i)),predicted_percent:Math.round(50+40*Math.cos(i)),facility_status:i>17?"closed":i===15?"partial":"open",scheduled_status:i>17?"closed":i===15?"partial":"open",scheduled_open_minutes:i>17?0:i===15?30:60,status_source:"published_schedule"}));
}
window.fetch = async (url)=>({ok:true,json:async()=>url.includes("evaluation-history")?{rows:[]}:previewData});
`;
for (const name of ['index.html','styles.css','app.js']) {
 const source = fs.readFileSync(path.join(__dirname, '../web', name), 'utf8');
 fs.writeFileSync(path.join(previewDir,name), (name === 'app.js' ? fixture : '') + source);
}

const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless', '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  '--user-data-dir=/tmp/gymcast-polish-cdp', '--remote-debugging-pipe'
], {stdio:['ignore','ignore','ignore','pipe','pipe']});
let id=0, buffer=''; const waiting=new Map();
chrome.stdio[4].on('data', chunk => {
 buffer+=chunk;
 let end;
 while((end=buffer.indexOf('\0'))!==-1) {
  const message=JSON.parse(buffer.slice(0,end)); buffer=buffer.slice(end+1);
  if(waiting.has(message.id)) {const {resolve,reject,timer}=waiting.get(message.id); clearTimeout(timer);waiting.delete(message.id);message.error?reject(message.error):resolve(message.result);}
 }
});
function send(method,params={},sessionId) {
 return new Promise((resolve,reject)=>{const request=++id;const timer=setTimeout(()=>reject(new Error(method+' timeout')),15000); waiting.set(request,{resolve,reject,timer});chrome.stdio[3].write(JSON.stringify({id:request,method,params,sessionId})+'\0');});
}
(async()=>{
 const {targetId}=await send('Target.createTarget',{url:'about:blank'});
 const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
 for(const width of [1440,375,390,430]) {
  await send('Emulation.setDeviceMetricsOverride',{width,height:1500,deviceScaleFactor:1,mobile:width<500},sessionId);
  await send('Page.navigate',{url:'file:///private/tmp/gymcast-polish-preview/index.html'},sessionId);
  await new Promise(r=>setTimeout(r,600));
  await send('Runtime.evaluate',{expression:'scrollTo(0,0)'},sessionId);
  const dimensions=await send('Runtime.evaluate',{expression:'JSON.stringify({width:innerWidth,scroll:document.documentElement.scrollWidth,main:document.querySelector("main").getBoundingClientRect().width,status:document.getElementById("status").textContent})',returnByValue:true},sessionId);
  console.log(dimensions.result.value);
  await send('Runtime.evaluate',{expression:'document.getElementById("location-trigger").click()'},sessionId);
  console.log((await send('Runtime.evaluate',{expression:'JSON.stringify({menu:document.getElementById("location-menu").getBoundingClientRect().toJSON(),options:document.querySelectorAll("[role=option]").length,expanded:document.getElementById("location-trigger").getAttribute("aria-expanded"),scroll:document.documentElement.scrollWidth})',returnByValue:true},sessionId)).result.value);
  await new Promise(r=>setTimeout(r,150));
  const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId);
  fs.writeFileSync(`/tmp/gymcast-polish-menu-${width}.png`,Buffer.from(shot.data,'base64'));
  await send('Runtime.evaluate',{expression:'document.getElementById("location-trigger").click()'},sessionId);
  await new Promise(r=>setTimeout(r,150));
  const closedShot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId);
  fs.writeFileSync(`/tmp/gymcast-polish-overview-${width}.png`,Buffer.from(closedShot.data,'base64'));
  const check=await send('Runtime.evaluate',{expression:`JSON.stringify((()=>{
    const chart=document.getElementById('forecast-chart');
    const before=chart.scrollLeft; chart.scrollLeft=100; const moved=chart.scrollLeft>before;
    return {width:innerWidth,scroll:document.documentElement.scrollWidth,chartWidth:chart.clientWidth,
      svgWidth:chart.querySelector('svg').getBoundingClientRect().width,moved,
      time:document.getElementById('next-time').textContent,
      aligned:Object.values(previewData.locations).flat().every(p=>new Date(p.time).getUTCMinutes()===0)};
  })())`,returnByValue:true},sessionId);
  const result=JSON.parse(check.result.value);
  assert.equal(result.scroll,width); assert.equal(result.aligned,true);
  if(width<500) assert.equal(result.moved,true);
  console.log('PASS chart scroll and aligned fixture', result);
  for (const state of ['empty','expired']) {
    const expression = state === 'empty'
      ? "renderLocation({locations:{Preview:[]}},'Preview')"
      : "renderLocation(previewData,names[0],previewOrigin+25*3600000)";
    await send('Runtime.evaluate',{expression},sessionId);
    const stateCheck=await send('Runtime.evaluate',{expression:"JSON.stringify({scroll:document.documentElement.scrollWidth,percent:document.getElementById('next-percent').textContent,barHidden:document.getElementById('next-capacity-track').hidden,time:document.getElementById('next-time').textContent})",returnByValue:true},sessionId);
    const stateResult=JSON.parse(stateCheck.result.value);
    assert.equal(stateResult.scroll,width); assert.equal(stateResult.percent,'—');
    assert.equal(stateResult.barHidden,true);
    if(state==='expired') assert.match(stateResult.time,/out of date/);
    const stateShot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId);
    fs.writeFileSync(`/tmp/gymcast-polish-${state}-${width}.png`,Buffer.from(stateShot.data,'base64'));
  }


 }
 await send('Browser.close');
})().catch(error=>{console.error(error);chrome.kill();process.exitCode=1;});
