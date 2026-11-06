# Frozen account-resources migration; production graph is pinned.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0002_account_resources'),
        ('app', '0001_initial'),
        ('deploy', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeployUpload',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('path', models.CharField(max_length=80, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('deploy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.deploy')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='account.user')),
            ],
            options={
                'db_table': 'deploy_resource_uploads',
            },
        ),
    ]
