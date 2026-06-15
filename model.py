"""Statistical model (Poisson) — turns odds + player stats into probabilities.

No AI involved: everything here is computed from real data.
"""
from __future__ import annotations

import unicodedata
from math import exp, factorial

MAX_GOALS = 7


# ------------------------------------------------------------ lineup filter


def _norm_name(s: str) -> str:
    """Normalize a player name for matching: accents off, dots out, casefold."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.replace(".", " ").casefold().split())


def _names_match(a: str, b: str) -> bool:
    """Tolerant match between two normalized names.

    Handles source differences like 'L. Messi' vs 'Lionel Messi':
    exact / containment, or same last name + same first initial.
    """
    if a == b or a in b or b in a:
        return True
    ta, tb = a.split(), b.split()
    return bool(ta and tb) and ta[-1] == tb[-1] and ta[0][:1] == tb[0][:1]


def filter_to_lineup(players: dict, allowed_names: list[str]) -> dict:
    """Keep only the players that appear in the official lineup list."""
    allowed = [_norm_name(n) for n in allowed_names]
    return {
        name: st
        for name, st in players.items()
        if any(_names_match(_norm_name(name), a) for a in allowed)
    }


def poisson_pmf(k: int, lam: float) -> float:
    return exp(-lam) * lam**k / factorial(k)


def implied_probs(odds: dict[str, float]) -> dict[str, float]:
    """Convert decimal odds to margin-free implied probabilities."""
    inv = {k: 1.0 / v for k, v in odds.items() if v and v > 1.0}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s else {}


def score_matrix(lh: float, la: float) -> list[list[float]]:
    return [
        [poisson_pmf(h, lh) * poisson_pmf(a, la) for a in range(MAX_GOALS)]
        for h in range(MAX_GOALS)
    ]


def _matrix_summary(m: list[list[float]]) -> tuple[float, float, float, float]:
    """(p_home_win, p_draw, p_away_win, p_over_2.5)"""
    ph = pd = pa = po = 0.0
    for h in range(MAX_GOALS):
        for a in range(MAX_GOALS):
            p = m[h][a]
            if h > a:
                ph += p
            elif h == a:
                pd += p
            else:
                pa += p
            if h + a > 2:
                po += p
    return ph, pd, pa, po


def fit_lambdas(
    p_home: float, p_draw: float, p_away: float, p_over25: float | None = None
) -> tuple[float, float]:
    """Grid-search expected goals (home, away) matching the market probabilities."""
    best, best_err = (1.3, 1.1), float("inf")
    steps = [0.2 + 0.05 * i for i in range(57)]  # 0.20 .. 3.00
    for lh in steps:
        for la in steps:
            ph, pd, pa, po = _matrix_summary(score_matrix(lh, la))
            err = (ph - p_home) ** 2 + (pd - p_draw) ** 2 + (pa - p_away) ** 2
            if p_over25 is not None:
                err += (po - p_over25) ** 2
            if err < best_err:
                best_err, best = err, (lh, la)
    return best


def top_scores(lh: float, la: float, k: int = 2) -> list[tuple[str, float]]:
    """k most likely scorelines for given expected goals: [('1-0', 0.18), ...]"""
    m = score_matrix(lh, la)
    flat = [(f"{h}-{a}", m[h][a]) for h in range(5) for a in range(5)]
    flat.sort(key=lambda x: x[1], reverse=True)
    return flat[:k]


def _score_key(s: str) -> str:
    """Normalize a scoreline so '1:0', '1-0', ' 1 - 0 ' all compare equal."""
    return s.replace(":", "-").replace(" ", "").strip()


def final_results(
    lh: float,
    la: float,
    correct_score_odds: dict[str, float] | None = None,
    k: int = 4,
) -> tuple[list[dict], bool]:
    """Top-k full-time scorelines with probability and odds.

    Each item: {"score": "1-0", "prob": 18.2, "odd": 7.5, "odd_source": "book"}.
    Odds use the real bookmaker Correct Score market when that exact score is
    quoted, otherwise fair model-implied odds (1/probability). Returns
    (results, any_book_odds_used).
    """
    book = {_score_key(k_): v for k_, v in (correct_score_odds or {}).items() if v}
    any_book = False
    out: list[dict] = []
    for score, p in top_scores(lh, la, k):
        odd = book.get(_score_key(score))
        if odd:
            any_book = True
            src = "book"
        else:
            odd = round(1.0 / p, 2) if p > 0 else None
            src = "model"
        out.append(
            {"score": score, "prob": round(p * 100, 1), "odd": odd, "odd_source": src}
        )
    return out, any_book


def split_halves(
    lh: float, la: float, fh_share_home: float = 0.45, fh_share_away: float = 0.45
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Split full-match lambdas into (first-half, second-half) lambdas."""
    fh = (lh * fh_share_home, la * fh_share_away)
    sh = (lh - fh[0], la - fh[1])
    return fh, sh


def first_scorer_probs(
    home_players: dict, away_players: dict, lh: float, la: float, top_n: int = 5
) -> list[tuple[str, float]]:
    """P(player scores first) ≈ player share of team goals × P(team scores first)."""
    p_no_goal = poisson_pmf(0, lh + la)
    p_home_first = (lh / (lh + la)) * (1 - p_no_goal) if lh + la > 0 else 0
    p_away_first = (la / (lh + la)) * (1 - p_no_goal) if lh + la > 0 else 0

    out: list[tuple[str, float]] = []
    for players, p_team in ((home_players, p_home_first), (away_players, p_away_first)):
        total = sum(p["goals"] for p in players.values()) or 1
        for name, st in players.items():
            if st["goals"] > 0:
                out.append((name, st["goals"] / total * p_team))
    out.sort(key=lambda x: x[1], reverse=True)
    return [(n, round(p * 100, 1)) for n, p in out[:top_n]]


def first_card_probs(
    home_players: dict, away_players: dict, top_n: int = 2
) -> dict[str, list[tuple[str, float]]]:
    """The top_n players per team most likely to receive the first card.

    Probabilities are P(player gets the match's first card) from empirical
    yellow-card shares, normalized across BOTH teams (so home and away numbers
    stay directly comparable), then grouped by team. Returns
    {"home": [(name, pct), ...], "away": [...]} (empty if no card history).
    """
    sides = {
        "home": {n: st["yellows"] for n, st in home_players.items() if st["yellows"] > 0},
        "away": {n: st["yellows"] for n, st in away_players.items() if st["yellows"] > 0},
    }
    total = sum(sum(counts.values()) for counts in sides.values())
    if not total:
        return {}
    p_any_card = 0.93  # ~baseline: at least one card in a WC match
    out: dict[str, list[tuple[str, float]]] = {}
    for side, counts in sides.items():
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        picks = [(n, round(c / total * p_any_card * 100, 1)) for n, c in ranked[:top_n]]
        if picks:
            out[side] = picks
    return out


def first_sub_probs(
    home_players: dict, away_players: dict, top_n: int = 2
) -> dict[str, list[tuple[str, float]]]:
    """The top_n players per team most likely to be the first one subbed off.

    Weight = how often a player is subbed off × how early. Normalized across
    both teams, then grouped by team. Returns {"home": [...], "away": [...]}.
    """

    def team_weights(players: dict) -> dict[str, float]:
        w: dict[str, float] = {}
        for name, st in players.items():
            mins = st["sub_off_minutes"]
            if not mins:
                continue
            avg_min = sum(mins) / len(mins)
            earliness = max(0.0, (90 - avg_min) / 45)  # 1.0 at 45', 0 at 90'
            w[name] = len(mins) * (0.5 + earliness)
        return w

    sides = {"home": team_weights(home_players), "away": team_weights(away_players)}
    total = sum(sum(w.values()) for w in sides.values())
    if not total:
        return {}
    out: dict[str, list[tuple[str, float]]] = {}
    for side, weights in sides.items():
        ranked = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        picks = [(n, round(w / total * 100, 1)) for n, w in ranked[:top_n]]
        if picks:
            out[side] = picks
    return out


def build_prediction(
    api_data: dict,
    home_stats: dict,
    away_stats: dict,
    lineups: dict | None = None,
) -> dict:
    """Combine everything into one prediction dict with per-section sources.

    lineups (optional): {"home": {"starters": [...], "subs": [...]},
                         "away": {...}} — official lineups. When present,
    player markets only include players actually in the squad.
    """
    pred: dict = {"sections": {}, "sources": set()}

    # --- Expected goals: from odds first, else from team stats ---
    odds = api_data.get("odds", {}) if api_data else {}
    mw = implied_probs(odds.get("match_winner", {}))
    ou = implied_probs(odds.get("over_under_2.5", {}))
    lh = la = None
    if {"home", "draw", "away"} <= mw.keys():
        lh, la = fit_lambdas(mw["home"], mw["draw"], mw["away"], ou.get("over"))
        pred["sources"].add("API-Football odds")
    elif home_stats and away_stats:
        lh = home_stats.get("goals_for_per_match") or 1.2
        la = away_stats.get("goals_for_per_match") or 1.2
        label = home_stats.get("source_name", "Sofascore")
        pred["sources"].add(f"{label} team stats")

    if lh is not None:
        # Full-time result: the 4 most likely final scorelines, each paired
        # with its real bookmaker Correct Score odd when available (else fair
        # model-implied odds).
        results, any_book = final_results(lh, la, odds.get("correct_score"), k=4)
        pred["sections"]["final_result"] = {"results": results}
        if any_book:
            pred["sources"].add("API-Football correct-score odds")

        # Per-half scorelines: prefer bookmaker first-half 1X2 odds (paid
        # API-Football plans), else split by each team's first-half goal share.
        fhw = implied_probs(odds.get("first_half_winner", {}))
        if {"home", "draw", "away"} <= fhw.keys():
            lh1, la1 = fit_lambdas(fhw["home"], fhw["draw"], fhw["away"])
            lh1, la1 = min(lh1, lh * 0.8), min(la1, la * 0.8)  # 1H can't exceed FT
            lh2, la2 = max(lh - lh1, 0.05), max(la - la1, 0.05)
            pred["sources"].add("API-Football 1H odds")
        else:
            fh_h = home_stats.get("first_half_goal_share", 0.45) if home_stats else 0.45
            fh_a = away_stats.get("first_half_goal_share", 0.45) if away_stats else 0.45
            (lh1, la1), (lh2, la2) = split_halves(lh, la, fh_h, fh_a)
        pred["sections"]["first_half"] = {
            "scores": [(s, round(p * 100, 1)) for s, p in top_scores(lh1, la1)]
        }
        pred["sections"]["second_half"] = {
            "scores": [(s, round(p * 100, 1)) for s, p in top_scores(lh2, la2)]
        }
        pred["lambdas"] = (round(lh, 2), round(la, 2))

    # --- Player markets: from recent-match player stats ---
    hp = home_stats.get("players", {}) if home_stats else {}
    ap = away_stats.get("players", {}) if away_stats else {}

    # Official lineups (if published): never suggest players who aren't
    # playing. Scorers may come from the whole squad (starters + bench);
    # first card / first sub can only involve starters.
    hp_squad, ap_squad = hp, ap      # starters + bench
    hp_start, ap_start = hp, ap      # starters only
    lineup_applied = False
    if lineups:
        h_lu = lineups.get("home") or {}
        a_lu = lineups.get("away") or {}
        if h_lu.get("starters"):
            hp_start = filter_to_lineup(hp, h_lu["starters"])
            hp_squad = filter_to_lineup(hp, h_lu["starters"] + (h_lu.get("subs") or []))
            lineup_applied = True
        if a_lu.get("starters"):
            ap_start = filter_to_lineup(ap, a_lu["starters"])
            ap_squad = filter_to_lineup(ap, a_lu["starters"] + (a_lu.get("subs") or []))
            lineup_applied = True
    if lineup_applied:
        pred["sources"].add("official lineups")

    if hp or ap:
        label = (home_stats or away_stats).get("source_name", "Sofascore")
        pred["sources"].add(f"{label} player stats")
        if lh is not None:
            scorers = first_scorer_probs(hp_squad, ap_squad, lh, la)
            if scorers:
                pred["sections"]["scorers"] = scorers
        cards = first_card_probs(hp_start, ap_start)
        if cards:
            pred["sections"]["first_card"] = cards
        subs = first_sub_probs(hp_start, ap_start)
        if subs:
            pred["sections"]["first_sub"] = subs

    pred["sources"] = sorted(pred["sources"])
    return pred
