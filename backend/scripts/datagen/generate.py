"""Deterministic synthetic municipal data (no LLM involved): templates + seeded randomisation only."""

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from scripts.datagen import constants as K
from scripts.datagen.geo import build_wards, to_lonlat

CATS = K.CATEGORIES
CAT_INDEX = {c: i for i, c in enumerate(CATS)}
DEPT_BY_CAT = {d.category: d for d in K.DEPARTMENTS}


@dataclass
class Dataset:
    wards: list[dict[str, Any]]
    complaints: list[dict[str, Any]]
    feedback: list[dict[str, Any]]
    performance: list[dict[str, Any]]
    truth: dict[str, Any]


def _iso(start_dt: datetime, seconds: float) -> str:
    return (start_dt + timedelta(seconds=float(seconds))).strftime("%Y-%m-%dT%H:%M:%S") + K.IST


def _burst_window(b: K.Burst, start: date, D: int) -> tuple[int, int] | None:
    s = (b.start - start).days if b.start else D - int(b.days_before_end or 0)
    e = s + b.duration_days
    if e <= 0 or s >= D:
        return None
    return max(s, 0), min(e, D)


def _burst_weights(shape: str, n: int) -> np.ndarray:
    x = np.arange(n, dtype=float)
    if shape == "ramp":
        return np.linspace(0.15, 1.0, n) ** 1.5
    if shape == "spike":
        return np.exp(-x / max(n / 3.0, 1.0)) + 0.05
    return np.ones(n)


def _render_text(py: random.Random, lang: str, cat: str, pi: int, ward: dict[str, Any], burst_key: str | None) -> str:
    pool = K.PROBLEMS[lang][cat]
    if burst_key and py.random() < 0.85:
        problem = py.choice(K.BURST_PROBLEMS[burst_key][lang])
    else:
        problem = pool[pi % len(pool)]
    loc = py.choice(K.LOC_TEMPLATES[lang]).format(
        lm=py.choice(ward["landmarks"]), st=py.choice(ward["streets"]), area=ward["name"]
    )
    if py.random() < 0.15:
        text = f"{problem} {loc}."
    else:
        dur = py.choice(K.DURATION[lang]).format(n=py.randint(2, 9))
        text = f"{problem} {loc} {dur}. {py.choice(K.URGENCY[lang])}"
    text = " ".join(text.split()).replace(" .", ".")
    if lang in ("en", "hinglish") and py.random() < 0.04:  # sloppy typing
        text = text.lower().replace(".", "").replace(",", "")
    return text


def _ward_text(py: random.Random, ward: dict[str, Any]) -> str:
    """Realistic messiness in how a ward is written (all forms are resolvable by the ingestion normaliser)."""
    r = py.random()
    n = int(ward["code"][1:])
    if r < 0.60:
        return ward["name"]
    if r < 0.75:
        return ward["code"]
    if r < 0.85:
        return f"Ward {n}"
    if r < 0.93:
        return ward["name"].lower() if py.random() < 0.5 else ward["name"].upper()
    if r < 0.97:
        return f"  {ward['name']} "
    return ""  # missing: the row still has coordinates, so the pipeline assigns the ward spatially


def generate(n_complaints: int, years: int, seed: int, end_date: date = K.DEFAULT_END_DATE) -> Dataset:
    rng = np.random.default_rng(seed)
    py = random.Random(seed)
    wards = build_wards(rng, py)
    W, C = len(wards), len(CATS)
    start = end_date - timedelta(days=round(365.25 * years))
    D = (end_date - start).days
    start_dt = datetime(start.year, start.month, start.day)
    dates = [start + timedelta(days=d) for d in range(D)]
    month = np.array([d.month for d in dates])
    dow = np.array([d.weekday() for d in dates])

    # ------------------------------------------------------------ baseline volume (day x ward x category)
    season = np.array([[K.SEASONALITY[c][m - 1] for c in CATS] for m in month])  # (D, C)
    dow_f = np.array([1.15, 1.10, 1.0, 1.0, 1.0, 0.9, 0.7])[dow]
    trend = (1 + K.YEARLY_VOLUME_GROWTH) ** (np.arange(D) / 365.25)
    day_cat = season * (dow_f * trend)[:, None] * np.array(K.CATEGORY_SHARE)[None, :]
    pop = np.array([w["population"] for w in wards], dtype=float)
    affinity = rng.lognormal(0, 0.35, (W, C))
    ward_cat = (pop / pop.sum())[:, None] * affinity
    P = (day_cat[:, None, :] * ward_cat[None, :, :]).ravel()
    P /= P.sum()

    # ------------------------------------------------------------ injected bursts (sized relative to the dataset)
    scale = n_complaints / 50_000
    plan: list[tuple[K.Burst, int, int, int]] = []
    for b in K.BURSTS:
        win = _burst_window(b, start, D)
        if win:
            plan.append((b, win[0], win[1], max(2, round(b.count_at_50k * scale))))
    total_burst = sum(p[3] for p in plan)
    if total_burst > 0.3 * n_complaints:
        f = 0.3 * n_complaints / total_burst
        plan = [(b, s, e, max(1, round(c * f))) for b, s, e, c in plan]
        total_burst = sum(p[3] for p in plan)
    n_base = n_complaints - total_burst

    flat = rng.multinomial(n_base, P)
    idx = np.repeat(np.arange(flat.size), flat)
    d_base, w_base, c_base = np.unravel_index(idx, (D, W, C))
    day, ward, cat = [d_base], [w_base], [c_base]
    burst_id = [np.full(idx.size, -1)]
    focus = {b.key: rng.uniform(0.25, 0.75, 2) for b, *_ in plan}
    for bi, (b, s, e, cnt) in enumerate(plan):
        per_day = rng.multinomial(cnt, _burst_weights(b.shape, e - s) / _burst_weights(b.shape, e - s).sum())
        d = np.repeat(np.arange(s, e), per_day)
        w = np.array([int(code[1:]) - 1 for code in b.wards])[rng.integers(0, len(b.wards), d.size)]
        c = np.full(d.size, CAT_INDEX[b.category])
        if b.second_category:
            c[rng.random(d.size) < 0.3] = CAT_INDEX[b.second_category]
        day.append(d), ward.append(w), cat.append(c), burst_id.append(np.full(d.size, bi))
    day, ward, cat, bid = np.concatenate(day), np.concatenate(ward), np.concatenate(cat), np.concatenate(burst_id)
    N = day.size

    # ------------------------------------------------------------ time of day, position inside the ward
    hp = np.array(K.HOUR_P, dtype=float)
    tod = rng.choice(24, N, p=hp / hp.sum()) * 3600 + rng.integers(0, 3600, N)
    t = day * 86400.0 + tod
    u, v = rng.uniform(0.03, 0.97, N), rng.uniform(0.03, 0.97, N)
    hs = rng.uniform(0.15, 0.85, (W, C, 2, 2))  # two chronic hotspots per ward x category
    h = rng.integers(0, 2, N)
    for cname, p_hot in K.HOTSPOT_PROB.items():
        ci = CAT_INDEX[cname]
        m = (cat == ci) & (bid < 0) & (rng.random(N) < p_hot)
        u[m] = np.clip(hs[ward[m], ci, h[m], 0] + rng.normal(0, 0.05, m.sum()), 0.02, 0.98)
        v[m] = np.clip(hs[ward[m], ci, h[m], 1] + rng.normal(0, 0.05, m.sum()), 0.02, 0.98)
    for bi, (b, *_rest) in enumerate(plan):
        m = bid == bi
        if b.key == "potholes":  # along the ring road at the northern edge of the wards
            u[m], v[m] = rng.uniform(0.05, 0.95, m.sum()), np.clip(rng.normal(0.92, 0.03, m.sum()), 0.02, 0.98)
        elif b.spatial_focus:
            u[m] = np.clip(rng.normal(focus[b.key][0], 0.09, m.sum()), 0.02, 0.98)
            v[m] = np.clip(rng.normal(focus[b.key][1], 0.09, m.sum()), 0.02, 0.98)

    # ------------------------------------------------------------ language, problem phrase, near-duplicates
    mix = np.array([K.WARD_LANG_MIX.get(w["code"], K.DEFAULT_LANG_MIX) for w in wards])
    cum = mix.cumsum(axis=1)
    lang_i = (rng.random(N)[:, None] > cum[ward]).sum(axis=1).clip(0, 3)
    pi = rng.integers(0, 4, N)
    en_extra = (lang_i == 0) & (rng.random(N) < 0.3)
    pi[en_extra] = rng.integers(0, 6, en_extra.sum())
    true_cat = cat.copy()

    is_dup = np.zeros(N, dtype=bool)
    dup_src = np.full(N, -1)
    n_dup = int(K.DUPLICATE_RATE * N)
    if n_dup:
        eligible = np.where((bid < 0) & (t < D * 86400 - 3 * 86400))[0]
        srcs = rng.choice(eligible, n_dup, replace=False)
        others = np.setdiff1d(np.where(bid < 0)[0], srcs)
        dups = rng.choice(others, min(n_dup, others.size), replace=False)
        srcs = srcs[: dups.size]
        ward[dups], true_cat[dups], cat[dups] = ward[srcs], true_cat[srcs], true_cat[srcs]
        pi[srcs] = pi[srcs] % 4
        pi[dups] = pi[srcs]
        u[dups], v[dups] = np.clip(u[srcs] + rng.normal(0, 0.004, dups.size), 0.01, 0.99), np.clip(v[srcs] + rng.normal(0, 0.004, dups.size), 0.01, 0.99)
        t[dups] = t[srcs] + rng.uniform(3600, 48 * 3600, dups.size)
        keep_lang = rng.random(dups.size) < 0.5
        lang_i[dups[keep_lang]] = lang_i[srcs[keep_lang]]
        is_dup[dups], dup_src[dups] = True, srcs

    # label noise: a share of complaints carries the wrong category label
    noisy = (rng.random(N) < K.LABEL_NOISE) & ~is_dup
    cat[noisy] = (true_cat[noisy] + rng.integers(1, C, noisy.sum())) % C

    order = np.argsort(t, kind="stable")
    inv = np.empty(N, dtype=int)
    inv[order] = np.arange(N)
    ward, cat, true_cat, bid, u, v, t, lang_i, pi = (a[order] for a in (ward, cat, true_cat, bid, u, v, t, lang_i, pi))
    dup_src = np.where(dup_src[order] >= 0, inv[np.clip(dup_src[order], 0, N - 1)], -1)
    day_ = (t // 86400).astype(int).clip(0, D - 1)

    corners = np.stack([w["corners"] for w in wards])
    lon, lat = to_lonlat(corners[ward], u, v)

    # ------------------------------------------------------------ resolution model (planted effects)
    sla = np.array([DEPT_BY_CAT[CATS[i]].sla_hours for i in range(C)])[true_cat].astype(float)
    frac = np.array([DEPT_BY_CAT[CATS[i]].median_sla_fraction for i in range(C)])[true_cat]
    dept_f = np.array([DEPT_BY_CAT[CATS[i]].performance_factor for i in range(C)])[true_cat]
    ward_f = np.array([K.WARD_PERFORMANCE.get(w["code"], 1.0) for w in wards])[ward]
    pair_f = np.where(rng.random((W, C)) < 0.06, 1.4, 1.0)[ward, true_cat]  # a few bad ward x department pairs
    mon = np.array([K.MONSOON_SLOWDOWN.get(CATS[i], 1.0) for i in range(C)])[true_cat]
    months = month[day_]
    monsoon = np.where((months >= 6) & (months <= 9), mon, 1.0)
    years_in = t / 86400 / 365.25
    roads_trend = np.where(true_cat == CAT_INDEX["roads"], 1 + K.ROADS_YEARLY_DETERIORATION * years_in, 1.0)
    strain = np.where(bid >= 0, 1.5, 1.0)
    hours = sla * frac * ward_f * dept_f * pair_f * monsoon * roads_trend * strain * rng.lognormal(0, 0.55, N)
    hours *= np.where(rng.random(N) < 0.04, 3.0, 1.0)
    p_never = np.clip(0.012 * (ward_f * dept_f) ** 1.5, 0, 0.2)
    never = rng.random(N) < p_never
    rejected = (rng.random(N) < 0.02) & ~never
    hours = np.where(rejected, rng.uniform(1, 24, N), hours)
    end_t = D * 86400.0
    resolved = ~never & ((t + hours * 3600) <= end_t)
    age_h = (end_t - t) / 3600
    u_status = rng.random(N)
    status = np.empty(N, dtype=object)
    for i in range(N):
        if rejected[i] and resolved[i]:
            status[i] = "rejected"
        elif resolved[i]:
            status[i] = "closed" if u_status[i] < 0.7 else "resolved"
        elif age_h[i] < 0.25 * sla[i]:
            status[i] = "open"
        elif never[i]:
            status[i] = "in_progress" if u_status[i] < 0.55 else ("assigned" if u_status[i] < 0.85 else "open")
        else:
            status[i] = "in_progress" if u_status[i] < 0.6 else "assigned"

    # ------------------------------------------------------------ materialise complaint rows
    counters: dict[int, int] = {}
    ext_ids: list[str] = []
    for i in range(N):
        y = (start_dt + timedelta(seconds=float(t[i]))).year
        counters[y] = counters.get(y, 0) + 1
        ext_ids.append(f"CMP-{y}-{counters[y]:06d}")
    chan_i = rng.choice(len(K.CHANNELS), N, p=K.CHANNEL_P)
    complaints: list[dict[str, Any]] = []
    for i in range(N):
        w = wards[ward[i]]
        lang = K.LANGUAGES[lang_i[i]]
        bkey = plan[bid[i]][0].key if bid[i] >= 0 else None
        name = f"{py.choice(K.FIRST_NAMES)} {py.choice(K.LAST_NAMES)}" if py.random() < 0.92 else ""
        phone = "9" + "".join(py.choice("0123456789") for _ in range(9)) if py.random() < 0.85 else ""
        res_at = _iso(start_dt, t[i] + hours[i] * 3600) if resolved[i] else ""
        complaints.append(
            {
                "external_id": ext_ids[i],
                "submitted_at": _iso(start_dt, t[i]),
                "description": _render_text(py, lang, CATS[true_cat[i]], int(pi[i]), w, bkey),
                "language": lang,
                "category": CATS[cat[i]],
                "ward": _ward_text(py, w),
                "latitude": round(float(lat[i]), 6),
                "longitude": round(float(lon[i]), 6),
                "address": f"{py.choice(w['streets'])}, {w['name']}",
                "channel": K.CHANNELS[chan_i[i]],
                "status": str(status[i]),
                "resolved_at": res_at,
                "resolution_hours": round(float(hours[i]), 1) if resolved[i] else "",
                "department": DEPT_BY_CAT[CATS[cat[i]]].name,
                "citizen_name": name,
                "citizen_phone": phone,
                "true_category": CATS[true_cat[i]],
                "true_duplicate_of": ext_ids[dup_src[i]] if dup_src[i] >= 0 else "",
                "burst": bkey or "",
            }
        )

    # ------------------------------------------------------------ feedback
    feedback: list[dict[str, Any]] = []
    for i in range(N):
        if status[i] in ("closed", "resolved") and py.random() < 0.38:
            over = max(0.0, hours[i] / sla[i] - 0.8)
            rating = int(np.clip(round(4.3 - 1.5 * over - 0.6 * (ward_f[i] - 1) + py.gauss(0, 0.7)), 1, 5))
            feedback.append(_feedback_row(py, rating, complaints[i], wards[ward[i]], _iso(start_dt, t[i] + hours[i] * 3600 + py.uniform(3600, 72 * 3600))))
    n_survey = int(0.12 * len(feedback))
    for j in range(n_survey):
        wi = py.randrange(W)
        d = DEPT_BY_CAT[CATS[py.randrange(C)]]
        rating = int(np.clip(round(3.5 - 1.2 * (K.WARD_PERFORMANCE.get(wards[wi]["code"], 1.0) - 1) + py.gauss(0, 1.0)), 1, 5))
        when = py.uniform(0, end_t - 86400)
        row = _feedback_row(py, rating, {"language": K.LANGUAGES[py.randrange(4)], "external_id": "", "department": d.name}, wards[wi], _iso(start_dt, when))
        row["channel"] = "survey"
        feedback.append(row)
    feedback = [f for f in feedback if f["submitted_at"] <= _iso(start_dt, end_t)]

    performance = _performance(rng, wards, start, D, day_, ward, cat, resolved, hours, sla, age_h)

    truth = {
        "generator_version": 1, "city": K.CITY, "seed": seed, "complaints": N, "years": years,
        "period": [str(start), str(end_date)],
        "underperforming_wards": {k: v for k, v in K.WARD_PERFORMANCE.items() if v > 1},
        "well_run_wards": {k: v for k, v in K.WARD_PERFORMANCE.items() if v < 1},
        "department_performance_factor": {d.code: d.performance_factor for d in K.DEPARTMENTS},
        "roads_yearly_deterioration": K.ROADS_YEARLY_DETERIORATION,
        "monsoon_slowdown": K.MONSOON_SLOWDOWN,
        "label_noise_rate": K.LABEL_NOISE,
        "duplicate_rate": K.DUPLICATE_RATE,
        "bursts": [
            {"key": b.key, "label": b.label, "category": b.category, "wards": list(b.wards),
             "start": str(start + timedelta(days=s)), "end": str(start + timedelta(days=e)), "complaints": c}
            for b, s, e, c in plan
        ],
    }
    return Dataset(wards, complaints, feedback, performance, truth)


def _feedback_row(py: random.Random, rating: int, c: dict[str, Any], ward: dict[str, Any], when: str) -> dict[str, Any]:
    bucket = "low" if rating <= 2 else ("mid" if rating == 3 else "high")
    lang = c["language"]
    text = py.choice(K.FEEDBACK_TEXT[bucket][lang]) if py.random() < 0.7 else ""
    return {
        "complaint_external_id": c["external_id"], "ward": ward["name"], "department": c["department"],
        "rating": rating, "text": text, "language": lang, "channel": "complaint_followup", "submitted_at": when,
    }


def _performance(rng: np.random.Generator, wards: list[dict[str, Any]], start: date, D: int, day_: np.ndarray,
                 ward: np.ndarray, cat: np.ndarray, resolved: np.ndarray, hours: np.ndarray, sla: np.ndarray,
                 age_h: np.ndarray) -> list[dict[str, Any]]:
    """Monthly per department x ward (and city-wide) records derived from the complaints, plus staffing/budget."""
    W, C = len(wards), len(CATS)
    dates = [start + timedelta(days=d) for d in range(D)]
    month_keys = sorted({(d.year, d.month) for d in dates})
    mi_of = {k: i for i, k in enumerate(month_keys)}
    m_idx = np.array([mi_of[(dates[d].year, dates[d].month)] for d in day_])
    M = len(month_keys)
    key = (cat * W + ward) * M + m_idx
    size = C * W * M
    breach = (resolved & (hours > sla)) | (~resolved & (age_h > sla))
    received = np.bincount(key, minlength=size)
    res_n = np.bincount(key, weights=resolved.astype(float), minlength=size)
    br_n = np.bincount(key, weights=breach.astype(float), minlength=size)
    res_h = np.bincount(key, weights=np.where(resolved, hours, 0.0), minlength=size)
    staff_base = rng.integers(3, 9, (C, W))
    rows: list[dict[str, Any]] = []
    city: dict[tuple[int, int], list[float]] = {}
    for c in range(C):
        dept = DEPT_BY_CAT[CATS[c]]
        for w in range(W):
            for m in range(M):
                k = (c * W + w) * M + m
                if received[k] == 0:
                    continue
                y, mo = month_keys[m]
                staff = int(staff_base[c, w] + rng.integers(-1, 2))
                budget = round(staff * 38_000 * rng.uniform(0.9, 1.15), 0)
                avg = round(float(res_h[k] / res_n[k]), 1) if res_n[k] else None
                rows.append({"department": dept.name, "ward": wards[w]["name"], "period": f"{y}-{mo:02d}-01",
                             "complaints_received": int(received[k]), "complaints_resolved": int(res_n[k]),
                             "sla_breaches": int(br_n[k]), "avg_resolution_hours": avg, "staff_count": staff, "budget_spent": budget})
                agg = city.setdefault((c, m), [0, 0, 0, 0.0, 0, 0.0])
                agg[0] += received[k]; agg[1] += res_n[k]; agg[2] += br_n[k]; agg[3] += res_h[k]; agg[4] += staff; agg[5] += budget
    for (c, m), a in sorted(city.items()):
        y, mo = month_keys[m]
        rows.append({"department": DEPT_BY_CAT[CATS[c]].name, "ward": "", "period": f"{y}-{mo:02d}-01",
                     "complaints_received": int(a[0]), "complaints_resolved": int(a[1]), "sla_breaches": int(a[2]),
                     "avg_resolution_hours": round(a[3] / a[1], 1) if a[1] else None, "staff_count": int(a[4]), "budget_spent": a[5]})
    return rows
