import {test,expect} from '@playwright/test';
import {capture,createBatch,open,simulate} from '../helpers/app';

test('nove estados visuais da fixture',async({page},info)=>{
  await open(page);await capture(page,'01-create-empty',info.project.name);
  await page.locator('#prompt-text').fill('Plano aberto\n---\nClose de produto');await capture(page,'02-create-prompts',info.project.name);
  await page.locator('#batch-name').fill('Lote visual determinístico');await capture(page,'03-create-named',info.project.name);
  await page.getByRole('button',{name:/Salvar fila/}).click();await expect(page.getByRole('heading',{name:'Pronto para gerar.'})).toBeVisible();await capture(page,'04-review-draft',info.project.name);
  await page.locator('[name=duration_seconds]').selectOption('3');await page.locator('[name=output_dir]').fill(process.env.FLOWLOCAL_TEST_OUTPUT!);await simulate(page);await capture(page,'05-review-ready',info.project.name);
  await capture(page,'06-consent-modal',info.project.name);
  for(const box of await page.locator('dialog input[type=checkbox]').all())await box.check();await page.locator('dialog .primary').click();await expect(page.getByRole('heading',{name:'Seus vídeos.'})).toBeVisible();await capture(page,'07-videos-queue',info.project.name);
  await expect(page.locator('video')).toHaveCount(1,{timeout:15000});await capture(page,'08-video-verified',info.project.name);
  await page.getByRole('button',{name:/Gerar 1 restante/}).click();await capture(page,'09-remainder-consent',info.project.name);
});
