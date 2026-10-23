"""Build reproducible standalone Spug bodies from reviewed, local files only."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def build_bundle():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    common = (ROOT / 'common.py').read_text(encoding='utf-8')
    items = []
    for spec in manifest['templates']:
        references = '\n'.join('# Reference: ' + manifest['sources'][source]['url']
                               for source in spec['sources'])
        header = ('# Reviewed inspection template; Python 3.8+ / Linux; read-only intent.\n'
                  '# Independent implementation, not an unmodified upstream script.\n'
                  '# tldr-pages team/contributors: command docs CC-BY-4.0 where referenced.\n'
                  '# Review and limitations: deploy/inspection-templates/README.md in this fork.\n'
                  + references + '\n\n')
        body = header + common + '\n\n' + (ROOT / 'checks' / (spec['key'] + '.py')).read_text(encoding='utf-8')
        body += '\n\nif __name__ == "__main__":\n    sys.exit(entrypoint(main))\n'
        compile(body, spec['key'], 'exec')
        if '\nSPUG_PY_EOF\n' in body:
            raise ValueError('Spug Python wrapper delimiter in body')
        params = []
        if spec.get('php_parameter'):
            params.append({'id': 1, 'name': 'PHP版本', 'variable': 'PHP_VERSION',
                           'type': 'string', 'required': False, 'default': 'all',
                           'desc': 'all检查全部宝塔版本，也可填7.4或74；只读检查'})
        if spec.get('domain_parameter'):
            params.append({'id': 1, 'name': '公网域名', 'variable': 'DOMAIN',
                           'type': 'string', 'required': True, 'default': '',
                           'desc': '例如www.your-domain.com，不带协议、端口、路径；仅HTTPS 443'})
        items.append({'key': spec['key'], 'name': spec['name'], 'type': spec['type'],
                      'desc': spec['desc'], 'interpreter': 'python', 'host_ids': [],
                      'parameters': params, 'body': body,
                      'sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()})
    return {'schema': 1, 'review_date': manifest['review_date'],
            'sources': manifest['sources'], 'templates': items}


if __name__ == '__main__':
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'bundle.json'
    target.write_text(json.dumps(build_bundle(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Built 12 templates: ' + str(target))
