import unittest
from app.ui import messages


class MessageTests(unittest.TestCase):
    def test_format_duration_mm_ss(self):
        self.assertEqual(messages.format_duration(59), "0:59")
        self.assertEqual(messages.format_duration(61), "1:01")

    def test_format_duration_force_hours(self):
        self.assertEqual(messages.format_duration(3661, force_hours=True), "1:01:01")


if __name__ == "__main__":
    unittest.main()
