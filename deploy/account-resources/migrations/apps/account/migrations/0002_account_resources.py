# Frozen account-resources migration; production graph is pinned.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0001_initial'),
        ('baota', '0006_panelinventorylease_panelinventorysnapshot_and_more'),
        ('host', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountResourcePolicy',
            fields=[
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='resource_policy', serialize=False, to='account.user')),
                ('server_limit', models.PositiveIntegerField(default=0, null=True)),
            ],
            options={
                'db_table': 'account_resource_policies',
            },
        ),
        migrations.CreateModel(
            name='ResourceQuotaLock',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
            ],
            options={
                'db_table': 'account_resource_quota_lock',
            },
        ),
        migrations.CreateModel(
            name='ResourceOwnership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('host', models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='resource_ownership', to='host.host')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='resource_ownerships', to='account.user')),
                ('panel', models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='resource_ownership', to='baota.panel')),
            ],
            options={
                'db_table': 'account_resource_ownerships',
            },
        ),
        migrations.CreateModel(
            name='ResourceGroupOwner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(max_length=8)),
                ('group_id', models.PositiveBigIntegerField()),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='account.user')),
            ],
            options={
                'db_table': 'account_resource_group_owners',
            },
        ),
        migrations.CreateModel(
            name='ResourceGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('level', models.CharField(choices=[('read', 'read'), ('manage', 'manage')], max_length=8)),
                ('host', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='resource_grants', to='host.host')),
                ('panel', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='resource_grants', to='baota.panel')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='resource_grants', to='account.user')),
            ],
            options={
                'db_table': 'account_resource_grants',
            },
        ),
        migrations.CreateModel(
            name='ResourceAccessAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event', models.CharField(max_length=40)),
                ('detail', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='account.user')),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='account.user')),
            ],
            options={
                'db_table': 'account_resource_audit',
            },
        ),
        migrations.AddConstraint(
            model_name='resourceownership',
            constraint=models.CheckConstraint(check=models.Q(models.Q(('host__isnull', False), ('panel__isnull', True)), models.Q(('host__isnull', True), ('panel__isnull', False)), _connector='OR'), name='account_ownership_one_resource'),
        ),
        migrations.AddConstraint(
            model_name='resourcegroupowner',
            constraint=models.UniqueConstraint(fields=('kind', 'group_id'), name='account_owned_group'),
        ),
        migrations.AddConstraint(
            model_name='resourcegroupowner',
            constraint=models.CheckConstraint(check=models.Q(('kind__in', ('host', 'panel'))), name='account_group_kind'),
        ),
        migrations.AddConstraint(
            model_name='resourcegrant',
            constraint=models.CheckConstraint(check=models.Q(models.Q(('host__isnull', False), ('panel__isnull', True)), models.Q(('host__isnull', True), ('panel__isnull', False)), _connector='OR'), name='account_grant_one_resource'),
        ),
        migrations.AddConstraint(
            model_name='resourcegrant',
            constraint=models.CheckConstraint(check=models.Q(('level__in', ('read', 'manage'))), name='account_grant_level'),
        ),
        migrations.AddConstraint(
            model_name='resourcegrant',
            constraint=models.UniqueConstraint(fields=('user', 'host'), name='account_grant_user_host'),
        ),
        migrations.AddConstraint(
            model_name='resourcegrant',
            constraint=models.UniqueConstraint(fields=('user', 'panel'), name='account_grant_user_panel'),
        ),
    ]
