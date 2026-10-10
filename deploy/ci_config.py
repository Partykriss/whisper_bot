"""Print non-secret Compose validation settings; create only dummy env files."""
import json
from pathlib import Path
import sys


if __name__ == '__main__':
    directory = Path(sys.argv[1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'app.env').write_text('BOT_TOKEN=123456:TEST\nOPENAI_API_KEY=test\nDATABASE_URL=postgresql+psycopg2://test:test@db/test\n')
    (directory / 'db.env').write_text('POSTGRES_USER=test\nPOSTGRES_PASSWORD=test\nPOSTGRES_DB=test\n')
    (directory / 'telegram.env').write_text('TELEGRAM_LOCAL=1\n')
    config = json.loads((Path(__file__).parent / 'config.json').read_text())
    for key, value in config['compose_environment'].items():
        print(f'{key}={value}')
    print('BOT_IMAGE=whisper-ci:test')
    print(f'RUNTIME_DIR={directory}')
