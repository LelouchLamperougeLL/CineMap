"""Title comparison and the corrections file (no network needed)."""

import tempfile
import unittest
from pathlib import Path

from moviemap import store
from moviemap.matcher import form_sim, title_sim
from moviemap.textnorm import fold


class Titles(unittest.TestCase):
    def test_fold(self):
        self.assertEqual(fold("Mission꞉ Impossible – Ghost Protocol"), "mission impossible ghost protocol")
        self.assertEqual(fold("Face/Off"), fold("Face-Off"))
        self.assertEqual(fold("Dune: Part One"), "dune part 1")
        self.assertEqual(fold("Infernal Affairs II"), "infernal affairs 2")
        self.assertEqual(fold("TÁR"), fold("Tár"))
        self.assertEqual(fold("Ernest & Celestine"), fold("Ernest and Célestine"))
        self.assertEqual(fold("無間道II"), fold("無間道 II"))

    def test_sequels_are_different_films(self):
        self.assertLess(title_sim(fold("Toy Story"), fold("Toy Story 2")), 0.6)
        self.assertLess(title_sim(fold("Infernal Affairs II"), fold("Infernal Affairs III")), 0.6)
        self.assertLess(form_sim("Dune: Part One", "Dune: Part Two"), 0.6)

    def test_subtitles_need_a_separator(self):
        self.assertGreaterEqual(form_sim("Glass Onion", "Glass Onion: A Knives Out Mystery"), 0.86)
        self.assertGreaterEqual(form_sim("Dìdi", "Dìdi (弟弟)"), 0.86)
        self.assertLess(form_sim("Lincoln", "The Lincoln Lawyer"), 0.86)
        self.assertLess(form_sim("Living", "Living with Chucky"), 0.86)
        self.assertLess(form_sim("Up", "Up in the Air"), 0.86)

    def test_abbreviation(self):
        self.assertGreaterEqual(title_sim(fold("Mulholland Dr."), fold("Mulholland Drive")), 0.86)
        self.assertLess(title_sim(fold("Her"), fold("Hero")), 0.86)


class Corrections(unittest.TestCase):
    def test_decisions(self):
        self.assertEqual(store.parse_decision("337674"), ("tmdb", 337674))
        self.assertEqual(store.parse_decision("https://www.themoviedb.org/movie/337674-elle"), ("tmdb", 337674))
        self.assertEqual(store.parse_decision("https://www.imdb.com/title/tt3716530/"), ("imdb", "tt3716530"))
        self.assertEqual(store.parse_decision("skip"), ("skip", None))
        self.assertIsNone(store.parse_decision("maybe"))

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "corrections.txt"
            store.set_correction("[2016] Elle (French).mkv", "337674", "Elle (2016)", path=path)
            store.set_correction("A = B.mkv", "skip", path=path)   # '=' inside a file name
            store.set_correction("[2016] Elle (French).mkv", "12345", path=path)  # replaces, never duplicates
            decisions, problems = store.read_corrections(path)
            self.assertEqual(problems, [])
            self.assertEqual(store.find_decision(decisions, "[2016] Elle (French).mkv").value, 12345)
            self.assertEqual(store.find_decision(decisions, "[2016] Elle (French).mp4").value, 12345)  # extension-free key
            self.assertEqual(store.find_decision(decisions, "A = B.mkv").kind, "skip")
            self.assertTrue(store.remove_correction("[2016] Elle (French).mkv", path=path))
            decisions, _ = store.read_corrections(path)
            self.assertIsNone(store.find_decision(decisions, "[2016] Elle (French).mkv"))


if __name__ == "__main__":
    unittest.main()
