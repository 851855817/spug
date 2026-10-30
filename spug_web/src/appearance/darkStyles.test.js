// Exercise actual AntD Less output: nested imports previously broke && selectors.
const fs=require('fs'),path=require('path'),postcss=require('postcss');
const {execFileSync}=require('child_process');
const {scopeDarkCss}=require('../../config-overrides');

test('known dark component surfaces and accent colors use palette tokens without rewriting assets or strings',()=>{
  const css=scopeDarkCss('.ant-modal-content {background:#172234;color:#e5edf7;border:1px solid #3b4a60;box-shadow:0 0 0 2px rgba(96, 165, 250, 0.2)} .x{color:#4677b5;background:#1b2b46;content:"#172234";background-image:url("/a/#172234")}');
  expect(css).toContain('var(--kops-surface, #172234)');
  expect(css).toContain('var(--kops-text, #e5edf7)');
  expect(css).toContain('var(--kops-border-soft, #3b4a60)');
  expect(css).toContain('var(--kops-focus-ring, rgba(96, 165, 250, 0.2))');
  expect(css).toContain('var(--kops-dark-primary-5, #4677b5)');
  expect(css).toContain('var(--kops-dark-primary-1, #1b2b46)');
  expect(css).toContain('content:"#172234"');
  expect(css).toContain('url("/a/#172234")');
});

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
  const range=[];
  css.walkRules(rule=>{if(rule.selector.includes('ant-picker-cell-range-hover'))rule.walkDecls(decl=>range.push(decl.value));});
  expect(range.some(value=>value.includes('var(--kops-range-border, #076eed)'))).toBe(true);
  expect(range.some(value=>value.includes('var(--kops-range-hover, #054ca2)'))).toBe(true);
  expect(scopeDarkCss('@keyframes x {to {opacity:0}} .example:hover {color:red}')).toContain('to {opacity:0}');
  expect(scopeDarkCss('html {color:red}')).not.toContain(' html');
},20000);
