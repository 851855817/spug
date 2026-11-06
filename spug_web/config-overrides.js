/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
const {override, addDecoratorsLegacy, addLessLoader} = require('customize-cra');
const postcss = require('postcss');
const valueParser = require('postcss-value-parser');
// Only the known neutral/brand colors from our independently compiled dark base.
// Keep semantic AntD status colors, image URLs and CSS strings untouched.
const darkColors = {
  '#101827':'page','#172234':'surface','#1e2c42':'raised','#e5edf7':'text','#a8b5ca':'muted',
  '#94a3b8':'disabled','#526178':'border','#3b4a60':'border-soft','#223149':'subtle',
  '#243d5d':'selected','#0b1320':'sidebar','#60a5fa':'primary',
  '#4681d4':'accent-hover','#8ac2ff':'accent-active','#076eed':'range-border','#054ca2':'range-hover',
};
['#1b2b46','#233a5c','#2d496f','#385d8d','#4677b5','#60a5fa','#7fb2eb','#abd1f5','#d5e9f9','#ecf5fb']
  .forEach((color,index)=>{if(index!==5)darkColors[color]='dark-primary-'+(index+1);});

// Compile AntD at top level first: nesting its && selectors in Less breaks layout.
// :where confines dark styles without outranking the existing page CSS modules.
function scopeDarkCss(css) {
  const root = postcss.parse(css);
  root.walkDecls(decl=>{
    const parsed=valueParser(decl.value);
    parsed.walk(node=>{
      if(node.type==='function') {
        if(node.value.toLowerCase()==='url') return false;
        const value=valueParser.stringify(node);
        const compact=value.toLowerCase().replace(/\s/g,'');
        const token=compact==='rgba(96,165,250,0.2)' ? 'focus-ring' :
          compact==='rgba(96,165,250,0.12)' ? 'focus-subtle' : null;
        if(token) {node.type='word';node.value='var(--kops-'+token+', '+value+')';return false;}
      }
      if(node.type==='word'&&darkColors[node.value.toLowerCase()]) {
        node.value='var(--kops-'+darkColors[node.value.toLowerCase()]+', '+node.value+')';
      }
    });
    decl.value=parsed.toString();
  });
  const scope = ":where(html[data-kops-mode='dark'])";
  root.walkRules(rule => {
    for (let parent = rule.parent; parent; parent = parent.parent) {
      if (parent.type === 'atrule' && /keyframes$/i.test(parent.name)) return;
    }
    rule.selectors = rule.selectors.map(selector => {
      if (/^(html|:root)(?=[\s.#:[>+~]|$)/.test(selector)) return selector.replace(/^(html|:root)/, scope);
      return scope + ' ' + selector;
    });
  });
  return root.toString();
}
const darkScope = {install(_less, manager) {
  manager.addPostProcessor({process(css, extra) {
    return extra.options.filename.replace(/\\/g, '/').endsWith('/appearance/antd.dark.less') ? scopeDarkCss(css) : css;
  }});
}};

module.exports = override(
  addDecoratorsLegacy(),
  addLessLoader({
    lessOptions: {
      javascriptEnabled: true,
      plugins: [darkScope]
    }
  }),
);
module.exports.scopeDarkCss = scopeDarkCss;
