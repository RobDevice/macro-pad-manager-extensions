import unittest
from unittest.mock import Mock

from n4driverext.n4_driver_support import N4DriverSupport


class N4DriverSupportTests(unittest.TestCase):
    def test_clear_all_visuals_blanks_all_button_and_touch_surfaces(self):
        driver = N4DriverSupport()
        driver._session.snapshot = Mock(return_value=Mock(connected=True))
        driver._session.upload_button_image = Mock()

        driver.clear_all_visuals()

        calls = driver._session.upload_button_image.call_args_list
        self.assertEqual(len(calls), 14)
        addresses = [call.args[0] for call in calls]
        self.assertEqual(addresses[:10], list(range(1, 11)))
        self.assertEqual(addresses[10:], [0x40, 0x41, 0x42, 0x43])


if __name__ == "__main__":
    unittest.main()
