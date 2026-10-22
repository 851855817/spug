"""Isolated test configuration; never reads/writes the production database."""
from spug.settings import *

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
ALLOWED_HOSTS = ['testserver', '127.0.0.1', 'localhost']
INSTALLED_APPS = [*INSTALLED_APPS, 'apps.baota'] if 'apps.baota' not in INSTALLED_APPS else INSTALLED_APPS
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
