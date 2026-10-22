"""Disposable CI database only; never load a production connection string."""
import os

from .test_settings import *

if os.environ.get('BAOTA_CI_DB_HOST') != 'baota-ci-db':
    raise RuntimeError('This configuration requires the isolated baota-ci-db container')

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.mysql',
    'HOST': 'baota-ci-db',
    'PORT': '3306',
    'NAME': 'baota_ci',
    'USER': 'root',
    'PASSWORD': os.environ['BAOTA_CI_DB_PASSWORD'],
    'ATOMIC_REQUESTS': True,
    'OPTIONS': {'charset': 'utf8mb4', 'sql_mode': 'STRICT_TRANS_TABLES'},
    'TEST': {'NAME': 'test_baota_ci'},
}}
