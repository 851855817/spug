"""Create-only ORM import. Does not invoke the task executor or contact any host.

Run with the deployed application's Python environment. A required out-of-band
bundle SHA256 ties import to the exact reviewed payload. Backups remain private.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

FIELDS = ('name', 'type', 'desc', 'body', 'interpreter', 'host_ids', 'parameters')


def normalized(row):
    value = {key: row[key] for key in FIELDS}
    for key in ('host_ids', 'parameters'):
        if isinstance(value[key], str):
            value[key] = json.loads(value[key])
    return value


def plan_import(existing, templates):
    names = [item['name'] for item in templates]
    if len(names) != len(set(names)):
        raise ValueError('duplicate template names in bundle')
    create, skip = [], []
    for item in templates:
        matches = [row for row in existing if row['name'] == item['name']]
        if len(matches) > 1:
            raise ValueError('duplicate existing name; refusing import: ' + item['name'])
        if matches:
            if normalized(matches[0]) != normalized(item):
                raise ValueError('existing template differs; refusing overwrite: ' + item['name'])
            skip.append(item['name'])
        else:
            create.append(item)
    return create, skip


def private_json(path, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--owner', default='admin')
    parser.add_argument('--app-root', default='/data/spug/spug_api')
    parser.add_argument('--backup-dir', default='/data/spug/backups/inspection-templates')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    raw = Path(args.bundle).read_bytes()
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != args.sha256:
        raise ValueError('bundle size or SHA256 mismatch')
    bundle = json.loads(raw)
    if bundle['schema'] != 1 or len(bundle['templates']) != 12:
        raise ValueError('expected reviewed schema and 12 templates')
    for item in bundle['templates']:
        if (hashlib.sha256(item['body'].encode('utf-8')).hexdigest() != item['sha256']
                or item['interpreter'] != 'python' or item['host_ids'] != []
                or len(item['name']) > 50 or len(item['type']) > 50 or len(item['desc']) > 255):
            raise ValueError('template metadata/hash mismatch')

    sys.path.insert(0, args.app_root)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')
    import django
    django.setup()
    from django.db import transaction
    from apps.account.models import User
    from apps.exec.models import ExecTemplate, ExecHistory

    user = User.objects.get(username=args.owner, is_active=True, is_supper=True, is_deleted=False)
    report = {'bundle_sha256': args.sha256, 'owner_id': user.id, 'created': []}
    with transaction.atomic():
        before = list(ExecTemplate.objects.select_for_update().all().values())
        report['templates_before'] = len(before)
        report['histories_before'] = ExecHistory.objects.count()
        create, skip = plan_import(before, bundle['templates'])
        report['skipped'] = skip
        if not args.apply:
            print(json.dumps({'dry_run': True, 'create': [item['name'] for item in create],
                              'skip': skip, 'histories': report['histories_before']}, ensure_ascii=False))
            return

        backup_dir = Path(args.backup_dir)
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = backup_dir.lstat()
        if (backup_dir.resolve() != backup_dir or not stat.S_ISDIR(mode.st_mode)
                or mode.st_uid != os.geteuid() or stat.S_IMODE(mode.st_mode) & 0o077):
            raise ValueError('backup directory must be private, owned and not a symlink')
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = backup_dir / (stamp + '-before.json')
        private_json(backup, before)
        report['backup_file'] = str(backup)

        for item in create:
            fields = normalized(item)
            for key in ('host_ids', 'parameters'):
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
            template = ExecTemplate.objects.create(created_by=user, **fields)
            report['created'].append({'id': template.id, 'name': template.name, 'sha256': item['sha256']})

        after = list(ExecTemplate.objects.all().values())
        pending, _ = plan_import(after, bundle['templates'])
        if pending or len(after) != len(before) + len(create):
            raise ValueError('post-import verification failed')
        report['templates_after'] = len(after)
        report['histories_after'] = ExecHistory.objects.count()
        report['executed_by_importer'] = False
        private_json(backup_dir / (stamp + '-result.json'), report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
