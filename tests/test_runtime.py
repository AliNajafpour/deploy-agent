import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyara.runtime import create_assistant


class RuntimeChecks(unittest.TestCase):
    def test_mcp_and_web_resolve_same_paths_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            envfile = root / '.env'
            envfile.write_text('LYARA_WORKSPACE=.\nLYARA_DATA_DIR=.lyara\n')
            with patch.dict(os.environ, {'LYARA_ENV_FILE': str(envfile), 'LYARA_WORKSPACE': '.', 'LYARA_DATA_DIR': '.lyara'}):
                service = create_assistant(demo=True)
                try:
                    self.assertEqual(service.workspace, root.resolve())
                    self.assertEqual(service.data_dir, (root / '.lyara' / 'demo').resolve())
                finally:
                    service.close()


if __name__ == '__main__':
    unittest.main()
