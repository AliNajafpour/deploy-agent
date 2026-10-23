import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPChecks(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_handshake_and_proposal_never_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()
            (source / 'package.json').write_text('{"scripts":{"start":"node server.js --password=hunter2"}}')
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'lyara', '--demo', 'mcp'],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, 'LYARA_DATA_DIR': str(Path(tmp) / 'state'), 'LYARA_WORKSPACE': str(source)})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    names = {tool.name for tool in result.tools}
                    self.assertTrue({'inspect_source', 'collect_evidence', 'propose', 'operation_status'} <= names)
                    self.assertNotIn('approve', names)
                    self.assertNotIn('execute', names)
                    inspection = await session.call_tool('inspect_source')
                    self.assertNotIn('hunter2', inspection.content[0].text)
                    projects = await session.call_tool('list_projects')
                    self.assertFalse(projects.isError)
                    self.assertIn('demo-app', projects.content[0].text)
                    proposal = await session.call_tool('propose', {'request': {
                        'message': 'Restart for review', 'project': 'demo-app', 'intent': 'operate'},
                        'plan': {'summary': 'Proposed restart', 'steps': [
                            {'action': 'restart', 'title': 'Restart', 'reason': 'Requested by user', 'args': {}}]}})
                    self.assertFalse(proposal.isError)
                    self.assertIn('awaiting_approval', proposal.content[0].text)


if __name__ == '__main__':
    unittest.main()
