/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
const {override, addDecoratorsLegacy, addLessLoader} = require('customize-cra');
const postcss = require('postcss');

// Compile AntD at top level first: nesting its && selectors in Less breaks layout.
// :where confines dark styles without outranking the existing page CSS modules.
function scopeDarkCss(css) {
  const root = postcss.parse(css);
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
