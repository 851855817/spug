# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from functools import wraps
from .utils import json_response


def auth(perm_list):
    def decorate(view_func):
        codes = perm_list.split('|')

        @wraps(view_func)
        def wrapper(*args, **kwargs):
            user = None
            for item in args[:2]:
                if hasattr(item, 'user'):
                    user = item.user
                    break
            if user:
                from apps.account import access
                try:
                    current, granted = access.permissions(user)
                    if current.is_supper or set(codes).intersection(granted):
                        for item in args[:2]:
                            if hasattr(item, 'user'):
                                item.user = current
                                break
                        return view_func(*args, **kwargs)
                    access.reject(current, '权限拒绝', codes=codes)
                except access.AccessDenied as exc:
                    return json_response(error=str(exc))
            return json_response(error='权限拒绝')

        return wrapper

    return decorate
