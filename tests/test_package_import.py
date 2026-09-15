import unittest
from importlib.metadata import distribution

import pytest


@pytest.mark.integration
class PackageImportSmokeTest(unittest.TestCase):
    def test_installed_distribution_exposes_import_package(self) -> None:
        installed_distribution = distribution("solar-platform-backend")

        import solar_platform

        self.assertEqual(
            installed_distribution.metadata["Name"],
            "solar-platform-backend",
        )
        self.assertTrue(installed_distribution.version)
        self.assertEqual(solar_platform.__name__, "solar_platform")


if __name__ == "__main__":
    unittest.main()
