const fs=require('fs'),path=require('path');
const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const result={modules_loaded:false,esbuild_processed:false,browser_started:false,browser_closed:false};

const ownershipFD=Number(process.env.OMC_PREFLIGHT_OWNERSHIP_FD);
const ownershipACK=Number(process.env.OMC_PREFLIGHT_OWNERSHIP_ACK_FD);
if(!Number.isInteger(ownershipFD)||!Number.isInteger(ownershipACK)||!process.env.OMC_PREFLIGHT_OWNERSHIP_TOKEN)throw Error('ownership_channel_required');
const cp=require('child_process'),spawn=cp.spawn;
cp.spawn=function(command,args,options){
 // Keep the launcher alive while the supervisor verifies the child's parent.
 const deferTermination=()=>{};
 process.on('SIGTERM',deferTermination);process.on('SIGINT',deferTermination);
 try {
 const child=spawn.call(this,command,args,options);
 if(child.pid&&options&&options.detached&&process.platform!=='win32'){
  const cache=fs.realpathSync(manifest.environment.PLAYWRIGHT_BROWSERS_PATH)+path.sep;
  if(fs.realpathSync(command).startsWith(cache)){
   const event={kind:'chromium_spawn',token:process.env.OMC_PREFLIGHT_OWNERSHIP_TOKEN,owner_pid:process.pid,pid:child.pid,pgid:child.pid};
   try{
    fs.writeSync(ownershipFD,JSON.stringify(event)+'\n');
    const ack=Buffer.alloc(1);
    if(fs.readSync(ownershipACK,ack,0,1,null)!==1||ack[0]!==49)throw Error('ownership_ack_missing');
   }
   catch(error){try{process.kill(-child.pid,'SIGKILL');}catch{}throw error;}
  }
 }
 return child;
 } finally {process.removeListener('SIGTERM',deferTermination);process.removeListener('SIGINT',deferTermination);}
};

(async()=>{
 let browser;
 try {
  const pw=require(manifest.modules.playwright),esbuild=require(manifest.modules.esbuild);
  const versions={playwright:require(path.join(manifest.modules.playwright,'package.json')).version,
    esbuild:require(path.join(manifest.modules.esbuild,'package.json')).version};
  for(const [name,version] of Object.entries(versions))if(version!==manifest.versions[name])throw Error('module_version_mismatch: '+name);
  result.modules_loaded=true;result.versions=versions;
  const built=esbuild.transformSync('const answer: number = 42;', {loader:'ts',target:'es2019'});
  if(!built.code.includes('42')||built.code.includes(': number'))throw Error('esbuild_processing_failed');
  result.esbuild_processed=true;
  const root=fs.realpathSync(manifest.root)+path.sep;
  for(const file of Object.keys(require.cache))if(!fs.realpathSync(file).startsWith(root))throw Error('module_outside_retained_root');
  browser=await pw.chromium.launch({headless:true});
  result.browser_started=true;const page=await browser.newPage();
  if(await page.evaluate(()=>6*7)!==42)throw Error('browser_connection_failed');
 } finally {
  if(browser){await browser.close();result.browser_closed=true;}
 }
 console.log(JSON.stringify(result));
})().catch(error=>{result.error=String(error);console.log(JSON.stringify(result));process.exitCode=1;});
