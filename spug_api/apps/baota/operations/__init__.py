"""The only permitted Baota task operations. No arbitrary action/URL passthrough."""
from .certificates import ApplyCertificate, RenewCertificate
from .sites import AddSite, Basedir, Rewrite
from .software import InstallPHPExtension, InstallSoftware, UninstallSoftware
from .upgrade import UpgradePanel
from .php_update import UpdatePHP


OPERATIONS = {operation.code: operation for operation in (
    UpgradePanel(), AddSite(), Basedir(), Rewrite(), ApplyCertificate(), RenewCertificate(),
    InstallSoftware(), InstallPHPExtension(), UninstallSoftware(), UpdatePHP())}


def get_operation(code):
    if not isinstance(code, str) or code not in OPERATIONS:
        raise ValueError('不支持该面板操作')
    return OPERATIONS[code]


def operation_descriptors():
    return [{'code': v.code, 'category': v.category, 'label': v.label,
             'resources': list(v.resources)} for v in OPERATIONS.values()]
