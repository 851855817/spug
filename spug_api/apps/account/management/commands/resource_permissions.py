import json
from django.core.management.base import BaseCommand, CommandError

from apps.account.resource_migration import impact_report, initialize_policies


class Command(BaseCommand):
    help = '只读列出资源权限影响；--apply 显式初始化旧普通账号额度，不共享资源或修改创建者'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--actor-id', type=int)

    def handle(self, *args, **options):
        applied = None
        if options['apply']:
            if not options['actor_id']:
                raise CommandError('--apply requires an active administrator --actor-id')
            applied = initialize_policies(options['actor_id'])
        result = impact_report()
        if applied is not None:
            result.update(mode='applied', initialized_account_ids=applied)
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
