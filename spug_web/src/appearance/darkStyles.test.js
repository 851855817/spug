// Exercise actual AntD Less output: nested imports previously broke && selectors.
const fs=require('fs'),path=require('path'),postcss=require('postcss');
const {execFileSync}=require('child_process');
const {scopeDarkCss}=require('../../config-overrides');

test('compiled dark components stay scoped, preserve compound layout selectors and keyframes',async()=>{
  const filename=path.join(__dirname,'antd.dark.less');
  const source=fs.readFileSync(filename,'utf8').replace("~antd/dist/antd.dark.less",require.resolve('antd/dist/antd.dark.less').replace(/\\/g,'/'));
  // Jest 24 resolves Less's ESM dependencies differently; use the actual build Node runtime.
  const compiled=execFileSync(process.execPath,['-e',"require('less').render(process.argv[1],{filename:process.argv[2],javascriptEnabled:true}).then(r=>process.stdout.write(r.css)).catch(e=>{console.error(e);process.exit(1)})",source,filename],{encoding:'utf8',maxBuffer:5*1024*1024});
  const css=postcss.parse(scopeDarkCss(compiled));
  const selectors=[];
  css.walkRules(rule=>{if(rule.parent.type==='atrule'&&/keyframes$/.test(rule.parent.name))return;selectors.push(...rule.selectors);});
  expect(selectors).toContain(":where(html[data-kops-mode='dark']) .ant-layout.ant-layout-has-sider");
  expect(selectors).toContain(":where(html[data-kops-mode='dark']) .ant-modal-content");
  expect(selectors).toContain(":where(html[data-kops-mode='dark']) .ant-input");
  expect(selectors.every(selector=>selector.startsWith(":where(html[data-kops-mode='dark'])"))).toBe(true);
  expect(scopeDarkCss('@keyframes x {to {opacity:0}} .example:hover {color:red}')).toContain('to {opacity:0}');
  expect(scopeDarkCss('html {color:red}')).not.toContain(' html');
},20000);
