const fs=require('fs'),path=require('path'),http=require('http');
const {chromium}=require('/private/tmp/omc-ad-recompare-20261016-qz1yvyu5/omc-kit/node_modules/playwright');
const cfg=JSON.parse(fs.readFileSync(path.join(__dirname,'runtime.json'),'utf8'));
(async()=>{
 const [op,...args]=process.argv.slice(2);const arm=op==='launch'?args[0]:path.basename(process.cwd());if(!cfg.arms[arm])throw Error('Use own work directory');const c=cfg.arms[arm],port=c.debug_port+100,base='http://localhost:'+c.port;
 if(op!=='launch'){
  const res=await fetch('http://127.0.0.1:'+port+'/command',{method:'POST',body:JSON.stringify({op,args})});const text=await res.text();console.log(text);if(!res.ok)process.exitCode=1;return;
 }
 const context=await chromium.launchPersistentContext(path.join(__dirname,'browser-profiles-ipc',arm),{headless:false,viewport:{width:1440,height:1000}});const page=context.pages()[0]||await context.newPage();
 async function run({op,args=[]}){
  if(op==='snapshot'){const dir=path.join(__dirname,arm,'.omc/browser');fs.mkdirSync(dir,{recursive:true});const file=path.join(dir,Date.now()+'.png');await page.screenshot({path:file,fullPage:true});return {url:page.url(),title:await page.title(),text:(await page.locator('body').innerText()).slice(0,18000),screenshot:file};}
  if(op==='evaluate')return await page.evaluate(args.join(' '));
  if(op==='goto'){const url=new URL(args[0],base);if(url.origin!==base)throw Error('Only own origin');await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:120000});}
  else if(op==='reload')await page.reload({waitUntil:'domcontentloaded',timeout:120000});
  else if(op==='wait')await page.waitForTimeout(Math.min(Number(args[0]),10000));
  else if(op==='click')await page.locator(args[0]).click({timeout:15000});
  else throw Error('snapshot | evaluate <JS> | goto <path> | wait <ms> | reload | click <selector>');
  return {url:page.url()};
 }
 http.createServer(async(req,res)=>{let body='';for await(const chunk of req)body+=chunk;try{const value=await run(JSON.parse(body));res.setHeader('content-type','application/json');res.end(JSON.stringify(value));}catch(e){res.statusCode=500;res.end(JSON.stringify({error:e.message}));}}).listen(port,'127.0.0.1',()=>console.log(JSON.stringify({arm,control_ready:true,port})));
 page.goto(base+'/sixshoptest01',{waitUntil:'domcontentloaded',timeout:120000}).then(()=>console.log(JSON.stringify({arm,url:page.url()}))).catch(e=>console.error(e.message));
})().catch(e=>{console.error(e.message);process.exit(1)});
