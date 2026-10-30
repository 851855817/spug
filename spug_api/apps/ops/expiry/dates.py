"""Calendar dates in the configured platform timezone, including USE_TZ=False."""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings


DATE = re.compile(r'\d{4}-\d{2}-\d{2}', re.ASCII)
HISTORICAL = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', re.ASCII)


def platform_today(now=None):
    zone = ZoneInfo(settings.TIME_ZONE)
    now = now or datetime.now(zone)
    return (now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)).date()


def parse_manual(value):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise ValueError('到期日必须为 YYYY-MM-DD 格式的有效日期')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError('到期日必须为 YYYY-MM-DD 格式的有效日期') from None


def parse_original(value):
    if not isinstance(value, str):
        return None
    try:
        if DATE.fullmatch(value):
            return date.fromisoformat(value)
        if HISTORICAL.fullmatch(value):
            return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').date()
    except ValueError:
        pass
    return None


def classify(expires_on, today):
    if expires_on is None:
        return 'unset', None
    days = (expires_on - today).days
    return ('expired' if days < 0 else 'within_7' if days <= 7 else
            'within_30' if days <= 30 else 'valid'), days


def reminder_stage(days):
    if days is None or days > 30:
        return None
    return 'expired' if days < 0 else '1d' if days <= 1 else '7d' if days <= 7 else '30d'
