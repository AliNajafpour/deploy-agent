"""Account-scoped hosting choices from the official CLI's /me contract.

Hardware month estimates use the CLI's 720-hour convention.  They are not
quotes or promises that an unknown workload fits a plan.
"""
import copy
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from .liara import LiaraError


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        if len(str(value)) > 80:
            return None
        number = Decimal(str(value))
        if (not number.is_finite() or number < 0 or (positive and not number)
                or number > Decimal('1e18') or number.as_tuple().exponent < -12):
            return None
        return number
    except InvalidOperation:
        return None


def _amount(value):
    return format(value.normalize(), 'f') if value is not None else None


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value) else ''


def _section(value):
    if not isinstance(value, dict):
        return {}, False
    if 'available' in value:
        return (value.get('data', {}), value.get('available') is True)
    return value, True


def _stale(value, now):
    if not isinstance(value, dict):
        return False
    if value.get('stale') is True:
        return True
    stamp = value.get('fetched_at') or value.get('collected_at')
    if stamp is None:
        return False
    try:
        age = (now - datetime.fromisoformat(stamp.replace('Z', '+00:00'))).total_seconds()
        return age > 300 or age < -60
    except (ValueError, TypeError, AttributeError):
        return True


def _manual(options):
    result, seen = [], set()
    for item in options[:20] if isinstance(options, (list, tuple)) else []:
        if not isinstance(item, dict):
            continue
        plan = _identifier(item.get('plan_id') or item.get('id'))
        bundle = _identifier(item.get('bundle_plan_id', ''))
        network = _identifier(item.get('network_id') or item.get('network', ''))
        network_name = item.get('network_name', '')
        if (not plan or (item.get('bundle_plan_id') and not bundle)
                or ((item.get('network_id') or item.get('network')) and not network)
                or not isinstance(network_name, str) or len(network_name) > 100
                or any(ord(char) < 32 for char in network_name)):
            continue
        identity = (plan, bundle, network, network_name)
        if identity in seen:
            continue
        seen.add(identity)
        result.append({'id': plan, 'plan_id': plan, 'bundle_plan_id': bundle, 'network_id': network,
                       'network': network, 'network_name': network_name,
                       'title': str(item.get('title') or plan)[:100],
                       'cost': str(item.get('cost') or 'هزینه نامشخص')[:300],
                       'origin': 'manual', 'available': None,
                       'price': {'amount': None, 'currency': 'TOMAN', 'period': 'month', 'kind': 'unknown'},
                       'capacity': {'memory_gb': None, 'cpu_cores': None, 'disk_gb': None},
                       'limitations': ['این گزینه را اپراتور ثبت کرده؛ قیمت، ظرفیت و موجودی فعلی از API تأیید نشده است.']})
    return result


def catalog(cloud, manual_options=()):
    """Read the current account/team catalog, preserving unknown and denied data.

    ``cloud.hosting_catalog()`` returns account (/me), billing (/billing), and
    networks (/networks), either raw JSON or {available, data} envelopes.
    The result deliberately contains no raw account or billing profile.
    """
    now = datetime.now(timezone.utc)
    result = {'available': False, 'origin': 'unavailable', 'fetched_at': now.isoformat(),
              'options': [], 'networks': [], 'networks_available': False,
              'balance': {'amount': None, 'currency': 'TOMAN',
              'scope': 'team' if getattr(cloud, 'team_id', '') else 'account'}, 'limitations': []}
    try:
        raw = cloud.hosting_catalog() if getattr(cloud, 'connected', True) and hasattr(cloud, 'hosting_catalog') else {}
    except (LiaraError, httpx.HTTPError, OSError, ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    account, account_ok = _section(raw.get('account'))
    billing, billing_ok = _section(raw.get('billing'))
    networks, networks_ok = _section(raw.get('networks'))
    account = account if isinstance(account, dict) else {}
    billing = billing if isinstance(billing, dict) else {}
    networks = networks if isinstance(networks, dict) else {}
    stale = _stale(raw, now) or _stale(raw.get('account'), now)
    plans = account.get('plans', {})
    projects = plans.get('projects') if isinstance(plans, dict) else None
    bundles = plans.get('projectBundlePlans', {}) if isinstance(plans, dict) else {}
    if account_ok and isinstance(projects, dict) and not stale:
        result.update(available=True, origin='liara-api')
        for plan_id, plan in list(projects.items())[:200]:
            if not _identifier(plan_id) or not isinstance(plan, dict):
                continue
            choices = bundles.get(plan_id, {}) if isinstance(bundles, dict) else {}
            if not isinstance(choices, dict) or not choices:
                choices = {'': {}}
            for bundle_id, bundle in list(choices.items())[:30]:
                if (bundle_id and not _identifier(bundle_id)) or not isinstance(bundle, dict):
                    continue
                hardware = _number(plan.get('price'))
                bundle_price = _number(bundle.get('displayPrice'))
                hardware_monthly = hardware * 720 if hardware is not None else None
                plan_available, bundle_available = plan.get('available'), bundle.get('available')
                available = (False if plan_available is False or bundle_available is False else
                             True if plan_available is True and bundle_available is True and bundle_id else None)
                capacity = {key: _amount(_number((plan.get(field) or {}).get('amount'), positive=True))
                            if isinstance(plan.get(field), dict) else None
                            for key, field in [('memory_gb', 'RAM'), ('cpu_cores', 'CPU')]}
                capacity['disk_gb'] = _amount(_number(plan.get('volume')))
                limitations = ['تناسب ظرفیت با بار واقعی سایت هنوز اندازه‌گیری نشده است.']
                if available is None:
                    limitations.append('موجودی پلن یا بستهٔ امکانات نامشخص است.')
                limitations.append('قاعدهٔ جمع هزینهٔ منابع و بسته در قرارداد بررسی‌شده مشخص نیست؛ مبلغ نهایی نامشخص است.')
                costs = []
                if hardware_monthly is not None:
                    costs.append(f'منابع: حدود {_amount(hardware_monthly)} تومان در ماه (۷۲۰ ساعت)')
                if bundle_price is not None:
                    costs.append(f'بستهٔ امکانات: {_amount(bundle_price)} تومان در ماه طبق API')
                result['options'].append({
                    'id': plan_id, 'plan_id': plan_id, 'bundle_plan_id': bundle_id, 'network_id': '',
                    'title': plan_id + (' / ' + bundle_id if bundle_id else ''),
                    'origin': 'liara-api', 'available': available, 'capacity': capacity,
                    'region': str(plan['region'])[:100] if isinstance(plan.get('region'), str) else None,
                    'price': {'amount': None, 'currency': 'TOMAN', 'period': 'month',
                              'kind': 'components' if costs else 'unknown',
                              'hardware_hourly': _amount(hardware), 'hours': 720,
                              'hardware_estimated_monthly': _amount(hardware_monthly),
                              'bundle_reported_monthly': _amount(bundle_price)},
                    'cost': '؛ '.join([*costs, 'مبلغ نهایی نامشخص است']),
                    'limitations': limitations})
    if stale:
        result['limitations'].append('اطلاعات کاتالوگ قدیمی یا زمان دریافت نامعتبر است؛ موجودی فعلی تأیید نشد.')
    if not result['available']:
        result['options'] = _manual(manual_options)
        result['origin'] = 'manual' if result['options'] else 'unavailable'
        result['limitations'].append('کاتالوگ زنده در دسترس نیست؛ گزینهٔ دستی اپراتور مسیر جایگزین است و باید دوباره تأیید شود.')
    elif not result['options']:
        result['limitations'].append('API گزینهٔ قابل شناسایی برای پلن و بستهٔ امکانات برنگرداند.')
    if networks_ok and not _stale(raw, now) and not _stale(raw.get('networks'), now):
        seen = set()
        entries = networks.get('networks')
        result['networks_available'] = isinstance(entries, list)
        for item in entries[:100] if isinstance(entries, list) else []:
            if not isinstance(item, dict) or not _identifier(item.get('_id')):
                result['networks_available'] = False
                continue
            if item['_id'] in seen:
                continue
            seen.add(item['_id'])
            result['networks'].append({'id': item['_id'], 'name': str(item.get('name') or item['_id'])[:100]})
    if not result['networks_available']:
        result['limitations'].append('فهرست شبکه‌های حساب قابل دریافت نیست؛ شبکه‌ای حدس زده نمی‌شود.')
    if result['balance']['scope'] == 'team':
        team = account.get('team', {})
        balance = _number(team.get('balance')) if account_ok and isinstance(team, dict) and not stale else None
    else:
        user = billing.get('user', {})
        balance = (_number(user.get('balance')) if billing_ok and isinstance(user, dict)
                   and not _stale(raw, now) and not _stale(raw.get('billing'), now) else None)
    result['balance']['amount'] = _amount(balance)
    if balance is None:
        result['limitations'].append('موجودی مالی حساب یا تیم نامشخص است؛ توان پرداخت تأیید نشده است.')
    return result


def recommend(current_catalog, inspection):
    """Order explicit choices; source type alone never establishes RAM needs."""
    options = copy.deepcopy(current_catalog.get('options', []))
    balance = _number(current_catalog.get('balance', {}).get('amount'))
    for item in options:
        price = _number(item.get('price', {}).get('amount'))
        item['affordability'] = ('unknown' if balance is None or price is None else
                                 'estimated_month_covered' if balance >= price else 'estimated_month_not_covered')
        item['recommended'] = False
        item['comparison_basis'] = 'components_only' if item.get('price', {}).get('kind') == 'components' else 'unknown'
    options.sort(key=lambda item: (item.get('available') is not True,
                                  _number(item.get('price', {}).get('hardware_estimated_monthly')) is None,
                                  _number(item.get('price', {}).get('hardware_estimated_monthly')) or Decimal(0),
                                  _number(item.get('price', {}).get('bundle_reported_monthly')) is None,
                                  _number(item.get('price', {}).get('bundle_reported_monthly')) or Decimal(0),
                                  item.get('plan_id', ''), item.get('bundle_plan_id', '')))
    for index, item in enumerate(options):
        if item.get('available') is False:
            item['reason'] = 'این پلن یا بستهٔ امکانات طبق پاسخ حساب فعلاً در دسترس نیست.'
        elif item.get('origin') == 'manual':
            item['reason'] = 'گزینهٔ ثبت‌شدهٔ اپراتور است؛ هزینه و موجودی فعلی نیاز به تأیید دارد.'
        elif item.get('available') is not True:
            item['reason'] = 'موجودی یا بستهٔ امکانات مشخص نیست؛ انتخاب قطعی ممکن نیست.'
        elif (index == 0 and item.get('price', {}).get('hardware_estimated_monthly') is not None
              and item.get('price', {}).get('bundle_reported_monthly') is not None):
            item['recommended'] = True
            kind = 'سایت استاتیک' if inspection.get('platform') == 'static' else 'این سایت'
            item['reason'] = f'برای شروع {kind}، گزینهٔ موجود با کمترین هزینهٔ منابع و سپس بسته در فهرست است؛ مبلغ نهایی نامشخص و ظرفیت نیازمند سنجش مصرف واقعی است.'
        else:
            item['reason'] = 'هزینه‌های منابع و بسته جداگانه قابل مقایسه‌اند؛ مبلغ نهایی و تناسب ظرفیت هنوز تأیید نشده است.'
    return options
