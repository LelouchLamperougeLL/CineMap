"""File-name parsing. Run:  .venv\\Scripts\\python -m unittest discover tests"""

import csv
import unittest
from pathlib import Path

from moviemap.names import parse

ROOT = Path(__file__).resolve().parent.parent


class NamingConvention(unittest.TestCase):
    def check(self, name, **expected):
        p = parse(name)
        for key, value in expected.items():
            self.assertEqual(getattr(p, key), value, f"{key} of {name!r}")
        return p

    def test_plain(self):
        self.check("[2014] Whiplash.mkv", title="Whiplash", year=2014, langs=[], is_video=True)

    def test_language_native_and_romanisation(self):
        self.check("[1988] My Neighbor Totoro (Japanese; となりのトトロ, Tonari no Totoro).mkv",
                   title="My Neighbor Totoro", langs=["Japanese"], natives=["となりのトトロ"],
                   romanized=["Tonari no Totoro"], lang_codes=["ja"])

    def test_language_only(self):
        self.check("[2016] Elle (French).mkv", title="Elle", natives=[], lang_codes=["fr"])

    def test_latin_native_title_with_comma_is_not_split(self):
        self.check("[2014] Two Days, One Night (French; Deux jours, une nuit).mkv",
                   natives=["Deux jours, une nuit"], romanized=[])

    def test_native_with_its_own_comma(self):
        self.check("[2022] The Tunnel to Summer, the Exit of Goodbyes (Japanese; 夏へのトンネル、さよならの出口, "
                   "Natsu e no Tonneru, Sayonara no Deguchi).mkv",
                   title="The Tunnel to Summer, the Exit of Goodbyes", natives=["夏へのトンネル、さよならの出口"],
                   romanized=["Natsu e no Tonneru, Sayonara no Deguchi"])

    def test_two_languages(self):
        self.check("[2018] Woman at War (Icelandic; Kona fer í stríð, Ukrainian; «Гірська жінка꞉ на війні»).mkv",
                   langs=["Icelandic", "Ukrainian"], natives=["Kona fer í stríð", "Гірська жінка: на війні"])
        self.check("[2025] It Was Just an Accident (Persian꞉ یک تصادف ساده, Yek tasadof-e sadeh; French꞉ Un simple accident).mkv",
                   langs=["Persian", "French"], romanized=["Yek tasadof-e sadeh"])

    def test_nested_parentheses(self):
        self.check("[2016] In This Corner (and Other Corners) of the World (Japanese; この世界の（さらにいくつもの）片隅に, "
                   "Kono Sekai no (Sara ni Ikutsumono) Katasumi ni).mkv",
                   title="In This Corner (and Other Corners) of the World",
                   romanized=["Kono Sekai no (Sara ni Ikutsumono) Katasumi ni"])

    def test_missing_closing_parenthesis(self):
        p = self.check("[2023] Godzilla Minus One (Japanese; ゴジラ -1.0, Gojira Mainasu Wan.mkv",
                       title="Godzilla Minus One", natives=["ゴジラ -1.0"])
        self.assertTrue(any("closing" in n for n in p.notes))

    def test_parentheses_that_belong_to_the_title(self):
        self.check("[2009] (500) Days of Summer.mkv", title="(500) Days of Summer", langs=[])
        self.check("[2014] BIRDMAN or (The Unexpected Virtue of Ignorance).mkv",
                   title="BIRDMAN or (The Unexpected Virtue of Ignorance)", langs=[])

    def test_lookalike_characters(self):
        self.check("[2007] 3꞉10 to Yuma.mkv", title="3:10 to Yuma")
        self.check("[2018] Can You Ever Forgive Me？.mkv", title="Can You Ever Forgive Me?")
        self.check("[2020] Quo Vadis, Aida？(Bosnian).mkv", title="Quo Vadis, Aida?", langs=["Bosnian"])

    def test_trailing_periods(self):
        self.check("[2001] Mulholland Dr..mkv", title="Mulholland Dr.", ext="mkv")
        self.check("[2020] One Night in Miami....mkv", title="One Night in Miami...")

    def test_year_after_title_and_parts(self):
        self.check("Carlos (2010) - Part 2.mkv", year=2010, title="Carlos - Part 2", part=2, base_title="Carlos")
        self.check("[2021] Dune꞉ Part One.mkv", part=1, base_title="Dune")
        self.check("[2022] Trenque Lauquen꞉ Part II.mkv", part=2, base_title="Trenque Lauquen")
        self.check("[2006] Volver (Spanish).mkv", part=None)

    def test_invisible_marks_removed(self):
        self.check("[2018] Capernaum (Arabic; كفرناحوم‎, Kafr nāħūm).mkv", natives=["كفرناحوم"])

    def test_not_films(self):
        for name in ("files.csv", "desktop.ini", "Thumbs.db", "[1979] Stalker (Russian).srt", "poster.jpg"):
            self.assertFalse(parse(name).is_video, name)

    def test_future_variants(self):
        self.check("[1982] Blade Runner (The Final Cut).mkv", title="Blade Runner", edition="The Final Cut")
        self.check("[2001] Spirited Away (1).mkv", title="Spirited Away")
        self.check("Paris, Texas (1984).mp4", title="Paris, Texas", year=1984)
        self.check("Some Film.mkv", year=None, title="Some Film")

    def test_every_row_of_the_listing_parses(self):
        listing = ROOT / "files.csv"
        if not listing.exists():
            self.skipTest("no files.csv")
        with open(listing, encoding="utf-8-sig", newline="") as fh:
            rows = [r[0] for r in csv.reader(fh)][1:]
        for name in rows:
            p = parse(name)
            if p.is_video:
                self.assertTrue(p.title, name)
                self.assertIsNotNone(p.year, name)
                self.assertNotIn("(Japanese", p.title, name)


if __name__ == "__main__":
    unittest.main()
