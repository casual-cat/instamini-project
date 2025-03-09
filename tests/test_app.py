import unittest
import os
import app

class TestApp(unittest.TestCase):
    def test_environment_vars(self):
        # Example test: check if DB vars exist
        self.assertIsNotNone(os.environ.get("MYSQL_HOST"))
        self.assertIsNotNone(os.environ.get("MYSQL_USER"))
        self.assertIsNotNone(os.environ.get("MYSQL_PASS"))
        self.assertIsNotNone(os.environ.get("MYSQL_DB"))

    def test_offensive_filter(self):
        # Example: test the censor_offensive function
        text = "This is a badword"
        censored = app.censor_offensive(text)
        # If "badword" was in your bad_words.txt, it should be replaced with "****"
        self.assertIn("****", censored)

if __name__ == "__main__":
    unittest.main()
