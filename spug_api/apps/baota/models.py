from django.db import models

from apps.account.models import User
from libs import ModelMixin, human_datetime
from .security import decrypt_secret


class PanelGroup(models.Model, ModelMixin):
    name = models.CharField(max_length=50)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')

    def to_view(self):
        return {'id': self.id, 'name': self.name, 'parent_id': self.parent_id}

    class Meta:
        db_table = 'baota_groups'
        ordering = ('id',)
        constraints = [models.UniqueConstraint(fields=['parent', 'name'], name='baota_group_parent_name')]


class Panel(models.Model, ModelMixin):
    name = models.CharField(max_length=100)
    url = models.CharField(max_length=2048)
    username = models.CharField(max_length=100)
    password_ciphertext = models.TextField()
    group = models.ForeignKey(PanelGroup, on_delete=models.PROTECT, related_name='panels')
    desc = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    created_at = models.CharField(max_length=20, default=human_datetime)

    def to_view(self):
        data = self.to_dict(excludes=('password_ciphertext',))
        data['has_password'] = bool(self.password_ciphertext)
        return data

    def get_password(self):
        return decrypt_secret(self.password_ciphertext)

    class Meta:
        db_table = 'baota_panels'
        ordering = ('-id',)
