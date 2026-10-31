import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy'))
from secret_config import prepare_secrets, raw_env, cleanup_secrets


BASE = 'BOT_TOKEN=123:test\nOPENAI_API_KEY=test\nDATABASE_URL=postgresql+psycopg2://whisper:p%24%23%22@db:5432/whisper_db\n'


class SecretConfigTests(unittest.TestCase):
    def test_cleanup_service_does_not_receive_provider_credentials(self):
        app, _, _, _ = prepare_secrets(BASE + 'CLEANUP_RETRY_SECONDS=7\n')
        cleanup = cleanup_secrets(app)
        self.assertNotIn('BOT_TOKEN', cleanup)
        self.assertNotIn('OPENAI_API_KEY', cleanup)
        self.assertEqual(cleanup['DATABASE_URL'], app['DATABASE_URL'])
        self.assertEqual(cleanup['CLEANUP_RETRY_SECONDS'], '7')

    def test_literal_values_and_database_credentials(self):
        app, database, telegram, local = prepare_secrets(BASE + "CUSTOM='${HOME} $(touch /tmp/never) # value'\n")
        self.assertEqual(app['CUSTOM'], '${HOME} $(touch /tmp/never) # value')
        self.assertIn('POSTGRES_PASSWORD=p$#"\n', raw_env(database))
        self.assertEqual(app['MEDIA_DOWNLOADS_DIR'], '/code/downloads')
        self.assertFalse(local)

    def test_wrong_database_host_rejected(self):
        with self.assertRaises(ValueError):
            prepare_secrets(BASE.replace('@db:', '@localhost:'))

    def test_injected_database_newline_rejected(self):
        with self.assertRaises(ValueError):
            prepare_secrets(BASE.replace('p%24%23%22', 'p%0aPOSTGRES_HOST_AUTH_METHOD=trust'))

    def test_multiline_value_rejected(self):
        with self.assertRaises(ValueError):
            prepare_secrets(BASE + 'CUSTOM="one\ntwo"\n')

    def test_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            prepare_secrets(BASE.replace('OPENAI_API_KEY=test', 'OPENAI_API_KEY='))

    def test_local_api_credentials_and_endpoint(self):
        local = BASE + 'MEDIA_TELEGRAM_API_BASE=http://telegram-api:8081\n'
        with self.assertRaises(ValueError):
            prepare_secrets(local)
        _, _, telegram, enabled = prepare_secrets(local + 'TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=test\n')
        self.assertTrue(enabled)
        self.assertEqual(telegram['TELEGRAM_API_ID'], '123')
