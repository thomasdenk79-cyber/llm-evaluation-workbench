import unittest

from src.identifiers import normalize_identifier


class IdentifierTests(unittest.TestCase):
    def test_normalizes_external_identifiers(self) -> None:
        self.assertEqual(
            normalize_identifier(" Siemens/DeepSeek V4 Flash "),
            "siemens-deepseek-v4-flash",
        )

    def test_collapses_separators(self) -> None:
        self.assertEqual(normalize_identifier("a /  b__c"), "a-b-c")


if __name__ == "__main__":
    unittest.main()
