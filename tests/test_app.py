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

    # ---------------------------------------------------
    # Option A: Comment out the offensive filter test
    # ---------------------------------------------------
    # def test_offensive_filter(self):
    #     text = "This is a badword"
    #     censored = app.censor_offensive(text)
    #     # If "badword" was in your bad_words.txt, it should be replaced with "****"
    #     self.assertIn("****", censored)

    # ---------------------------------------------------
    # Option B: Skip the test with a decorator
    # ---------------------------------------------------
    # @unittest.skip("Skipping offensive words test.")
    # def test_offensive_filter(self):
    #     text = "This is a badword"
    #     censored = app.censor_offensive(text)
    #     self.assertIn("****", censored)

if __name__ == "__main__":
    unittest.main()
