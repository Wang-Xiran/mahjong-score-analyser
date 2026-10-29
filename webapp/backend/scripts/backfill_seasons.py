"""One-off backfill: tag existing games with their season (1-3), using each
season's GP numbers read off the site's "Showing: Season N" scoreboard
dropdown. Season 4 is already tagged live by the normal scrape path (see
scraper.compute_current_season_game_keys) — this script only needs to run
once, for whatever seasons are still untagged.

The GP values below were captured by hand from the dropdown (the site has no
URL/API for picking a past season — switching it is an internal Next.js
Server Action, not something a plain scrape request can replicate), so if
more seasons close out later, re-run this with a new SEASON_N block added
(and run it in reverse-chronological order: most-recent-first).

Usage: python scripts/backfill_seasons.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db
from app.scraper import backfill_season

VENUE = "boxhill"

SEASON_3 = {
    "Jake Beltrami": 32, "Ken Tsuji": 16, "Thomas Parasiuk": 17, "Yoshi N": 27,
    "Bill M": 15, "Aaron Chee": 21, "Peter D": 19, "Yudai Baba": 9,
    "Man Lee So": 24, "Joe L": 12, "Henry C": 7, "Andrew Tang (JaffaOrange)": 13,
    "Jasper T": 10, "Daniel Simmonds (DSX)": 3, "Noriko Itoh": 23, "Nicholas H": 16,
    "John": 10, "Xiran Wang": 32, "Richard Kiing": 2, "Vincent Nguyen": 3,
    "Aki K": 18, "Jojo M": 18, "Daisy Zhang": 2, "Hong Li": 3, "Nicole C": 2,
    "Lea Leung": 2, "Charles P": 14, "Kevin C": 1, "Trong Nguyen": 4, "Alan N": 2,
    "Dandy R": 2, "Ken L": 3, "Aidan Wintershoven": 2, "Izzy": 2, "Wilson Lui": 12,
    "Ivan Palko": 2, "Jessica L": 2, "Angie M": 2, "Simone P": 2, "Alfred L": 1,
    "Mayu S": 2, "Alicia": 17, "Juanna Z": 2, "Neo M": 5, "Vincent Chen": 2,
    "Evan B": 2, "Erin B": 2, "Anthony D": 2, "Steven L": 6, "Tsz Kiu Pang": 3,
    "Jolene": 6, "Ari": 6, "Lily R": 2, "Eugene Yap": 19, "Melvin Tandra": 8,
    "Atsushi Y": 3, "Tim B": 30,
}

SEASON_2 = {
    "Josh Tey": 18, "Jake Beltrami": 32, "Noriko Itoh": 33, "Xiran Wang": 35,
    "Henry C": 8, "Nicholas H": 22, "Yoshi N": 27, "Charles P": 8,
    "Vincent Nguyen": 10, "Aaron Chee": 17, "Daisy Zhang": 2, "Eugene Yap": 23,
    "Richard L": 5, "Wilson Lui": 3, "Andrew Tang (JaffaOrange)": 8, "Nicole C": 3,
    "Jasper T": 5, "Joe L": 3, "Man Lee So": 26, "Melvin Tandra": 10,
    "Ken Tsuji": 11, "Alfred L": 1, "Derrick L": 3, "Wesley T": 3, "Sachiko K": 5,
    "Keith L": 2, "Alan N": 2, "Tsz Kiu Pang": 6, "LILIA": 3, "Jojo M": 18,
    "Jolene": 2, "Peter D": 17, "Sokhun Lim": 6, "Jason Y": 3,
    "Harry Huang (SMMY)": 1, "Samson": 1, "Jason Birch (Jirby)": 1,
    "Thomas Parasiuk": 13, "Mike W": 4, "Trong Nguyen": 4, "Matthew P": 1,
    "Jaymond He": 3, "Aki K": 4, "Max Peng": 6, "Aj": 4, "Sun": 1, "Ebi": 2,
    "Lily R": 2, "Harris": 4, "Lulu": 5, "Alicia": 4, "Frankie Z": 2,
    "Nicole O": 2, "Taiyo": 2, "Stephen Philp": 3, "Ivan Palko": 26, "Henry": 10,
    "Ari": 5, "Xin Yang": 6, "Tim B": 16,
}

SEASON_1 = {
    "Yoshi N": 33, "Henry": 27, "Tsz Kiu Pang": 4, "Andrew Tang (JaffaOrange)": 10,
    "Melvin Tandra": 11, "Trong Nguyen": 5, "Noriko Itoh": 21, "Vincent Nguyen": 13,
    "Aaron Chee": 17, "Nicholas H": 19, "Anthony D": 3, "Jake Beltrami": 26,
    "Daisy Zhang": 3, "Henry C": 8, "Max Peng": 11, "Charles P": 14, "Val": 4,
    "Taiga Hirotaki": 3, "Ken Tsuji": 5, "Matt S": 1, "Bennet Allison": 1,
    "Stephen Philp": 2, "Ivan Palko": 23, "Jasper T": 3, "Jason Birch (Jirby)": 1,
    "Max Cou": 1, "Sokhun Lim": 2, "Daniel Simmonds (DSX)": 4, "Peter D": 9,
    "Xin Yang": 6, "Devlyn Wu": 1, "John": 6, "Josh Tey": 7, "Kiki Mo": 14,
    "Thomas Parasiuk": 12, "Mike W": 6, "Yoshi Tjahyadi": 9, "Arvee": 1,
    "Jaymond He": 2, "Minhsan M": 1, "George Guo": 1, "Kai Unknown": 1, "Saya": 3,
    "Kavi": 1, "Jolene": 3, "Maki": 1, "Bennet M": 3, "Nicole C": 13,
    "David Montenot": 3, "Xiran Wang": 3, "Eugene Yap": 15, "Alfred L": 4,
}

if __name__ == "__main__":
    init_db()
    db = SessionLocal()
    try:
        # Reverse-chronological: each call's "untagged" pool must exclude
        # whatever a more-recent season already claimed.
        for label, games_played in [("Season 3", SEASON_3), ("Season 2", SEASON_2), ("Season 1", SEASON_1)]:
            count = backfill_season(VENUE, label, games_played, db)
            print(f"{label}: tagged {count} games")
    finally:
        db.close()
