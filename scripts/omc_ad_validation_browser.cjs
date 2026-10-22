// Local inspection only: no model, login automation, or arbitrary evaluate/click.
'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto'),http=require('http');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const pin=p=>sha(fs.readFileSync(p));
function noLinks(file){for(let p=file;;p=path.dirname(p)){
 if(fs.existsSync(p)&&fs.lstatSync(p).isSymbolicLink())throw Error('path_link_refused');
 if(path.dirname(p)===p)break;
}}
function load(file){
 noLinks(file);
 const cfg=JSON.parse(fs.readFileSync(file,'utf8')),run=fs.realpathSync(cfg.run_root);
 const receipt=JSON.parse(fs.readFileSync(path.join(run,'restore-receipt.json'),'utf8'));
 if(file!==path.join(run,'runtime.json')||pin(file)!==receipt.runtime_sha256||
    pin(__filename)!==cfg.browser_sha256||path.dirname(cfg.candidate)!==run)throw Error('runtime_binding_mismatch');
 if(cfg.schema_version!=='omc-ad-runtime/v1'||pin(path.join(cfg.playwright_module,'package.json'))!==cfg.playwright_package_sha256)throw Error('dependency_binding_mismatch');
 for(const [name,p] of Object.entries(receipt.restored_files)){noLinks(path.join(run,name));if(pin(path.join(run,name))!==p)throw Error('restored_file_mismatch');}
 for(const q of Object.values(cfg.queries)){noLinks(q.path);if(pin(q.path)!==q.sha256)throw Error('query_digest_mismatch');}
 return cfg;
}
(async()=>{
 const [op,flag,file]=process.argv.slice(2);
 if(op!=='launch'||flag!=='--config'||!path.isAbsolute(file))throw Error('launch --config <absolute runtime.json> required');
 const cfg=load(file),base='http://localhost:'+cfg.port,run=cfg.run_root;
 const {chromium}=require(cfg.playwright_module);
 noLinks(path.join(run,'browser-profile'));
 const context=await chromium.launchPersistentContext(path.join(run,'browser-profile'),{
  headless:cfg.headless,viewport:{width:1440,height:1000}});
 const page=context.pages()[0]||await context.newPage(),bundles=new Map();
 await page.route('**/*',route=>{
  const request=route.request();
  if(request.isNavigationRequest()&&request.frame()===page.mainFrame()&&new URL(request.url()).origin!==base)return route.abort();
  return route.continue();
 });
 page.on('response',r=>{if(r.url().startsWith(base+'/')&&new URL(r.url()).pathname.endsWith('.js'))
  r.body().then(b=>bundles.set(r.url(),sha(b))).catch(()=>{});});
 const own=()=>{if(new URL(page.url()).origin!==base)throw Error('page_origin_mismatch');};
 async function command({op,args=[]}){
  load(file);
  if(!Array.isArray(args)||!args.every(x=>typeof x==='string'))throw Error('invalid_arguments');
  if(op==='status'&&args.length===0)return {ready:true,url:page.url(),candidate:cfg.candidate};
  if(op==='goto'&&args.length===1){
   const url=new URL(args[0],base);
   if(url.origin!==base||url.username||url.password)throw Error('only_own_origin');
   bundles.clear();await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:15000});own();return {url:page.url()};
  }
  own();
  if(op==='reload'&&args.length===0){bundles.clear();await page.reload({waitUntil:'domcontentloaded',timeout:15000});own();return {url:page.url()};}
  if(op==='wait'&&args.length===1){const n=Number(args[0]);if(!Number.isFinite(n)||n<0||n>10000)throw Error('invalid_wait');await page.waitForTimeout(n);return {url:page.url()};}
  if(op==='evaluate'&&args.length===1){
   // Existing 637a965 preflight sends the exact query text; accept its pinned hash too.
   const q=cfg.queries[args[0]]||Object.values(cfg.queries).find(q=>q.sha256===sha(args[0]));
   if(!q)throw Error('query_not_allowlisted');
   const queryBytes=fs.readFileSync(q.path);
   if(sha(queryBytes)!==q.sha256)throw Error('query_not_allowlisted');
   return await page.evaluate(queryBytes.toString('utf8'));
  }
  if(op==='snapshot'&&args.length===0){
   const dir=path.join(cfg.candidate,'.omc/browser');noLinks(dir);fs.mkdirSync(dir,{recursive:true});
   const screenshot=path.join(dir,crypto.randomUUID()+'.png');await page.screenshot({path:screenshot,fullPage:true});
   return {url:page.url(),title:await page.title(),text:(await page.locator('body').innerText()).slice(0,18000),
    candidate:cfg.candidate,screenshot,screenshot_sha256:pin(screenshot),bundle_hashes:Object.fromEntries(bundles)};
  }
  throw Error('unsupported_command');
 }
 let busy=false;
 const server=http.createServer(async(req,res)=>{
  if(req.method!=='POST'||req.url!=='/command'){res.writeHead(404);res.end();return;}
  if(busy){res.writeHead(409);res.end(JSON.stringify({error:'command_in_progress'}));return;}
  busy=true;
  try{
   let body='';for await(const c of req){body+=c;if(body.length>65536)throw Error('request_too_large');}
   const value=await command(JSON.parse(body));res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));
  }catch(e){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error_type:e.name,error:e.message}));}
  finally{busy=false;}
 });
 async function stop(){server.close();await context.close();process.exit(0);}
 process.on('SIGTERM',stop);process.on('SIGINT',stop);
 server.on('error',async e=>{console.error(JSON.stringify({error:e.message}));await context.close();process.exit(1);});
 server.listen(cfg.control_port,'127.0.0.1',()=>console.log(JSON.stringify({control_ready:true,port:cfg.control_port,candidate:cfg.candidate})));
})().catch(e=>{console.error(JSON.stringify({error_type:e.name,error:e.message}));process.exit(1);});
