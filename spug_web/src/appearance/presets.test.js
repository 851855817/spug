import { PRESETS, presetFor, themeVariables } from './presets';

function contrast(first, second) {
  const luminance = hex => {
    const rgb = hex.replace('#','').match(/../g).map(value => parseInt(value,16)/255)
      .map(value => value <= .04045 ? value / 12.92 : ((value+.055)/1.055)**2.4);
    return rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722;
  };
  const a=luminance(first), b=luminance(second);
  return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
}

test('existing and six refined palettes preserve readable text, links and filled buttons', () => {
  expect(PRESETS.map(row=>row.id)).toEqual(['classic-blue','jade','indigo','graphite','night',
    'obsidian-gold','midnight-teal','moonlit-violet','porcelain','pine-mist','champagne']);
  for(const preset of PRESETS) {
    const colors=preset.colors;
    for(const surface of ['surface','page','raised']) {
      expect(contrast(colors.text,colors[surface])).toBeGreaterThanOrEqual(4.5);
      expect(contrast(colors.muted,colors[surface])).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(colors.primary,colors.surface)).toBeGreaterThanOrEqual(4.5);
    if(preset.collection==='refined') for(const surface of ['page','raised','subtle','selected']) {
      expect(contrast(colors.primary,colors[surface])).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(colors.onPrimary,colors.primary)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colors.onPrimary,colors.primaryHover)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colors.sidebarText,colors.sidebar)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colors.success,colors.successBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colors.warning,colors.warningBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colors.error,colors.errorBg)).toBeGreaterThanOrEqual(4.5);
    expect(Object.values(themeVariables(preset.id)).every(value=>/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value))).toBe(true);
  }
});
test('switching back clears every derived dark accent through a complete token set',()=>{
  const keys=Object.keys(themeVariables('night')).sort();
  for(const preset of PRESETS) expect(Object.keys(themeVariables(preset.id)).sort()).toEqual(keys);
  expect(themeVariables('obsidian-gold')['--kops-dark-primary-1']).not.toBe(themeVariables('night')['--kops-dark-primary-1']);
  expect(themeVariables('night')['--kops-dark-primary-1']).toBe('#1B2B46');
});
test('unknown palette values cannot become CSS or replace fixed colors', () => {
  expect(presetFor('url(javascript:1)')).toBe(PRESETS[0]);
  expect(presetFor(null)).toBe(PRESETS[0]);
  expect(themeVariables('night')['--kops-surface']).toBe('#172234');
});
