# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.conf import settings
from apps.app.models import Deploy
from apps.setting.utils import AppSetting
from libs.gitlib import Git
from apps.account import access
from urllib.parse import urlsplit
import shutil
import os


def parse_envs(text):
    data = {}
    if text:
        for line in text.split('\n'):
            fields = line.split('=', 1)
            if len(fields) != 2 or fields[0].strip() == '':
                raise Exception(f'解析自定义全局变量{line!r}失败，确认其遵循 key = value 格式')
            data[fields[0].strip()] = fields[1].strip()
    return data


def fetch_versions(deploy: Deploy, user=None):
    from apps.deploy import resources
    actor = user or deploy.created_by_id
    resources.authorize(actor, deploy)
    def authorize():
        current = Deploy.objects.get(pk=deploy.pk)
        if current.extend_obj.git_repo != deploy.extend_obj.git_repo:
            access.reject(actor, '仓库配置已改变，请重试')
        return resources.authorize(actor, current)
    return fetch_repo(deploy.pk, deploy.extend_obj.git_repo, actor, authorize)


def fetch_repo(deploy_id, git_repo, user=None, authorize=None):
    current = access.current_user(user)
    from apps.deploy import resources
    authorize = authorize or (lambda: resources.authorize(current.pk, Deploy.objects.get(pk=deploy_id)))
    authorize()
    parsed = urlsplit(git_repo)
    if not current.is_supper and (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password):
        access.reject(current, '普通账号仅能使用公开 HTTP(S) 仓库；SSH 私有仓库需管理员配置和执行')
    os.makedirs(settings.REPOS_DIR, exist_ok=True)
    repo_dir = os.path.join(settings.REPOS_DIR, str(deploy_id))
    pkey = AppSetting.get_default('private_key') if current.is_supper else None
    with Git(git_repo, repo_dir, pkey, authorize=authorize, public_only=not current.is_supper) as git:
        return git.fetch_branches_tags()


def remove_repo(deploy_id):
    shutil.rmtree(os.path.join(settings.REPOS_DIR, str(deploy_id)), True)
