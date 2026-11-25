import {expect,Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

export async function open(page:Page,route='/criar'){
  await page.goto(`/#/${route}?token=${process.env.FLOWLOCAL_TEST_TOKEN}`);
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('nav')).toContainText('Criar');
}
export async function capture(page:Page,name:string,project:string){
  const dir=path.join('qa','screenshots',project);fs.mkdirSync(dir,{recursive:true});
  await page.screenshot({path:path.join(dir,`${name}.png`),fullPage:true,animations:'disabled'});
  fs.writeFileSync(path.join(dir,`${name}.json`),JSON.stringify({name,project,provider_mode:process.env.FLOWLOCAL_PROVIDER_MODE,viewport:page.viewportSize(),url:new URL(page.url()).hash.split('?')[0],captured_at:new Date().toISOString()},null,2));
}
export async function createBatch(page:Page,prompts='Plano aberto\n---\nCâmera aproxima devagar'){
  await open(page);
  await page.locator('#prompt-text').fill(prompts);
  await page.getByRole('button',{name:/Salvar fila/}).click();
  await expect(page.getByRole('heading',{name:'Pronto para gerar.'})).toBeVisible();
  await page.locator('[name=duration_seconds]').selectOption('3');
  await page.locator('[name=output_dir]').fill(process.env.FLOWLOCAL_TEST_OUTPUT!);
}
export async function simulate(page:Page){
  await page.getByRole('button',{name:'Gerar agora'}).click();
  await expect(page.locator('dialog')).toBeVisible();
}
