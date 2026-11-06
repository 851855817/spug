# Frozen account-resources migration; production graph is pinned.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0002_account_resources'),
        ('alarm', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AlarmResource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('host_ids', models.JSONField(default=list)),
                ('alarm', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='resource_scope', to='alarm.alarm')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='account.user')),
            ],
            options={
                'db_table': 'alarm_resource_scopes',
            },
        ),
    ]
