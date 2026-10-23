"""Reviewed Linux file protocol. No arbitrary action or filename forwarding."""
import posixpath
import re
from collections import OrderedDict

from ..client import PanelAPIError
from ..upload_store import absolute_directory, relative_path
from .base import Operation, strict


class FileUpload(Operation):
    code = 'file_upload'
    category = '文件管理'
    label = '上传文件 / 目录'
    resources = ()

    def validate(self, params):
        strict(params, ('bundle_id', 'target_dir', 'directory_source'), ('bundle_id', 'target_dir'))
        if not isinstance(params['bundle_id'], str) or not re.fullmatch('[0-9a-f]{32}', params['bundle_id']):
            raise ValueError('上传文件包无效')
        source = params.get('directory_source', 'common')
        if source not in ('individual', 'common'):
            raise ValueError('目标目录来源无效')
        return {'bundle_id': params['bundle_id'], 'target_dir': absolute_directory(params['target_dir']),
                'directory_source': source}


def destination(directory, source):
    directory = absolute_directory(directory)
    name = directory + '/' + relative_path(source.relative_path)
    if len(name) > 2048 or len(name.encode('utf-8')) > 4095:
        raise ValueError('最终文件路径过长')
    return name


class RemotePaths:
    """Bounded exact-path listings, including each existing ancestor's link flag.

    Missing GetDirNew paths silently fall back on the panel; never query them.
    Listings and existence responses must agree. This is a just-before-send
    check; the official upload API does not provide atomic compare-and-write.
    """
    PAGE_SIZE = 100
    MAX_ENTRIES = 10000
    MAX_CACHE_ENTRIES = 20000
    MAX_CACHE_DIRS = 256

    def __init__(self, client):
        self.client, self.cache = client, OrderedDict()
        self.cache_entries = 0

    def listing(self, path):
        if path in self.cache:
            self.cache.move_to_end(path)
            return self.cache[path]
        found = {}
        for page in range(1, self.MAX_ENTRIES // self.PAGE_SIZE + 2):
            response = self.client.request('/files', 'GetDirNew',
                {'path': path, 'p': page, 'showRow': self.PAGE_SIZE, 'sort': 'name', 'reverse': 'False'})
            if not isinstance(response, dict) or response.get('path') != path:
                raise PanelAPIError('目录读取失败或路径发生回退')
            dirs, files = response.get('dir'), response.get('files')
            if (not isinstance(dirs, list) or not isinstance(files, list)
                    or len(dirs) + len(files) > self.PAGE_SIZE):
                raise PanelAPIError('目录分页格式无效')
            for kind, entries in (('directory', dirs), ('file', files)):
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise PanelAPIError('目录条目格式无效')
                    name = entry.get('nm')
                    try:
                        name_size = len(name.encode('utf-8')) if isinstance(name, str) else 0
                    except UnicodeError:
                        raise PanelAPIError('目录条目编码无效') from None
                    if (not isinstance(name, str) or not name or '/' in name or name in ('.', '..')
                            or name_size > 255 or name in found or not isinstance(entry.get('lnk'), str)):
                        raise PanelAPIError('目录条目或分页不可靠')
                    found[name] = {'kind': kind, 'link': bool(entry['lnk'])}
            if len(found) > self.MAX_ENTRIES:
                raise PanelAPIError('远端目录过大，请缩小目标目录')
            if len(dirs) + len(files) < self.PAGE_SIZE:
                if len(found) <= self.MAX_CACHE_ENTRIES:
                    while self.cache and (len(self.cache) >= self.MAX_CACHE_DIRS
                            or self.cache_entries + len(found) > self.MAX_CACHE_ENTRIES):
                        _, expired = self.cache.popitem(last=False)
                        self.cache_entries -= len(expired)
                    self.cache[path] = found
                    self.cache_entries += len(found)
                return found
        raise PanelAPIError('远端目录分页超出限制')

    def inspect(self, filename):
        parts = filename[1:].split('/')
        parent, missing, leaf = '/', False, None
        for i, part in enumerate(parts):
            if missing:
                continue
            entry = self.listing(parent).get(part)
            if entry is None:
                missing = True
                continue
            if entry['link']:
                raise ValueError('目标文件或父目录为符号链接，已阻止上传')
            if i < len(parts) - 1 and entry['kind'] != 'directory':
                raise ValueError('父路径已有同名文件，已阻止上传')
            if i == len(parts) - 1:
                leaf = entry
                if leaf['kind'] == 'directory':
                    raise ValueError('目标路径已有同名目录，已阻止上传')
            parent = posixpath.join(parent, part)
        response = self.client.request('/files', 'upload_files_exists', {'files': filename})
        if not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], dict):
            raise PanelAPIError('文件存在性返回格式无效')
        row = response[0]
        if (row.get('filename') != filename or type(row.get('exists')) is not bool
                or type(row.get('isfile')) is not bool or type(row.get('size')) is not int
                or type(row.get('mtime')) is not int or row['size'] < 0 or row['mtime'] < 0
                or row['exists'] != (leaf is not None) or (row['exists'] and not row['isfile'])):
            raise PanelAPIError('文件类型或存在性无法可靠确认')
        return {'exists': row['exists'], 'size': row['size'] if row['exists'] else 0,
                'mtime': row['mtime'] if row['exists'] else 0}


def verify_content(client, filename, source):
    value = client.file_sha256(filename, maximum=max(1, source.size))
    if (not isinstance(value, dict) or value.get('size') != source.size
            or value.get('sha256') != source.sha256):
        raise ValueError('文件内容校验不一致')
    return {'size': source.size, 'sha256': source.sha256}
