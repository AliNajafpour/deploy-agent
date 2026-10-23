import unittest
from unittest.mock import patch

from lyara.liara import LiaraClient,LiaraError


class BuildCursorChecks(unittest.TestCase):
    def test_paged_logs_reach_failure_and_keep_only_bounded_tail(self):
        rows=[{'createdAt':f'2026-10-23T12:{i//60:02d}:{i%60:02d}Z',
               'stream':'stderr','line':'missing dependency' if i==119 else f'build {i}'} for i in range(120)]
        cursors=[];progress=[]
        def page(release_id,since=None,timeout=12):
            cursors.append(since)
            return {'buildOutput':[r for r in rows if since is None or r['createdAt']>since][:20]}
        with LiaraClient('test-token') as cloud:
            with patch.object(cloud,'releases',side_effect=[
                {'releases':[{'_id':'release','state':'BUILDING'}]},
                {'releases':[{'_id':'release','state':'FAILED'}]}]), \
                 patch.object(cloud,'release_status',return_value={'release':{}}), \
                 patch.object(cloud,'build_logs',side_effect=page):
                with self.assertRaises(LiaraError):
                    cloud.wait_release('site','release',interval=0,progress=progress.append)
        self.assertEqual(cursors[0],None)
        self.assertEqual(cursors[1],rows[19]['createdAt'])
        tail=progress[-1]['build']['buildOutput']
        self.assertEqual(len(tail),100)
        self.assertEqual(tail[-1]['line'],'missing dependency')
        self.assertEqual(len(cursors),6)


if __name__=='__main__':unittest.main()
