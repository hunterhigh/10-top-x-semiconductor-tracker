import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("extract_script", ROOT / "scripts" / "extract.py")
extract_script = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(extract_script)


class AdaptiveChunkTests(unittest.TestCase):
    def test_failed_batch_is_bisected_without_reordering(self):
        calls = []

        def extractor(tweets):
            ids = [tweet["tweet_id"] for tweet in tweets]
            calls.append(ids)
            if len(tweets) > 2:
                return None
            return [{"tweet_id": tweet_id} for tweet_id in ids]

        tweets = [{"tweet_id": str(i)} for i in range(8)]
        leaves = extract_script.extract_with_adaptive_chunks(tweets, extractor)

        self.assertEqual([len(batch) for batch, _ in leaves], [2, 2, 2, 2])
        self.assertEqual(
            [tweet["tweet_id"] for batch, _ in leaves for tweet in batch],
            [str(i) for i in range(8)],
        )
        self.assertEqual([len(call) for call in calls], [8, 4, 2, 2, 4, 2, 2])

    def test_failed_singleton_is_returned_for_resumable_error_record(self):
        tweet = {"tweet_id": "1"}
        leaves = extract_script.extract_with_adaptive_chunks([tweet], lambda _: None)
        self.assertEqual(leaves, [([tweet], None)])


if __name__ == "__main__":
    unittest.main()
