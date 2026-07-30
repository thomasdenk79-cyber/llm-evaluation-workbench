import unittest

from src.retry_policy import should_retry


class RetryPolicyTests(unittest.TestCase):
    def test_retries_transient_errors_before_limit(self) -> None:
        self.assertTrue(should_retry(TimeoutError(), 0))
        self.assertTrue(should_retry(ConnectionError(), 2))

    def test_never_retries_permission_errors(self) -> None:
        self.assertFalse(should_retry(PermissionError(), 0))

    def test_stops_after_three_attempts(self) -> None:
        self.assertFalse(should_retry(TimeoutError(), 3))


if __name__ == "__main__":
    unittest.main()
