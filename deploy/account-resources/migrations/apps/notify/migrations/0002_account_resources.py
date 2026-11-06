# Frozen account-resources migration; production graph is pinned.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0002_account_resources'),
        ('notify', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotifyAudience',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('permission', models.CharField(default='', max_length=100)),
                ('host_ids', models.JSONField(default=list)),
                ('panel_ids', models.JSONField(default=list)),
                ('unread', models.BooleanField(default=True)),
                ('notification', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audience', to='notify.notify')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='account.user')),
            ],
            options={
                'db_table': 'notify_resource_audience',
            },
        ),
        migrations.AddConstraint(
            model_name='notifyaudience',
            constraint=models.UniqueConstraint(fields=('notification', 'user'), name='unique_notify_resource_recipient'),
        ),
    ]
