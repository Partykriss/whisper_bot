import os
import importlib
import unittest
from datetime import datetime, timedelta


class TrialLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["LIMIT_TRIAL_MIN"] = "3"
        import app.config
        importlib.reload(app.config)
        import app.services.billing
        importlib.reload(app.services.billing)
        cls.billing = app.services.billing

    def test_trial_resets_when_expired(self):
        # Simulate expired trial: paid_until in past
        past = datetime.utcnow() - timedelta(days=1)
        self.assertTrue(self.billing.is_period_expired(past))

    def test_trial_not_expired_when_future(self):
        future = datetime.utcnow() + timedelta(days=1)
        self.assertFalse(self.billing.is_period_expired(future))


if __name__ == "__main__":
    unittest.main()
