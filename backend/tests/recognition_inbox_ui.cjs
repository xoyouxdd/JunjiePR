'use strict';

const assert = require('node:assert/strict');
const net = require('node:net');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

const backend = path.resolve(__dirname, '..');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function run() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const base = 'http://127.0.0.1:'+port;
  const server = spawn(path.join(backend,'.venv/Scripts/python.exe'), [
    path.join(__dirname,'recognition_photo_fixture.py'),String(port),
  ],{cwd:backend,windowsHide:true});
  let serverLog = '';
  let browser;
  server.stdout.on('data',chunk => serverLog+=chunk);
  server.stderr.on('data',chunk => serverLog+=chunk);

  try {
    let health;
    for (let attempt=0; attempt<120 && server.exitCode===null; attempt++) {
      try {
        const response = await fetch(base+'/health');
        if (response.ok) { health=await response.json(); break; }
      } catch {}
      await sleep(250);
    }
    assert(health,serverLog);
    browser = await chromium.launch({channel:'msedge',headless:true});
    const errors = [];
    const sample = Buffer.from(await fetch(base+'/__fixture/photo/blue').then(response=>response.arrayBuffer()));

    async function session(account,width) {
      const context = await browser.newContext({viewport:{width,height:850}});
      const login = await context.request.post(base+'/api/login',{data:{employee_no:account,password:'1234'}});
      assert(login.ok(),await login.text());
      await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
      const page = await context.newPage();
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/');
      return {context,page};
    }

    async function selectSigner(form,id) {
      const choice = form.locator(`[data-recognizer-option="${id}"]`);
      await form.locator('.recognizer-trigger').click();
      const group = form.locator(`.recognizer-group:has([data-recognizer-option="${id}"])`);
      await group.locator('.recognizer-group-toggle').click();
      await choice.click();
    }

    async function issue(sender,target,types,signers={}) {
      const {context,page} = await session(sender,1280);
      await page.locator('.tabs-desktop [data-tab=register]').click();
      assert.equal(await page.locator('[data-register-mode=proxy]').count(),0);
      await page.locator('#recognitionForm').getByText('认可图片（选填，最多1张）').waitFor();
      if (target==='SUPTEST01') await page.locator('[data-register-target=supervisor]').click();
      const form = page.locator('#recognitionForm');
      await form.locator('[data-employee-search]').fill(target);
      await form.locator('.employee-search-result').filter({hasText:target}).click();
      const options = await context.request.get(base+'/api/options').then(response=>response.json());
      const me = await context.request.get(base+'/api/me').then(response=>response.json());
      const venue = options.recognition_venues.find(row=>row.name==='热力追踪');
      await form.locator('[name=occurred_attraction_id]').selectOption(String(venue.id));
      const records = [];
      for (const [index,code] of types.entries()) {
        const kind = options.recognition_types.find(row=>row.code===code);
        await form.locator('[name=recognition_type_id]').selectOption(String(kind.id));
        await page.waitForFunction(id=>document.querySelector('#recognizerSelect')?.value===String(id),me.id);
        if (signers[code]) {
          const circleId = await form.locator('[name=employee_id]').getAttribute('data-attraction-id');
          const candidates = await context.request.get(base+'/api/recognizers?attraction_id='+circleId).then(response=>response.json());
          const candidate = candidates.find(row=>row.employee_no===signers[code]);
          assert(candidate,signers[code]);
          await selectSigner(form,candidate.id);
        }
        await form.locator('[name=content]').fill('浏览器验证'+code);
        const attachPhoto = code !== 'COURTESY';
        if (!attachPhoto) {
          const album = form.locator('[data-evidence-album-input]');
          await album.setInputFiles({name:'empty.png',mimeType:'image/png',buffer:Buffer.alloc(0)});
          await form.getByText('图片未成功读取，请重新选择图片后提交。',{exact:true}).waitFor();
          assert.equal(await form.locator('[data-evidence-thumbnail]').isVisible(),false);
          await form.locator('[data-evidence-remove]').click();
          assert.equal(await album.evaluate(input=>input.files.length),0);
        }
        if (attachPhoto) {
          const source = index%2 ? 'camera' : 'album';
          await form.locator('[data-evidence-'+source+'-input]').setInputFiles({
            name:source+'.png',mimeType:'image/png',buffer:sample,
          });
          await form.locator('[data-evidence-preview]').waitFor();
        }
        let response;
        try {
          [response] = await Promise.all([
            page.waitForResponse(result=>new URL(result.url()).pathname.endsWith('/api/recognitions') &&
              result.request().method()==='POST',{timeout:10000}),
            form.locator('.form-sticky-actions button').click(),
          ]);
        } catch (error) {
          throw new Error('提交未完成 '+sender+' → '+target+' '+code+' '+await page.locator('#toast').textContent(),{cause:error});
        }
        assert(response.ok(),await response.text());
        const payload = await response.json();
        assert.equal(payload.record.status,'confirmed');
        assert.equal(payload.record.has_image,attachPhoto);
        records.push(payload.record);
        const feedback = page.locator('.performance-feedback-continue');
        await feedback.click();
        await feedback.waitFor({state:'detached'});
        await form.locator('[data-evidence-preview]').waitFor({state:'hidden'});
      }
      await context.close();
      return records;
    }

    const records = await issue('SUPTEST01','CMTEST01',['COURTESY','SAFETY','INCLUSION'],{SAFETY:'GSMTEST01'});
    const readId = records[1].id;
    const member = await session('CMTEST01',320);
    const home = member.page.locator('#recognitionInbox');
    await home.locator('[data-inbox-entry]').filter({hasText:'3 封未读'}).click();
    await home.locator('[data-letter-id]').first().waitFor();
    assert.equal(await home.locator('[data-letter-id]').count(),3);
    await home.locator('[data-letter-id]').filter({hasText:'安全'}).click();
    assert(!await home.locator('.rf-paper').isVisible());
    await home.locator('[data-unseal]').click();
    await home.locator('[data-replay]').waitFor();
    await home.locator('.rf-paper img').waitFor();
    await member.page.waitForFunction(()=>document.querySelector('#recognitionInbox .rf-paper img')?.naturalWidth>0);
    assert.match(await home.locator('.rf-paper').innerText(),/浏览器验证SAFETY/);
    assert.match(await home.locator('.rf-credit').innerText(),/实际增加/);
    await home.locator('[data-inbox-list]').click();
    await home.getByText('2 封未读',{exact:true}).waitFor();
    assert.equal(await home.locator('[data-letter-id][data-unread=false]').count(),1);
    await home.locator('[data-inbox-home]').click();
    await home.locator('[data-inbox-entry]').filter({hasText:'2 封未读'}).waitFor();
    await member.page.locator('.personal-records summary').click();
    const safetyCard = member.page.locator('.personal-records .record-card').filter({hasText:'浏览器验证SAFETY'});
    await safetyCard.getByText(`加分人：${records[1].operator_name}（主动发信）`,{exact:true}).waitFor();
    assert(await member.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await member.context.close();

    const reviewer = await session('TATEST01',1280);
    await reviewer.page.locator('.tabs-desktop [data-tab=review]').click();
    await reviewer.page.locator('#reviewHistoryToggle').click();
    const reviewRow = reviewer.page.locator(`[data-review-row="${readId}"]`);
    await reviewRow.getByText('上级主动认可 · 仅供查看').waitFor();
    assert.equal(await reviewRow.locator('[data-action=restore]').count(),0);
    await reviewer.context.close();

    async function correct(senderAccount,recipientAccount,original,{removeOriginalSigner=false,attachPhoto=true}={}) {
      const sender = await session(senderAccount,1280);
      await sender.page.locator('.tabs-desktop [data-tab=entries]').click();
      await sender.page.setViewportSize({width:390,height:850});
      await sender.page.locator(`[data-entry-withdraw="${original.id}"]:visible`).click();
      await sender.page.locator('[data-modal-input]').fill('原内容填写有误');
      await sender.page.locator('[data-modal-confirm]').click();
      await sender.page.locator('.modal-overlay.is-leaving').waitFor({state:'detached'});
      const reissueButton = sender.page.locator(`[data-entry-reissue="${original.id}"]:visible`);
      await reissueButton.waitFor();
      const draftResponse = await sender.context.request.get(base+`/api/recognitions/${original.id}/reissue`);
      assert(draftResponse.ok(),await draftResponse.text());
      const draft = await draftResponse.json();
      const senderId = await sender.context.request.get(base+'/api/me').then(response=>response.json()).then(me=>me.id);
      assert.notEqual(draft.recognizer_employee_id,senderId);
      if (removeOriginalSigner) {
        await sender.page.route('**/api/recognizers?*',async route=>{
          const response = await route.fetch();
          const candidates = await response.json();
          await route.fulfill({response,json:candidates.filter(row=>row.id!==draft.recognizer_employee_id)});
        });
      }
      await reissueButton.click();
      const correction = sender.page.locator('#recognitionForm');
      await sender.page.locator('[data-reissue-notice]').waitFor();
      assert.equal(await correction.locator('[name=content]').inputValue(),original.content);
      await correction.locator('.employee-search-result').filter({hasText:recipientAccount}).click();
      if (removeOriginalSigner) {
        await correction.getByText('当前签卡人不可用，请重新选择签卡人。',{exact:true}).waitFor();
        assert.equal(await correction.locator('#recognizerSelect').inputValue(),'');
        await correction.locator('.form-sticky-actions button').click();
        await sender.page.locator('#toast').getByText('请选择认可人',{exact:true}).waitFor();
        await selectSigner(correction,senderId);
      }
      const expectedSigner = removeOriginalSigner ? senderId : draft.recognizer_employee_id;
      await sender.page.waitForFunction(id=>document.querySelector('#recognizerSelect')?.value===String(id),expectedSigner);
      await correction.locator('[name=content]').fill('重新发卡浏览器验证');
      if (attachPhoto) {
        await correction.locator('[data-evidence-album-input]').setInputFiles({name:'corrected.png',mimeType:'image/png',buffer:sample});
        await correction.locator('[data-evidence-preview]').waitFor();
      }
      let committedReplacement;
      if (removeOriginalSigner) {
        const existing = await sender.context.request.post(base+'/api/recognitions',{multipart:{
          employee_id:String(original.employee_id),
          recognition_date:draft.recognition_date,
          recognition_type_id:String(draft.recognition_type_id),
          occurred_attraction_id:String(draft.occurred_attraction_id),
          recognizer_employee_id:String(expectedSigner),
          content:'另一项独立表现',
          active_recognition:'true',
          same_day_duplicate_confirmed:'true',
          image:{name:'proof.png',mimeType:'image/png',buffer:sample},
        }});
        assert(existing.ok(),await existing.text());
        await sender.page.route('**/api/recognitions',async route=>{
          const confirmed = route.request().postDataBuffer()?.includes(Buffer.from('name="same_day_duplicate_confirmed"'));
          if (!confirmed || committedReplacement) {
            await route.continue();
            return;
          }
          const response = await route.fetch({maxRetries:0});
          assert(response.ok(),await response.text());
          committedReplacement = (await response.json()).record;
          await route.abort('failed');
        });
        await correction.locator('.form-sticky-actions button').click();
        await sender.page.getByRole('button',{name:'确认是独立表现，继续登记'}).click();
        await sender.page.locator('#toast').getByText(/提交结果暂未确认/).waitFor();
        assert(committedReplacement);
      }
      const [response] = await Promise.all([
        sender.page.waitForResponse(result=>new URL(result.url()).pathname.endsWith('/api/recognitions') && result.request().method()==='POST'),
        correction.locator('.form-sticky-actions button').click(),
      ]);
      assert(response.ok(),await response.text());
      const payload = await response.json();
      const replacement = payload.record;
      assert.equal(replacement.has_image,attachPhoto);
      if (committedReplacement) {
        assert.equal(payload.duplicate,true);
        assert.equal(replacement.id,committedReplacement.id);
      }
      if (!removeOriginalSigner) {
        assert.equal(replacement.recognizer_name,original.recognizer_name);
        assert.equal(replacement.credited_fraction,original.credited_fraction);
      }
      const oldPhoto = await sender.context.request.get(base+original.image_url+'?preview=1');
      assert.equal(oldPhoto.status(),200);
      await sender.context.close();
      return replacement;
    }

    const replacementId = (await correct('SUPTEST01','CMTEST01',records[1],{attachPhoto:false})).id;

    const correctedMember = await session('CMTEST01',320);
    assert.equal((await correctedMember.context.request.get(base+records[1].image_url+'?preview=1')).status(),403);
    const correctedInbox = correctedMember.page.locator('#recognitionInbox');
    await correctedInbox.locator('[data-inbox-entry]').filter({hasText:'4 封未读'}).click();
    await correctedInbox.locator(`[data-letter-id="${readId}"]`).filter({hasText:'已撤回'}).click();
    await correctedInbox.getByText('原认可已撤回',{exact:true}).waitFor();
    await correctedInbox.getByText('原内容填写有误',{exact:true}).waitFor();
    await correctedInbox.locator('[data-inbox-list]').click();
    await correctedInbox.locator(`[data-letter-id="${replacementId}"]`).filter({hasText:'重新发卡'}).click();
    await correctedInbox.locator('[data-unseal]').click();
    await correctedInbox.locator('[data-replay]').waitFor();
    await correctedInbox.getByText('重新发卡浏览器验证',{exact:true}).waitFor();
    assert.equal(await correctedInbox.locator('.rf-paper img').count(),0);

    // The recipient's batch form warns about the active letter before saving.
    await correctedMember.page.setViewportSize({width:1280,height:850});
    await correctedMember.page.locator('.tabs-desktop [data-tab=register]').click();
    const batch = correctedMember.page.locator('#recognitionForm');
    const card = batch.locator('[data-batch-entry]').first();
    const opts = await correctedMember.context.request.get(base+'/api/options').then(r=>r.json());
    const me = await correctedMember.context.request.get(base+'/api/me').then(r=>r.json());
    const signers = await correctedMember.context.request.get(base+'/api/recognizers?attraction_id='+me.attraction_id).then(r=>r.json());
    const signerId = signers.find(row=>row.employee_no==='GSMTEST01').id;
    await card.locator('[name=recognition_type_id]').selectOption(String(opts.recognition_types.find(row=>row.code==='SAFETY').id));
    await card.locator('[name=occurred_attraction_id]').selectOption(String(opts.recognition_venues.find(row=>row.name==='热力追踪').id));
    await card.locator('[name=recognizer_employee_id]').selectOption(String(signerId),{force:true});
    await card.locator('[name=content]').fill('本人登记提醒验证');
    await batch.locator('[data-evidence-album-input]').setInputFiles({name:'self.png',mimeType:'image/png',buffer:sample});
    const duplicateDialog = correctedMember.page.getByRole('dialog',{name:'已有主动发信'});
    await batch.locator('[data-batch-submit]').click();
    await duplicateDialog.getByText(`加分人：${records[1].operator_name}（主动发信）`,{exact:false}).waitFor();
    await duplicateDialog.getByRole('button',{name:'取消'}).click();
    await duplicateDialog.waitFor({state:'detached'});
    assert.equal(await card.locator('[name=content]').inputValue(),'本人登记提醒验证');
    assert.equal(await batch.locator('[data-evidence-album-input]').evaluate(input=>input.files.length),1);
    const afterCancel = await correctedMember.context.request.get(base+'/api/dashboard').then(r=>r.json());
    assert(!afterCancel.records.some(row=>row.content==='本人登记提醒验证'));
    await batch.locator('[data-batch-submit]').click();
    await duplicateDialog.getByRole('button',{name:'确认是独立表现，继续登记'}).waitFor();
    const concurrentSender = await session('GSMTEST01',1280);
    const newLetter = await concurrentSender.context.request.post(base+'/api/recognitions',{multipart:{
      employee_id:String(me.id),
      recognition_date:await card.locator('[name=recognition_date]').inputValue(),
      occurred_attraction_id:await card.locator('[name=occurred_attraction_id]').inputValue(),
      recognition_type_id:await card.locator('[name=recognition_type_id]').inputValue(),
      recognizer_employee_id:String(signerId),
      content:'确认期间新发来的认可',
      active_recognition:'true',
      same_day_duplicate_confirmed:'true',
    }});
    assert(newLetter.ok(),await newLetter.text());
    await concurrentSender.context.close();
    await duplicateDialog.getByRole('button',{name:'确认是独立表现，继续登记'}).click();
    const refreshedDialog = duplicateDialog.filter({hasText:'确认期间新发来的认可'});
    await refreshedDialog.getByText('同日同类记录已更新，请重新核对。',{exact:false}).waitFor();
    const afterStaleConfirmation = await correctedMember.context.request.get(base+'/api/dashboard').then(r=>r.json());
    assert(!afterStaleConfirmation.records.some(row=>row.content==='本人登记提醒验证'));
    const [batchAccepted] = await Promise.all([
      correctedMember.page.waitForResponse(r=>r.url().endsWith('/api/recognitions/batch')&&r.request().method()==='POST'),
      refreshedDialog.getByRole('button',{name:'确认是独立表现，继续登记'}).click(),
    ]);
    assert(batchAccepted.ok(),await batchAccepted.text());
    assert.equal((await batchAccepted.json()).records[0].status,'pending');
    await correctedMember.context.close();

    const [trainerOriginal] = await issue('SUPTEST01','TRTEST01',['EFFICIENCY'],{EFFICIENCY:'GSMTEST01'});
    const trainer = await session('TRTEST01',390);
    const trainerInbox = trainer.page.locator('#recognitionInbox');
    await trainerInbox.locator('[data-inbox-entry]').filter({hasText:'1 封未读'}).click();
    await trainerInbox.locator('[data-unseal]').click();
    await trainerInbox.locator('[data-replay]').waitFor();
    await trainerInbox.getByText('浏览器验证EFFICIENCY',{exact:true}).waitFor();
    await trainer.context.close();
    const trainerReplacement = await correct('SUPTEST01','TRTEST01',trainerOriginal,{removeOriginalSigner:true});
    const correctedTrainer = await session('TRTEST01',390);
    assert.equal((await correctedTrainer.context.request.get(base+trainerOriginal.image_url+'?preview=1')).status(),403);
    const trainerLetters = await correctedTrainer.context.request.get(base+'/api/recognition-inbox').then(response=>response.json());
    assert(trainerLetters.items.some(item=>item.id===trainerOriginal.id && item.cancelled));
    assert(trainerLetters.items.some(item=>item.id===trainerReplacement.id && item.reissued));
    await correctedTrainer.context.close();

    const [leadOriginal] = await issue('GSMTEST01','SUPTEST01',['SHOW'],{SHOW:'AMTEST01'});
    const lead = await session('SUPTEST01',390);
    await lead.page.locator('[data-tab=home]:visible').first().click();
    const leadInbox = lead.page.locator('#recognitionInbox');
    await leadInbox.locator('[data-inbox-entry]').filter({hasText:'1 封未读'}).click();
    await leadInbox.locator('[data-unseal]').click();
    await leadInbox.locator('[data-replay]').waitFor();
    assert.match(await leadInbox.locator('.rf-paper').innerText(),/浏览器验证SHOW/);
    await leadInbox.locator('[data-inbox-list]').click();
    await leadInbox.getByText('0 封未读',{exact:true}).waitFor();
    await lead.context.close();
    const leadReplacement = await correct('GSMTEST01','SUPTEST01',leadOriginal);
    const correctedLead = await session('SUPTEST01',390);
    assert.equal((await correctedLead.context.request.get(base+leadOriginal.image_url)).status(),403);
    await correctedLead.page.locator('[data-tab=home]:visible').first().click();
    const correctedLeadInbox = correctedLead.page.locator('#recognitionInbox');
    await correctedLeadInbox.locator('[data-inbox-entry]').filter({hasText:'2 封未读'}).click();
    await correctedLeadInbox.locator(`[data-letter-id="${leadReplacement.id}"]`).filter({hasText:'重新发卡'}).click();
    await correctedLeadInbox.locator('[data-unseal]').click();
    await correctedLeadInbox.getByText('重新发卡浏览器验证',{exact:true}).waitFor();
    await correctedLead.context.close();
    for (const account of ['AMTEST01','OMTEST01']) {
      const senior = await session(account,1280);
      await senior.page.locator('.tabs-desktop [data-tab=register]').click();
      await senior.page.locator('#pocRecognitionForm').waitFor();
      assert.equal(await senior.page.locator('#recognitionForm').count(),0);
      await senior.context.close();
    }
    assert.deepEqual(errors,[]);
    console.log('Edge UI passed: LEAD → CM/TR, GSM → LEAD, read-only history, correction, reissue after a lost response, 3→2 unread, stale batch confirmation refreshed.');
  } finally {
    await browser?.close();
    server.kill();
  }
}

run().catch(error=>{console.error(error);process.exitCode=1;});
