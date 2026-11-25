import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir:'./tests/e2e/specs',
  outputDir:'./qa/playwright-artifacts',
  workers:1,
  retries:0,
  timeout:45_000,
  expect:{timeout:10_000},
  reporter:[['line'],['json',{outputFile:'qa/playwright-report.json'}]],
  use:{baseURL:process.env.FLOWLOCAL_TEST_URL,trace:'on',video:'retain-on-failure',screenshot:'only-on-failure'},
  projects:[
    {name:'fixture-390',testMatch:'**/visual.spec.ts',use:{browserName:'chromium',channel:'chrome',viewport:{width:390,height:844}}},
    {name:'fixture-768',testMatch:'**/visual.spec.ts',use:{browserName:'chromium',channel:'chrome',viewport:{width:768,height:1024}}},
    {name:'fixture-1440',use:{browserName:'chromium',channel:'chrome',viewport:{width:1440,height:900}}}
  ]
});
