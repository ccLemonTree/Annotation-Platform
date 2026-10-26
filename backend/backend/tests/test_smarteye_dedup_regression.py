import unittest

import imagehash

from integrations.smarteye.api.image_dedup import ImageDeduplicator


class _HashOnlyDeduplicator(ImageDeduplicator):
    def __init__(self, values, threshold=10):
        super().__init__(threshold=threshold)
        self.values = values

    def compute_hash(self, img_path):
        return imagehash.hex_to_hash(f"{self.values[img_path]:016x}")


class ImageDeduplicatorTests(unittest.TestCase):
    def test_unique_count_includes_one_retained_image_per_duplicate_group(self):
        deduplicator = _HashOnlyDeduplicator({'a.jpg': 0, 'b.jpg': 0, 'c.jpg': 15}, threshold=0)
        groups, unique = deduplicator.find_duplicates(['a.jpg', 'b.jpg', 'c.jpg'])
        self.assertEqual(groups, [['a.jpg', 'b.jpg'], ['c.jpg']])
        self.assertEqual(unique, 2)

    def test_indexed_grouping_keeps_threshold_semantics(self):
        values = {
            "a.jpg": 0,
            "b.jpg": 1,
            "c.jpg": (1 << 20) - 1,
            "d.jpg": ((1 << 20) - 1) ^ 1,
        }
        deduplicator = _HashOnlyDeduplicator(values, threshold=10)

        groups, _ = deduplicator.find_duplicates(list(values))

        self.assertEqual(groups, [["a.jpg", "b.jpg"], ["c.jpg", "d.jpg"]])

    def test_exact_hash_grouping_does_not_compare_every_pair(self):
        values = {
            f"{idx}.jpg": (idx * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
            for idx in range(2000)
        }
        deduplicator = _HashOnlyDeduplicator(values, threshold=0)

        groups, _ = deduplicator.find_duplicates(list(values))

        self.assertEqual(len(groups), len(values))
        self.assertLess(deduplicator.comparison_count, 200_000)

    def test_threshold_ten_uses_a_bounded_candidate_set(self):
        values = {
            f"{idx}.jpg": (idx * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
            for idx in range(2000)
        }
        deduplicator = _HashOnlyDeduplicator(values, threshold=10)

        deduplicator.find_duplicates(list(values))

        self.assertLess(deduplicator.comparison_count, 500_000)


if __name__ == "__main__":
    unittest.main()
