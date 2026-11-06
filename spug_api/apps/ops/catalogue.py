"""Versioned fixed probes. No user scripts, remote paths or template imports."""
import shlex


PHP_VERSIONS = ('74', '80', '81', '82', '83', '84', '85')
# key, label, scalar kind, warning rule. Only these values can enter reports.
SCHEMAS = {
    'linux_inspection': (
        ('os_id', 'Linux 发行版', 'identifier', None),
        ('os_version', '发行版版本', 'version', None),
        ('kernel', '内核版本', 'version', None),
        ('cpu_count', '逻辑 CPU 数', 'integer', None),
        ('load_1', '一分钟负载', 'number', None),
        ('memory_available_percent', '可用内存 %', 'percent', 'low'),
        ('root_used_percent', '根分区使用 %', 'percent', 'high'),
    ),
    'php_environment': (
        ('php_version', 'PHP 版本', 'version', None),
        ('display_errors', '显示错误', 'boolean', 'enabled'),
        ('expose_php', '公开 PHP 标识', 'boolean', 'enabled'),
        ('allow_url_include', '允许远程包含', 'boolean', 'enabled'),
        ('extension_curl', 'cURL 扩展', 'boolean', 'disabled'),
        ('extension_openssl', 'OpenSSL 扩展', 'boolean', 'disabled'),
        ('extension_mbstring', 'mbstring 扩展', 'boolean', 'disabled'),
        ('extension_pdo_mysql', 'PDO MySQL 扩展', 'boolean', None),
    ),
    'web_environment': (
        ('nginx_available', 'Nginx 可执行文件', 'boolean', None),
        ('nginx_version', 'Nginx 版本', 'version', None),
        ('apache_available', 'Apache 可执行文件', 'boolean', None),
        ('apache_version', 'Apache 版本', 'version', None),
        ('port_80_listening', 'TCP 80 监听', 'boolean', 'disabled'),
        ('port_443_listening', 'TCP 443 监听', 'boolean', 'disabled'),
    ),
}

_JOBS = (
    {'code': 'linux_inspection', 'version': '1', 'name': 'Linux 基础巡检',
     'description': '采集发行版、内核和资源指标，区分告警与缺失数据。',
     'dependencies': ['Linux', 'python3'],
     'limitations': ['只读采集；资源指标为采集时点快照，差异也会包含资源变化。', '仅检查根分区。']},
    {'code': 'php_environment', 'version': '1', 'name': '宝塔 PHP 配置检查',
     'description': '检查指定宝塔 PHP CLI 的安全开关和固定扩展声明。',
     'dependencies': ['宝塔标准 PHP 安装目录', '指定版本的 PHP CLI'],
     'limitations': ['CLI 与网站 PHP-FPM 实际配置可能不同。', '不读取或返回完整配置，不修改 PHP。'],
     'php_versions': list(PHP_VERSIONS)},
    {'code': 'web_environment', 'version': '1', 'name': 'Web 服务检查',
     'description': '检查 Nginx/Apache 版本及本机 TCP 80/443 监听状态。',
     'dependencies': ['Linux', 'python3'],
     'limitations': ['可执行文件存在不代表服务健康；本机监听不代表公网可达。', '不读取站点配置、日志或证书私钥。']},
)


def jobs():
    return [dict(item) for item in _JOBS]


def get_job(code):
    for item in _JOBS:
        if code == item['code']:
            return dict(item)
    raise ValueError('不支持的标准作业')


def parameters(code, params):
    get_job(code)
    if not isinstance(params, dict):
        raise ValueError('作业参数必须为对象')
    allowed = {'php_version'} if code == 'php_environment' else set()
    if set(params) - allowed:
        raise ValueError('标准作业不接受脚本、路径或自定义参数')
    if code == 'php_environment':
        version = params.get('php_version', '74')
        if version not in PHP_VERSIONS:
            raise ValueError('不支持的 PHP 版本')
        return {'php_version': version}
    return {}


_LINUX = '''import json,os,re
v={}
try:
 for line in open('/etc/os-release'):
  k,sep,value=line.partition('=')
  if k in ('ID','VERSION_ID') and sep: v['os_id' if k=='ID' else 'os_version']=value.strip().strip(chr(34)).strip(chr(39))
except OSError: pass
v['kernel']=os.uname().release
v['cpu_count']=os.cpu_count()
try: v['load_1']=round(os.getloadavg()[0],2)
except OSError: pass
try:
 m={line.split(':')[0]:int(line.split()[1]) for line in open('/proc/meminfo') if line.startswith(('MemTotal:','MemAvailable:'))}
 v['memory_available_percent']=round(m['MemAvailable']*100/m['MemTotal'],2)
except (OSError,KeyError,ValueError,ZeroDivisionError): pass
try:
 s=os.statvfs('/'); v['root_used_percent']=round((s.f_blocks-s.f_bfree)*100/(s.f_blocks-s.f_bfree+s.f_bavail),2)
except (OSError,ZeroDivisionError): pass
print(json.dumps({'values':v},allow_nan=False))
'''

_WEB = '''import json,os,re,shutil,subprocess
v={}
for service,names,flag in [('nginx',['/www/server/nginx/sbin/nginx','nginx'],'-v'),('apache',['/www/server/apache/bin/httpd','apache2','httpd'],'-v')]:
 binary=next((shutil.which(n) for n in names if shutil.which(n)),None)
 v[service+'_available']=bool(binary)
 if binary:
  try:
   p=subprocess.run([binary,flag],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=8)
   m=re.search(rb'(?:nginx/|Apache/)([0-9][0-9A-Za-z.+_-]{0,60})',p.stdout[:4096])
   if m: v[service+'_version']=m.group(1).decode('ascii')
  except (OSError,subprocess.TimeoutExpired): pass
ports=set(); readable=False
for path in ['/proc/net/tcp','/proc/net/tcp6']:
 try:
  with open(path) as f:
   next(f)
   for line in f:
    fields=line.split()
    if len(fields)>3 and fields[3]=='0A': ports.add(int(fields[1].split(':')[1],16))
  readable=True
 except (OSError,ValueError): pass
if readable:
 v['port_80_listening']=80 in ports; v['port_443_listening']=443 in ports
print(json.dumps({'values':v},allow_nan=False))
'''

_PHP = '''$v=['php_version'=>PHP_VERSION];
foreach(['display_errors','expose_php','allow_url_include'] as $k){
 $raw=ini_get($k); if($raw===false){continue;}
 $enabled=filter_var($raw,FILTER_VALIDATE_BOOLEAN,FILTER_NULL_ON_FAILURE);
 if($k==='display_errors' && in_array(strtolower(trim($raw)),['stderr','stdout','2'],true)){$enabled=true;}
 if($enabled!==null){$v[$k]=$enabled;}
}
foreach(['curl','openssl','mbstring','pdo_mysql'] as $e){$v['extension_'.$e]=extension_loaded($e);}
echo json_encode(['values'=>$v]);'''


def command(code, params):
    params = parameters(code, params)
    if code == 'php_environment':
        binary = '/www/server/php/%s/bin/php' % params['php_version']
        return 'if [ -x %s ]; then %s -r %s; else exit 42; fi' % (
            shlex.quote(binary), shlex.quote(binary), shlex.quote(_PHP))
    return 'python3 -c %s' % shlex.quote(_LINUX if code == 'linux_inspection' else _WEB)
