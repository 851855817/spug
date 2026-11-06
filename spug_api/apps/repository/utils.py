# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django_redis import get_redis_connection
from django.conf import settings
from django.db import close_old_connections
from libs.utils import AttrDict, human_datetime, render_str, human_seconds_time
from libs.executor import Executor
from apps.repository.models import Repository
from apps.config.utils import compose_configs
from apps.app.utils import fetch_repo
from apps.deploy.helper import Helper, SpugError
from apps.deploy import resources
import json
import uuid
import time
import os
import shlex
from pathlib import PurePosixPath

REPOS_DIR = settings.REPOS_DIR
BUILD_DIR = settings.BUILD_DIR


def dispatch(rep: Repository, helper=None, language='zh'):
    if rep.pk:
        rep = Repository.objects.get(pk=rep.pk)
    try:
        authorize = resources.execution_guard(rep, parent=getattr(helper, 'authorize', None))
    except (ValueError, PermissionError):
        if rep.pk:
            Repository.objects.filter(pk=rep.pk, status__in=['0', '1']).update(status='2')
        raise
    rep.status = '1'
    alone_build = helper is None
    if not helper:
        rds = get_redis_connection()
        helper = Helper.make(rds, rep.deploy_key, ['local'], language)
        rep.save()
    helper.authorize = authorize
    try:
        helper.set_deploy_process('local')
        api_token = uuid.uuid4().hex
        resources.issue_config_token(helper.rds, api_token, getattr(helper, 'execution_record', rep), authorize())
        env = AttrDict(
            SPUG_APP_NAME=rep.app.name,
            SPUG_APP_KEY=rep.app.key,
            SPUG_APP_ID=str(rep.app_id),
            SPUG_DEPLOY_ID=str(rep.deploy_id),
            SPUG_BUILD_ID=str(rep.id),
            SPUG_ENV_ID=str(rep.env_id),
            SPUG_ENV_KEY=rep.env.key,
            SPUG_VERSION=rep.version,
            SPUG_BUILD_VERSION=rep.spug_version,
            SPUG_API_TOKEN=api_token,
            SPUG_REPOS_DIR=REPOS_DIR,
        )
        helper.send_clear('local')
        helper.send_info('local', f'应用名称: {rep.app.name}\r\n', with_time=False)
        helper.send_info('local', f'执行环境: {rep.env.name}\r\n', with_time=False)
        extras = json.loads(rep.extra)
        if extras[0] == 'branch':
            env.update(SPUG_GIT_BRANCH=extras[1], SPUG_GIT_COMMIT_ID=extras[2])
            helper.send_info('local', f'代码分支: {extras[1]}/{extras[2][:8]}\r\n', with_time=False)
        else:
            env.update(SPUG_GIT_TAG=extras[1])
            helper.send_info('local', f'代码版本: {extras[1]}', with_time=False)
        helper.send_info('local', f'执行人员: {rep.created_by.nickname}\r\n', with_time=False)
        helper.send_info('local', f'执行时间: {human_datetime()}\r\n', with_time=False)
        helper.send_warn('local', '.' * 50 + '\r\n\r\n')
        helper.send_info('local', '构建准备...        ', status='doing')

        # append configs
        configs = compose_configs(rep.app, rep.env_id)
        configs_env = {f'_SPUG_{k.upper()}': v for k, v in configs.items()}
        env.update(configs_env)

        _build(rep, helper, env)
        rep.status = '5'
        helper.set_deploy_success('local')
    except Exception as e:
        rep.status = '2'
        helper.set_deploy_fail('local')
        if not isinstance(e, SpugError):
            helper.send_error('local', f'Exception: {e}', with_break=False)
        if not alone_build:
            raise e
    finally:
        # cleanup must never mask the build result, local_raw raises on failure
        try:
            # Cleanup is a fixed, validated workspace operation, not a user hook.
            from pathlib import Path
            import shutil
            root = Path(REPOS_DIR).resolve()
            workspace = (root / rep.spug_version).resolve()
            if workspace.parent != root or workspace.name == str(rep.deploy_id):
                raise ValueError('Invalid build workspace')
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception as e:
            print(f'clean build workspace of {rep.spug_version} error: {e}')
        close_old_connections()
        if alone_build:
            helper.clear()
            # the record may have been removed while building, keep the worker alive
            if Repository.objects.filter(pk=rep.id).exists():
                rep.save()
            return rep
        elif rep.status == '5':
            rep.save()


def _build(rep: Repository, helper, env):
    flag = time.time()
    extend = rep.deploy.extend_obj
    git_dir = os.path.join(REPOS_DIR, str(rep.deploy_id))
    build_dir = os.path.join(REPOS_DIR, rep.spug_version)
    tar_file = os.path.join(BUILD_DIR, f'{rep.spug_version}.tar.gz')
    env.update(SPUG_DST_DIR=render_str(extend.dst_dir, env))
    fetch_repo(rep.deploy_id, extend.git_repo, user=helper.authorize(), authorize=helper.authorize)
    helper.send_success('local', '完成√\r\n')

    with Executor(env, authorize=helper.authorize) as et:
        helper.save_pid(et.pid, 'local')
        if extend.hook_pre_server:
            helper.send_info('local', '检出前任务...\r\n')
            helper.local(et, f'cd {git_dir} && {extend.hook_pre_server}')

        helper.send_info('local', '执行检出...        ')
        tree_ish = env.get('SPUG_GIT_COMMIT_ID') or env.get('SPUG_GIT_TAG')
        if not isinstance(tree_ish, str) or not tree_ish or tree_ish.startswith('-'):
            raise ValueError('构建版本无效')
        command = f'cd {shlex.quote(git_dir)} && git archive --prefix={shlex.quote(rep.spug_version + "/")} {shlex.quote(tree_ish)} | (cd .. && tar xf -)'
        helper.local_raw(command)
        helper.send_success('local', '完成√\r\n')

        if extend.hook_post_server:
            helper.send_info('local', '检出后任务...\r\n')
            helper.local(et, f'cd {build_dir} && {extend.hook_post_server}')

        helper.send_info('local', '执行打包...        ')
        filter_rule, exclude, contain = json.loads(extend.filter_rule), '', shlex.quote(rep.spug_version)
        files = helper.parse_filter_rule(filter_rule['data'], env=env)
        if any('..' in PurePosixPath(x).parts for x in files):
            raise ValueError('构建文件范围不能超出工作目录')
        if files:
            if filter_rule['type'] == 'exclude':
                excludes = []
                for x in files:
                    if x.startswith('/'):
                        excludes.append(shlex.quote(f'--exclude={rep.spug_version}{x}'))
                    else:
                        excludes.append(shlex.quote(f'--exclude={x}'))
                exclude = ' '.join(excludes)
            else:
                contain = ' '.join(shlex.quote(f'{rep.spug_version}/{x.lstrip("/")}') for x in files)
        helper.local_raw(f'mkdir -p {shlex.quote(BUILD_DIR)} && cd {shlex.quote(REPOS_DIR)} && tar zcf {shlex.quote(tar_file)} {exclude} {contain}')
        helper.send_success('local', '完成√\r\n')
        helper.set_cross_env(rep.spug_version, et.get_envs())
        human_time = human_seconds_time(time.time() - flag)
        helper.send_success('local', f'\r\n** 构建成功，耗时：{human_time} **\r\n', status='success')
