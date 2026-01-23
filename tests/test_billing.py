import os
import importlib
import unittest
from datetime import datetime, timedelta


class BillingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["LIMIT_TRIAL_MIN"] = "3"
        os.environ["LIMIT_PLUS_MIN"] = "100"
        os.environ["LIMIT_PRO_MIN"] = "2000"
        import app.config
        importlib.reload(app.config)
        import app.services.billing
        importlib.reload(app.services.billing)
        cls.billing = app.services.billing

    def test_add_one_month_clamps_end_of_month(self):
        dt = datetime(2026, 1, 31, 12, 0, 0)
        out = self.billing.add_one_month(dt)
        self.assertEqual(out.year, 2026)
        self.assertEqual(out.month, 2)
        self.assertEqual(out.day, 28)

    def test_days_left_none(self):
        self.assertIsNone(self.billing.days_left(None))

    def test_expiry_warning_text(self):
        paid_until = datetime.utcnow() + timedelta(days=2, hours=1)
        text = self.billing.expiry_warning_text("plus", paid_until)
        self.assertIn("day(s) left", text)

    def test_get_plan_limit_seconds(self):
        class Dummy:
            value = "trial"
        self.assertEqual(self.billing.get_plan_limit_seconds(Dummy()), 180)


if __name__ == "__main__":
    unittest.main()
