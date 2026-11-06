"""Opt-in local MariaDB test configuration; only the loopback endpoint is used."""
import json
import os
from pathlib import Path

import pymysql
pymysql.install_as_MySQLdb()

from .test_settings import *  # noqa: E402,F403

config = json.loads(Path(os.environ['RESOURCE_TEST_DB_CONFIG']).read_text())
if not config['name'].startswith('kops_resource_test') or not 1024 < int(config['port']) < 65536:
    raise ValueError('Use a dedicated local resource test database')
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.mysql', 'HOST': '127.0.0.1',
    'PORT': int(config['port']), 'NAME': config['name'], 'USER': config['user'],
    'PASSWORD': config['password'], 'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed'},
    'TEST': {'NAME': config['name']},
}}
