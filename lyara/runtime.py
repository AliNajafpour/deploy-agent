import os
from pathlib import Path

from dotenv import load_dotenv

from .demo import DemoCloud, DemoPlanner
from .liara import LiaraClient
from .providers import provider_from_env
from .service import Assistant


def create_assistant(demo=False, model=True):
    default_env = Path(__file__).resolve().parents[1] / '.env'
    if not default_env.is_file():
        default_env = Path.cwd() / '.env'
    env_file = Path(os.getenv('LYARA_ENV_FILE', str(default_env))).resolve()
    load_dotenv(env_file)
    base = env_file.parent if env_file.is_file() else Path.cwd()
    root = (base / os.getenv('LYARA_WORKSPACE', '.')).resolve()
    data = (base / os.getenv('LYARA_DATA_DIR', '.lyara')).resolve()
    if demo:
        cloud = DemoCloud().client()
        planner = DemoPlanner()
        data = data / 'demo'
    else:
        cloud = LiaraClient(os.getenv('LIARA_API_TOKEN', ''), os.getenv('LIARA_TEAM_ID', ''))
        planner = provider_from_env() if model else None
    return Assistant(root, data, cloud, planner)
