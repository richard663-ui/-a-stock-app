const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');

(async()=>{
  const root=path.resolve(__dirname,'..');
  const screenshots=path.join(root,'runtime','screenshots');
  fs.mkdirSync(screenshots,{recursive:true});
  const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||undefined});
  try{
    for(const [name,width,height] of [['mobile',390,844],['desktop',1280,900]]){
      const page=await browser.newPage({viewport:{width,height}});
      await page.addInitScript(()=>localStorage.setItem('astock_mobile_session_v2','test-only'));
      await page.route('**/functions/v1/**',async route=>{
        const url=route.request().url();
        let body={};
        if(url.endsWith('/learning'))body={training:{state:'WAITING_FRESH_DATA',pooled_samples:63165,
          learning_progress:{data_latest_date:'2026-09-07',new_samples_since_last_training:0}},
          recorder:{data_mode:'L1_BASELINE',labeled_counts_today:{}},model_results:[]};
        else if(url.endsWith('/evaluation'))body={summaries:[]};
        else if(url.includes('/context'))body={symbol:'600522.SH',summary:'周期分化',timeframes:{}};
        else if(url.endsWith('/state'))body={symbol:'600522.SH',price:null,fresh:false,stale_seconds:8,
          one_minute:{direction:'WATCH',label:'数据延迟'},two_minute:{direction:'WATCH',label:'数据延迟'},
          forecast_context:{score_60:50,score_120:70}};
        else body={fresh:false};
        await route.fulfill({json:body,headers:{'access-control-allow-origin':'*'}});
      });
      await page.goto(pathToFileURL(path.join(root,'docs','index.html')).href);
      await page.locator('#learningState').filter({hasText:'等待新行情样本'}).waitFor();
      await page.locator('#evaluationState').filter({hasText:'暂无前瞻样本'}).waitFor();
      assert.equal(await page.locator('#one').textContent(),'数据延迟');
      assert.equal(await page.locator('#price').textContent(),'--');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:path.join(screenshots,name+'.png'),fullPage:true});
      await page.close();
    }
    console.log('Mobile and desktop rendering, learning state and delayed-WATCH display PASS');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
