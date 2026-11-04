"""Regressions for problems found in the critical review (no network needed)."""

import tempfile
import unittest
from pathlib import Path

from moviemap import store
from moviemap.matcher import Candidate, form_sim, score_candidate, user_forms
from moviemap.names import parse
from moviemap.textnorm import fold


class ReReleases(unittest.TestCase):
    def test_a_late_re_release_does_not_pass_for_the_files_year(self):
        # "[2025] Avatar: Fire and Ash" must not be satisfied by Avatar (2009) re-released in 2025
        p = parse("[2025] Avatar꞉ Fire and Ash.mkv")
        c = Candidate(id=19995, title="Avatar", original_title="Avatar", language="en", year=2009, votes=30000, popularity=1)
        c.record = {"alt_titles": [], "release_years": [2009, 2010, 2022, 2025], "spoken_languages": ["en"]}
        score_candidate(p, c, user_forms(p))
        self.assertLess(c.Y, 0.5)
        self.assertLess(c.T, 0.86)  # the file's title extends TMDB's: not a title match on its own

    def test_a_festival_premiere_still_counts(self):
        p = parse("[2022] Hundreds of Beavers.mkv")
        c = Candidate(id=1, title="Hundreds of Beavers", original_title="Hundreds of Beavers", language="en",
                      year=2024, votes=300, popularity=1)
        c.record = {"alt_titles": [], "release_years": [2022, 2023, 2024], "spoken_languages": ["en"]}
        score_candidate(p, c, user_forms(p))
        self.assertGreaterEqual(c.Y, 0.97)

    def test_subtitle_directions(self):
        self.assertGreaterEqual(form_sim("Glass Onion", "Glass Onion: A Knives Out Mystery"), 0.86)
        self.assertLess(form_sim("Avatar: Fire and Ash", "Avatar"), 0.86)


class OddNames(unittest.TestCase):
    def test_non_decimal_digits_do_not_crash(self):
        p = parse("[2015] Lamb (Amharic; በግ ፩, Lamb).mkv")
        self.assertTrue(user_forms(p))
        self.assertIn("፩", fold("በግ ፩"))

    def test_markers_after_the_language_tag(self):
        for name in ("[2016] Elle (French) (1).mkv", "[2016] Elle (French) - Copy.mkv"):
            p = parse(name)
            self.assertEqual((p.title, p.langs), ("Elle", ["French"]), name)
        p = parse("[2001] Spirited Away (Japanese; 千と千尋の神隠し, Sen to Chihiro no Kamikakushi) [1080p].mkv")
        self.assertEqual((p.title, p.natives, p.edition), ("Spirited Away", ["千と千尋の神隠し"], "1080p"))


class CorrectionsFile(unittest.TestCase):
    def test_titles_starting_with_a_hash(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "corrections.txt"
            path.write_text("# a comment\n#note without a space\n#Alive.mkv = 614696\n", encoding="utf-8")
            decisions, problems = store.read_corrections(path)
            self.assertEqual(problems, [])
            self.assertEqual(store.find_decision(decisions, "#Alive.mkv").value, 614696)

    def test_notepad_unicode_encoding(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "corrections.txt"
            path.write_bytes("[2016] Elle (French).mkv = 337674\r\n".encode("utf-16"))
            decisions, problems = store.read_corrections(path)
            self.assertEqual(problems, [])
            self.assertEqual(store.find_decision(decisions, "[2016] Elle (French).mkv").value, 337674)


class Listing(unittest.TestCase):
    def test_a_cut_short_listing_changes_nothing_unless_confirmed(self):
        from moviemap import sync as sync_module
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / "matches.json"
            names = [f"[2000] Film {i}.mkv" for i in range(40)]
            store_path, store.MATCHES_PATH = store.MATCHES_PATH, ledger
            try:
                store.save_matches({"files": {n: {"status": "skipped"} for n in names}})
                before = ledger.read_bytes()
                for confirm in (None, lambda missing: False):  # unattended, or answered "no"
                    rep = sync_module.sync(names[:10], tmdb=None, log=lambda *a: None, confirm_removal=confirm)
                    self.assertIn("30 files", rep.stopped or "")
                    self.assertEqual(ledger.read_bytes(), before)
            finally:
                store.MATCHES_PATH = store_path

    def test_unquoted_commas_in_a_one_column_listing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "files.csv"
            path.write_bytes("﻿Name\r\n[1979] Stalker (Russian; Сталкер, Stalker).mkv\r\n".encode("utf-8"))
            self.assertEqual(store.read_collection(path), ["[1979] Stalker (Russian; Сталкер, Stalker).mkv"])


if __name__ == "__main__":
    unittest.main()
