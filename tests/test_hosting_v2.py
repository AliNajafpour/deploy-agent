import copy
import unittest
from datetime import datetime, timedelta, timezone

from lyara.hosting import catalog, recommend
from lyara.liara import LiaraError


def fixture():
    # Official CLI contract, not a claim that these example IDs are live plans.
    return {'account': {'plans': {
        'projects': {'test-small': {'available': True, 'region': 'iran',
                    'RAM': {'amount': 0.5}, 'CPU': {'amount': 0.25}, 'volume': 5, 'price': 10},
                     'test-large': {'available': True, 'region': 'germany',
                    'RAM': {'amount': 2}, 'CPU': {'amount': 1}, 'volume': 20, 'price': 20}},
        'projectBundlePlans': {'test-small': {'test-bronze': {'available': True, 'displayPrice': 1000},
                                             'test-gold': {'available': False, 'displayPrice': 3000}},
                               'test-large': {'test-standard': {'available': True, 'displayPrice': 2000}}}}},
        'billing': {'available': True, 'data': {'user': {'balance': 10000, 'email': 'private@example.com'}}},
        'networks': {'networks': [{'_id': 'network-a', 'name': 'private'}, {'_id': 'network-b', 'name': 'private'}]}}


class Cloud:
    connected = True
    team_id = ''

    def __init__(self, data=None):
        self.data = fixture() if data is None else data
        self.calls = 0

    def hosting_catalog(self):
        self.calls += 1
        return self.data


class HostingTests(unittest.TestCase):
    def test_official_plan_bundle_network_and_exact_price_basis(self):
        result = catalog(Cloud())
        self.assertTrue(result['available'])
        self.assertEqual(result['origin'], 'liara-api')
        self.assertEqual(len(result['options']), 3)
        small = result['options'][0]
        self.assertEqual((small['plan_id'], small['bundle_plan_id']), ('test-small', 'test-bronze'))
        self.assertEqual(small['price'], {'amount': None, 'currency': 'TOMAN', 'period': 'month',
                         'kind': 'components', 'hardware_hourly': '10', 'hours': 720,
                         'hardware_estimated_monthly': '7200',
                         'bundle_reported_monthly': '1000'})
        self.assertNotIn('8200', small['cost'])
        self.assertIn('7200', small['cost'])
        self.assertIn('1000', small['cost'])
        self.assertIn('نامشخص', small['cost'])
        self.assertEqual(small['capacity'], {'memory_gb': '0.5', 'cpu_cores': '0.25', 'disk_gb': '5'})
        self.assertFalse(result['options'][1]['available'])
        self.assertEqual([item['id'] for item in result['networks']], ['network-a', 'network-b'])
        self.assertTrue(result['networks_available'])
        self.assertEqual(result['balance']['amount'], '10000')
        self.assertNotIn('private@example.com', str(result))
        self.assertEqual(result['options'][2]['region'], 'germany')  # no CLI's arbitrary region/generation filter

    def test_billing_and_network_denied_do_not_hide_catalog_or_invent_zero(self):
        raw = fixture()
        raw.update(billing={'available': False, 'error': 'denied'}, networks={'available': False})
        result = catalog(Cloud(raw))
        self.assertTrue(result['available'])
        self.assertIsNone(result['balance']['amount'])
        self.assertEqual(result['networks'], [])
        self.assertFalse(result['networks_available'])
        self.assertGreaterEqual(len(result['limitations']), 2)

    def test_team_balance_never_uses_personal_balance(self):
        cloud = Cloud()
        cloud.team_id = 'team-private'
        self.assertIsNone(catalog(cloud)['balance']['amount'])
        cloud.data['account']['team'] = {'balance': 0}
        result = catalog(cloud)
        self.assertEqual(result['balance'], {'scope': 'team', 'amount': '0', 'currency': 'TOMAN'})
        cloud.data['account'] = {'available': False, 'data': cloud.data['account']}
        self.assertIsNone(catalog(cloud)['balance']['amount'])

    def test_partial_capacity_price_and_availability_stay_unknown(self):
        raw = fixture()
        raw['account']['plans']['projects']['test-small'] = {'available': True, 'CPU': {'amount': float('nan')}}
        del raw['account']['plans']['projectBundlePlans']['test-small']['test-bronze']['available']
        result = catalog(Cloud(raw))
        small = result['options'][0]
        self.assertIsNone(small['available'])
        self.assertIsNone(small['price']['amount'])
        self.assertEqual(small['price']['kind'], 'components')
        self.assertEqual(small['capacity'], dict.fromkeys(['memory_gb', 'cpu_cores', 'disk_gb']))
        del raw['account']['plans']['projectBundlePlans']['test-small']
        small = catalog(Cloud(raw))['options'][0]
        self.assertEqual(small['bundle_plan_id'], '')
        self.assertIsNone(small['available'])

    def test_zero_money_valid_negative_nonfinite_and_bool_unknown(self):
        raw = fixture()
        raw['account']['plans']['projects']['test-small']['price'] = 0
        raw['account']['plans']['projectBundlePlans']['test-small']['test-bronze']['displayPrice'] = 0
        raw['billing']['data']['user']['balance'] = 0
        result = catalog(Cloud(raw))
        self.assertIsNone(result['options'][0]['price']['amount'])
        self.assertEqual(result['options'][0]['price']['hardware_estimated_monthly'], '0')
        self.assertEqual(result['options'][0]['price']['bundle_reported_monthly'], '0')
        self.assertEqual(result['balance']['amount'], '0')
        for value in [-1, 'NaN', float('inf'), True, None, [], 'not-money', '1e99999999', '1e-9999999']:
            with self.subTest(value=value):
                raw['account']['plans']['projects']['test-small']['price'] = value
                raw['billing']['data']['user']['balance'] = value
                result = catalog(Cloud(raw))
                self.assertIsNone(result['options'][0]['price']['amount'])
                self.assertIsNone(result['options'][0]['price']['hardware_estimated_monthly'])
                self.assertIsNone(result['balance']['amount'])

    def test_disconnected_exception_and_stale_use_explicit_manual_fallback(self):
        manual = [{'id': 'operator-plan', 'title': 'Operator option', 'cost': 'Confirmed separately'}]
        cloud = Cloud()
        cloud.connected = False
        result = catalog(cloud, manual)
        self.assertEqual(cloud.calls, 0)
        self.assertFalse(result['available'])
        self.assertEqual(result['origin'], 'manual')
        self.assertIsNone(result['options'][0]['available'])
        self.assertIsNone(result['options'][0]['price']['amount'])
        self.assertTrue(result['options'][0]['limitations'])
        cloud.connected = True
        cloud.data['fetched_at'] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        result = catalog(cloud, manual)
        self.assertEqual(result['origin'], 'manual')
        self.assertIsNone(result['balance']['amount'])
        self.assertEqual(result['networks'], [])
        class Denied(Cloud):
            def hosting_catalog(self):
                raise LiaraError(403, 'do not expose raw credential-error')
        result = catalog(Denied(), manual)
        self.assertEqual(result['origin'], 'manual')
        self.assertNotIn('credential-error', str(result))

    def test_malformed_sections_and_duplicates_do_not_create_fake_choices(self):
        for raw in [None, [], {'account': {'plans': []}}, {'account': {'available': True, 'data': []}},
                    {'account': {'plans': {'projects': []}}}]:
            cloud = Cloud()
            cloud.data = raw
            self.assertEqual(catalog(cloud)['options'], [])
        raw = fixture()
        raw['networks']['networks'] += [{'_id': 'network-a', 'name': 'duplicate'}, {'_id': '../invalid'}, None]
        raw['account']['plans']['projects']['../invalid'] = {'available': True}
        result = catalog(Cloud(raw))
        self.assertEqual(len(result['networks']), 2)
        self.assertEqual(len(result['options']), 3)
        fallback = catalog(Cloud([]), [{'id': 'operator', 'title': 'x', 'cost': 'y'},
                                       {'id': 'operator', 'title': 'duplicate', 'cost': 'y'},
                                       {'id': 'operator', 'bundle_plan_id': 'other-bundle', 'cost': 'y'}])
        self.assertEqual(len(fallback['options']), 2)

    def test_recommendation_uses_known_available_cost_not_id_or_invented_fit(self):
        result = catalog(Cloud())
        original = copy.deepcopy(result)
        ordered = recommend(result, {'platform': 'static'})
        self.assertEqual(ordered[0]['bundle_plan_id'], 'test-bronze')
        self.assertTrue(ordered[0]['recommended'])
        self.assertIn('استاتیک', ordered[0]['reason'])
        self.assertIn('مصرف واقعی', ordered[0]['reason'])
        self.assertEqual(ordered[0]['affordability'], 'unknown')
        self.assertEqual(ordered[1]['affordability'], 'unknown')
        self.assertEqual(ordered[0]['comparison_basis'], 'components_only')
        self.assertFalse(ordered[-1]['available'])
        self.assertEqual(result, original)
        result['balance']['amount'] = None
        self.assertEqual(recommend(result, {})[0]['affordability'], 'unknown')
        raw = fixture()
        for plan in raw['account']['plans']['projects'].values():
            plan.pop('price')
        self.assertFalse(any(item['recommended'] for item in recommend(catalog(Cloud(raw)), {})))

    def test_network_empty_is_known_only_for_fresh_successful_list(self):
        raw = fixture()
        raw['networks'] = {'available': True, 'data': {'networks': []}}
        self.assertTrue(catalog(Cloud(raw))['networks_available'])
        for value in [{}, {'available': False}, {'available': True, 'data': {}},
                      {'available': True, 'data': {'networks': None}},
                      {'available': True, 'stale': True, 'data': {'networks': []}},
                      {'available': True, 'data': {'networks': [{'_id': '../bad'}]}}]:
            raw['networks'] = value
            self.assertFalse(catalog(Cloud(raw))['networks_available'])
        cloud = Cloud([])
        result = catalog(cloud, [{'id': 'operator', 'network_name': {'bad': 'type'}}])
        self.assertEqual(result['options'], [])


if __name__ == '__main__':
    unittest.main()
