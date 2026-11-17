// Optional browser check. Uses an isolated headless Chrome, never a user profile.
const {chromium}=require(process.env.LOCAL_AI_PLAYWRIGHT || 'playwright');
const {spawn}=require('child_process');
const fs=require('fs');
const path=require('path');
(async()=>{
  const fixture=spawn(process.env.LOCAL_AI_PYTHON || 'python3',['-m','tests.browser-fixture'],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','inherit']});
  let browser;
  try{
    const base=await new Promise((resolve,reject)=>{fixture.stdout.once('data',b=>resolve(b.toString().trim()));fixture.once('exit',c=>reject(Error('fixture exited '+c)))});
    browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1120,height:900}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/chat');
    await page.getByRole('option',{name:'qwen3-8b-8k:latest'}).waitFor({state:'attached'});
    await page.getByRole('textbox',{name:'聊天消息'}).fill('连接测试');
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await page.getByRole('status').filter({hasText:'回答完成'}).waitFor();
    await page.getByText('网页连接成功',{exact:true}).waitFor();
    await page.getByText('推理内容',{exact:true}).click();
    await page.getByText('fixture thinking',{exact:true}).waitFor();
    const output=process.env.LOCAL_AI_EVIDENCE;
    if(output){await page.screenshot({path:path.join(output,'web-chat-fixture.png'),fullPage:true});fs.writeFileSync(path.join(output,'web-chat-ui.json'),JSON.stringify({fixture_stream_passed:true,thinking_display:true,page_errors:errors,real_windows_chat:'not tested; remote API currently503'},null,2))}
    if(errors.length)throw Error(errors.join(';'));
    console.log(JSON.stringify({fixture_stream_passed:true,thinking_display:true,page_errors:errors}));
  }finally{if(browser)await browser.close();fixture.stdin.end()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
