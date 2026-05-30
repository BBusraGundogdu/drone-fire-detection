import os


import pandas as pd
import numpy as np
import gurobipy as gp
from gurobipy import GRB
import math
import time as timer
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D



HEX_SCALE = "110"   # "44" | "110" | "340"

_SCALE_CSV = {
    "44":  r"D:\Firdevs\Desktop\deneme\Son_44_Altigen_Koordinatli.csv",
    "110": r"C:\Users\gundo\Downloads\110_Altigen_Koordinatli (1).csv",
    "340": r"D:\Firdevs\Desktop\deneme\Altigen_Verileri_Duzenli.csv",
}
_SCALE_OUTPUT_SUBDIR = {"44": "44hex", "110": "110hex", "340": "340hex"}

# Solver ayarlari da olcege gore (bunlar matematik degil, ham solver tuning):
_SCALE_SOLVER = {
    "44":  {"K_HEX_NEIGHBORS": 8,  "TIME_LIMIT": 300,  "MIP_GAP": 0.15, "STATION_LIMIT_PCT": 4.0},
    "110": {"K_HEX_NEIGHBORS": 10, "TIME_LIMIT": 600,  "MIP_GAP": 0.20, "STATION_LIMIT_PCT": 3.5},
    "340": {"K_HEX_NEIGHBORS": 12, "TIME_LIMIT": 1200, "MIP_GAP": 0.25, "STATION_LIMIT_PCT": 2.5},
}



class Config:
    # ---- YOLLAR (V16: olcege gore otomatik) -------------------------------
    HEX_CSV    = _SCALE_CSV[HEX_SCALE]
    OUTPUT_DIR = r"D:\Firdevs\Desktop\deneme" + "\\" + _SCALE_OUTPUT_SUBDIR[HEX_SCALE]
    SCALE_NAME = HEX_SCALE  # bilgilendirme amacli

    # ---- ISTASYONLAR (k-means + risk-agirlikli centroid) ------------------
    STATIONS = [
        {"id": "S1", "lat": 38.188180, "lon": 26.779120},
        {"id": "S2", "lat": 38.179069, "lon": 26.784845},
        {"id": "S3", "lat": 38.188075, "lon": 26.791065},
    ]
    # ---- DRONLAR (V19: 110 hex N_d=10 duyarlilik analizi) ----------------
    
    DRONES_HOME = {
        "D1":  "S1", "D2":  "S1", "D3":  "S1", "D4":  "S2",
        "D5":  "S2", "D6":  "S3", "D7":  "S3",
    }

    # ---- BATARYA (DJI Matrice 30T) ----------------------------------------
    E_CAP    = 100.0     # %
    E_MIN    = 20.0      # %
    D_FLIGHT = 2.0       # %/dk (ucus)
    D_HOVER  = 2.2       # %/dk (hover/tarama)
    C_RATE   = 1.6       # %/dk (sarj)
    V_CRUISE = 15.0      # m/s
    E_DEP    = 60.0      # % (kalkis esigi - istasyondan ayrilmak icin minimum)
    HOVER_TIME_MIN = 1.0  # dk 

    # ---- V11: BLOK SONU ENERJI ESIGI ------------------------
    E_BLOCK_END = 60.0   # % (= E_DEP)

    # ---- ZAMAN -------------------------------------------------------------
    DELTA_T     = 10     # dk/dilim
    BLOCK_HOURS = 2      # V13: 1 -> 2 (kapasite kalibrasyonu)
    TOTAL_HOURS = 24     # toplam plan (saat)

    # ---- SARJ -------------------------------------------------------------
    CAP_S  = 10
    S_WAIT = 1         

    # ---- PERIYODIK DENETIM ------------------
    MAX_INTERVAL_HIGH = 180     # dk (V12: 120, V13: 180)
    MAX_INTERVAL_LOW  = 360     # dk (degismedi) - dusuk riskli hex (6h)
    TAU_BUFFER        = 240     # bilgi amacli, kisit kaynagi degil

    # ---- KENAR FILTRELEME (V16: olcege gore otomatik) ---------------------
    K_HEX_NEIGHBORS    = _SCALE_SOLVER[HEX_SCALE]["K_HEX_NEIGHBORS"]
    STATION_LIMIT_PCT  = _SCALE_SOLVER[HEX_SCALE]["STATION_LIMIT_PCT"]

    # ---- CEVRIM  -------------------
    RETURN_HOME_EVERY_BLOCK = True   

    # ---- AMAC FONKSIYONU AGIRLIKLARI  ------------------
    ALPHA = 1.0
    BETA  = 10.0
    GAMMA = 5.0     # V8: 2.0

    # ---- GUROBI (V16: olcege gore otomatik) -------------------------------
    TIME_LIMIT_PER_BLOCK = _SCALE_SOLVER[HEX_SCALE]["TIME_LIMIT"]
    MIP_GAP              = _SCALE_SOLVER[HEX_SCALE]["MIP_GAP"]
    MIP_FOCUS            = 2       
                                   
    HEURISTICS_PCT       = 0.30    # V14: 0.20 -> 0.30 (daha agresif heuristik)
    VERBOSE              = False   # True: Gurobi log; False: sessiz

    # ---- CIKTI ------------------------------------------------------------
    EXPORT_CSV           = True
    EXPORT_PNG           = True
    EXPORT_BLOCK_DETAILS = True

    @property
    def N_BLOCKS(self):
        return self.TOTAL_HOURS // self.BLOCK_HOURS

    @property
    def BLOCK_LEN(self):
        return self.BLOCK_HOURS * 60 // self.DELTA_T



# ==============================================================================
# VERI YUKLEME
# ==============================================================================
def load_data(cfg):
    df = pd.read_csv(cfg.HEX_CSV)
    df.columns = df.columns.str.strip().str.replace("\ufeff", "")
    df = df.sort_values("id").reset_index(drop=True)

    lat_ref   = df["Latitude"].mean()
    lon_ref   = df["Longitude"].mean()
    m_per_lon = 111_000 * math.cos(math.radians(lat_ref))

    coords, risks, hex_ids = {}, {}, []
    for _, row in df.iterrows():
        hid = f"H{int(row['id'])}"
        coords[hid] = (
            (row["Longitude"] - lon_ref) * m_per_lon,
            (row["Latitude"]  - lat_ref) * 111_000,
        )
        risks[hid] = float(row["TOPLAM_RISK"])
        hex_ids.append(hid)

    for st in cfg.STATIONS:
        coords[st["id"]] = (
            (st["lon"] - lon_ref) * m_per_lon,
            (st["lat"] - lat_ref) * 111_000,
        )

    stn_ids = [st["id"] for st in cfg.STATIONS]
    return coords, risks, hex_ids, stn_ids, lat_ref, lon_ref, m_per_lon


def compute_geometry(coords, V, cfg):
    tau_step, travel_energy = {}, {}
    for i in V:
        for j in V:
            if i == j:
                continue
            xa, ya = coords[i]
            xb, yb = coords[j]
            d_m   = math.hypot(xa - xb, ya - yb)
            t_min = d_m / cfg.V_CRUISE / 60.0   # dakika
            tau_step[(i, j)]      = max(1, math.ceil(t_min / cfg.DELTA_T))
            travel_energy[(i, j)] = cfg.D_FLIGHT * t_min
    return tau_step, travel_energy


def range_filter(V, travel_energy, hex_ids, stn_ids, cfg):
    edges = set()

    # 1. Hex-to-hex: k-NN
    for i in hex_ids:
        outgoing = sorted(
            [(j, travel_energy[(i, j)]) for j in hex_ids if j != i],
            key=lambda x: x[1]
        )
        for j, _ in outgoing[:cfg.K_HEX_NEIGHBORS]:
            edges.add((i, j))

    for s in stn_ids:
        for h in hex_ids:
            if travel_energy[(s, h)] <= cfg.STATION_LIMIT_PCT:
                edges.add((s, h))
            if travel_energy[(h, s)] <= cfg.STATION_LIMIT_PCT:
                edges.add((h, s))

    return list(edges)


def compute_max_interval(risks_dict, hex_ids, cfg):
    risk_min  = min(risks_dict.values())
    risk_max  = max(risks_dict.values())
    risk_span = risk_max - risk_min if risk_max > risk_min else 1.0

    max_interval = {}
    for i in hex_ids:
        r = risks_dict[i]
        norm = (risk_max - r) / risk_span         # 0=en yuksek risk, 1=en dusuk
        max_interval[i] = (cfg.MAX_INTERVAL_HIGH +
                           (cfg.MAX_INTERVAL_LOW - cfg.MAX_INTERVAL_HIGH) * norm)
    return max_interval, risk_min, risk_max


# ==============================================================================
# TEK BLOK MILP
# ==============================================================================
def solve_block(cfg, V, hex_ids, stn_ids, DRONES, home,
                tau_step, travel_energy, allowed,
                start_loc, start_E, start_tau,
                risks_dict, max_interval,
                is_final_block, block_idx):
 
    m = gp.Model(f"Blok_{block_idx}")
    m.Params.OutputFlag = 1 if cfg.VERBOSE else 0
    m.Params.TimeLimit  = cfg.TIME_LIMIT_PER_BLOCK
    m.Params.MIPGap     = cfg.MIP_GAP

    m.Params.MIPFocus   = cfg.MIP_FOCUS
    m.Params.Heuristics = cfg.HEURISTICS_PCT

    T_LEN  = cfg.BLOCK_LEN
    T      = list(range(T_LEN))
    T_PLUS = list(range(T_LEN + 1))

    # ── Degiskenler ─────────────────────────────────────────────────────────
    x = {(d, i, j, t): m.addVar(vtype=GRB.BINARY, name=f"x_{d}_{i}_{j}_{t}")
         for d in DRONES for (i, j) in allowed for t in T}

    at = {(d, i, t): m.addVar(vtype=GRB.BINARY, name=f"at_{d}_{i}_{t}")
          for d in DRONES for i in V for t in T_PLUS}

    visit = {(d, i, t): m.addVar(vtype=GRB.BINARY, name=f"visit_{d}_{i}_{t}")
             for d in DRONES for i in hex_ids for t in T}

    charging = {(d, s, t): m.addVar(vtype=GRB.BINARY, name=f"chg_{d}_{s}_{t}")
                for d in DRONES for s in stn_ids for t in T}

    E = {(d, t): m.addVar(lb=cfg.E_MIN, ub=cfg.E_CAP, name=f"E_{d}_{t}")
         for d in DRONES for t in T_PLUS}

    overflow = {(d, t): m.addVar(lb=0.0, name=f"ovf_{d}_{t}")
                for d in DRONES for t in T}

    tau = {(i, t): m.addVar(lb=0.0, ub=1500.0, name=f"tau_{i}_{t}")
           for i in hex_ids for t in T_PLUS}

    slack = {(i, t): m.addVar(lb=0.0, name=f"slack_{i}_{t}")
             for i in hex_ids for t in T}

    # ── BASLANGIC ────────────────────────────────
    for d in DRONES:
        for i in V:
            if i == start_loc[d]:
                m.addConstr(at[d, i, 0] == 1, name=f"start_loc_{d}_{i}")
            else:
                m.addConstr(at[d, i, 0] == 0, name=f"start_zero_{d}_{i}")
        m.addConstr(E[d, 0] == start_E[d], name=f"start_E_{d}")

    for i in hex_ids:
        s_tau_clamped = min(start_tau.get(i, 0), 1400.0)
        m.addConstr(tau[i, 0] == s_tau_clamped, name=f"tau_init_{i}")

    # ── AKIS / SUREKLILIK ───────────────────────────────────────────────────
    for d in DRONES:
        for t in T_PLUS:
            m.addConstr(gp.quicksum(at[d, i, t] for i in V) == 1,
                        name=f"tek_konum_{d}_{t}")

    
    for (d, i, j, t) in x:
        m.addConstr(x[d, i, j, t] <= at[d, i, t], name=f"har_bas_{d}_{i}_{j}_{t}")

    
    for (d, i, j, t) in x:
        t_arr = t + tau_step[(i, j)]
        if t_arr <= T_LEN:
            m.addConstr(at[d, j, t_arr] >= x[d, i, j, t],
                        name=f"varis_{d}_{i}_{j}_{t}")

    
    for d in DRONES:
        for i in V:
            for t in T:
                kalkis = gp.quicksum(x[d, i, j, t] for j in V
                                     if j != i and (d, i, j, t) in x)
                m.addConstr(at[d, i, t+1] + kalkis >= at[d, i, t],
                            name=f"yerinde_{d}_{i}_{t}")

   
    for d in DRONES:
        for t in T:
            exprs = [x[d, i, j, t] for (i, j) in allowed if (d, i, j, t) in x]
            if exprs:
                m.addConstr(gp.quicksum(exprs) <= 1, name=f"tek_har_{d}_{t}")

    # ── EYLEM TEKILLIGI  ─────────────
    for d in DRONES:
        for t in T:
            m.addConstr(
                gp.quicksum(x[d, i, j, t] for (i, j) in allowed
                            if (d, i, j, t) in x)
                + gp.quicksum(visit[d, i, t] for i in hex_ids
                              if (d, i, t) in visit)
                + gp.quicksum(charging[d, s, t] for s in stn_ids
                              if (d, s, t) in charging)
                <= 1,
                name=f"tek_eylem_{d}_{t}"
            )

    # ── ZIYARET ────────────────────────────────────────────────────────────
    for (d, i, t) in visit:
        m.addConstr(visit[d, i, t] <= at[d, i, t], name=f"vis_pos_{d}_{i}_{t}")

    # ── CARPISMA KISITLARI ──────────────────────────────────────
    for i in hex_ids:
        for t in T_PLUS:
            m.addConstr(
                gp.quicksum(at[d, i, t] for d in DRONES) <= 1,
                name=f"carp_hex_{i}_{t}"
            )

  
    seen_pairs = set()
    for (i, j) in allowed:
        if (j, i) in allowed and (j, i) not in seen_pairs:
            seen_pairs.add((i, j))
            for t in T:
                m.addConstr(
                    gp.quicksum(x[d, i, j, t] for d in DRONES
                                if (d, i, j, t) in x)
                    + gp.quicksum(x[d, j, i, t] for d in DRONES
                                  if (d, j, i, t) in x)
                    <= 1,
                    name=f"carp_kenar_{i}_{j}_{t}"
                )

    # ── PERIYODIK DENETIM  ───────────────────────────────
    BIG_M_TAU = 1500.0
    for i in hex_ids:
        for t in T:
            ziyaret = gp.quicksum(visit[d, i, t] for d in DRONES
                                  if (d, i, t) in visit)
            m.addConstr(tau[i, t+1] <= BIG_M_TAU * (1 - ziyaret),
                        name=f"tau_reset_{i}_{t}")

    for i in hex_ids:
        for t in T:
            ziyaret = gp.quicksum(visit[d, i, t] for d in DRONES
                                  if (d, i, t) in visit)
            m.addConstr(tau[i, t+1] >= tau[i, t] + cfg.DELTA_T - BIG_M_TAU * ziyaret,
                        name=f"tau_increase_{i}_{t}")

    for i in hex_ids:
        for t in T:
            m.addConstr(tau[i, t] <= max_interval[i] + slack[i, t],
                        name=f"max_iv_{i}_{t}")

    # ── SARJ ───────────────────────────────────────────────────────────────
    for (d, s, t) in charging:
        m.addConstr(charging[d, s, t] <= at[d, s, t],
                    name=f"sarj_konum_{d}_{s}_{t}")

    for s in stn_ids:
        for t in T:
            es_zamanli = gp.quicksum(charging[d, s, t] for d in DRONES
                                     if (d, s, t) in charging)
            m.addConstr(es_zamanli <= cfg.CAP_S, name=f"soket_{s}_{t}")

    for d in DRONES:
        for s in stn_ids:
            for j in V:
                if j != s:
                    for t in T:
                        if (d, s, j, t) in x:
                            m.addConstr(cfg.E_DEP * x[d, s, j, t] <= E[d, t],
                                        name=f"dep_{d}_{s}_{j}_{t}")

    for s in stn_ids:
        for d1 in DRONES:
            for d2 in DRONES:
                if d1 == d2:
                    continue
                for t in T:
                    kalkis_d1 = gp.quicksum(x[d1, s, j, t] for j in V
                                            if j != s and (d1, s, j, t) in x)
                    giris_d2 = gp.quicksum(
                        charging[d2, s, tp]
                        for tp in range(t, min(t + cfg.S_WAIT + 1, T_LEN))
                        if (d2, s, tp) in charging
                    )
                    m.addConstr(giris_d2 <= cfg.S_WAIT * (1 - kalkis_d1),
                                name=f"swait_{s}_{d1}_{d2}_{t}")

    # ── ENERJI ─────────────────────────────────────────────────────────────
    hover_per_visit = cfg.D_HOVER * cfg.HOVER_TIME_MIN  
    for d in DRONES:
        for t in T:
            ucus = gp.quicksum(travel_energy[(i, j)] * x[d, i, j, t]
                               for (i, j) in allowed if (d, i, j, t) in x)
            hover = hover_per_visit * gp.quicksum(
                visit[d, i, t] for i in hex_ids if (d, i, t) in visit)
            sarj = (cfg.C_RATE * cfg.DELTA_T) * gp.quicksum(
                charging[d, s, t] for s in stn_ids if (d, s, t) in charging)
            m.addConstr(E[d, t+1] == E[d, t] - ucus - hover + sarj - overflow[d, t],
                        name=f"enerji_{d}_{t}")
            m.addConstr(overflow[d, t] <= sarj, name=f"ovf_ub_{d}_{t}")

    # ─────────────────────────────────────────────────────────────────────
    for d in DRONES:
        m.addConstr(at[d, home[d], T_LEN] == 1, name=f"end_home_{d}")
        if is_final_block:
            m.addConstr(E[d, T_LEN] == cfg.E_CAP, name=f"cycle_close_E_{d}")
        else:
            m.addConstr(E[d, T_LEN] >= cfg.E_BLOCK_END, name=f"end_energy_{d}")

    # ── AMAC FONKSIYONU ────────────────────────────────────────────────────
    toplam_enerji = (
        gp.quicksum(travel_energy[(i, j)] * x[d, i, j, t]
                    for (d, i, j, t) in x)
        + hover_per_visit * gp.quicksum(
            visit[d, i, t] for (d, i, t) in visit)
    )

    risk_ihlali = gp.quicksum(
        risks_dict[i] * slack[i, t]
        for i in hex_ids for t in T
    )

    ziyaret_odulu = gp.quicksum(
        risks_dict[i] * visit[d, i, t]
        for (d, i, t) in visit
    )
    EPSILON_TAU = 1e-4
    tau_toplam = gp.quicksum(tau[i, t] for i in hex_ids for t in T_PLUS)

    m.setObjective(
        cfg.ALPHA * toplam_enerji
        + cfg.BETA  * risk_ihlali
        - cfg.GAMMA * ziyaret_odulu
        + EPSILON_TAU * tau_toplam,       
        GRB.MINIMIZE
    )

    # ── COZ ────────────────────────────────────────────────────────────────
    m.optimize()

    status = m.Status
    if m.SolCount == 0:
        status_map = {
            2: "OPTIMAL",
            3: "INFEASIBLE",
            4: "INF_OR_UNBD",
            5: "UNBOUNDED",
            9: "TIME_LIMIT (cozum bulunamadi)",
            11: "INTERRUPTED",
            13: "SUBOPTIMAL",
        }
        if status == 3:
            try:
                ilp_path = os.path.join(cfg.OUTPUT_DIR,
                                        f"diag_v9_blok_{block_idx+1}_iis.ilp")
                lp_path = os.path.join(cfg.OUTPUT_DIR,
                                       f"diag_v9_blok_{block_idx+1}_full.lp")
                if not os.path.exists(ilp_path):
                    print(f"\n  [DIAG] Blok {block_idx+1} INFEASIBLE - IIS hesaplaniyor...")
                    m.computeIIS()
                    m.write(ilp_path)
                    m.write(lp_path)
                    print(f"  [DIAG] Yazildi: diag_v9_blok_{block_idx+1}_iis.ilp + .lp")

                    iis_constrs = [c for c in m.getConstrs() if c.IISConstr]
                    print(f"  [DIAG] IIS'te {len(iis_constrs)} cakisan kisit:")
                    from collections import Counter
                    kategori = Counter()
                    for c in iis_constrs:
                        cat = c.ConstrName.split("_")[0]
                        kategori[cat] += 1
                    for cat, sayi in kategori.most_common():
                        print(f"    {cat:25s}: {sayi}")
                    print(f"  [DIAG] Ilk 15 cakisan kisit:")
                    for c in iis_constrs[:15]:
                        print(f"    - {c.ConstrName}")
                    # Baslangic durumu
                    print(f"  [DIAG] Bu blok baslangic durumu:")
                    for d in DRONES:
                        print(f"    {d}: konum={start_loc[d]}, E={start_E[d]:.1f}%")
                    tau_max_5 = sorted(start_tau.items(), key=lambda x: -x[1])[:5]
                    print(f"  [DIAG] En yuksek 5 tau:")
                    for h, tv in tau_max_5:
                        print(f"    {h}: tau={tv:.1f} (max_iv={max_interval[h]:.0f})")
            except Exception as e:
                print(f"  [DIAG] IIS hesabi basarisiz: {e}")

        return {"failed": True,
                "status_code": status,
                "status_text": status_map.get(status, f"STATUS_{status}"),
                "runtime": m.Runtime}

    # ── BITIS DURUMU ───────────────────────────────────────────────────────
    end_loc = {}
    end_E   = {}
    for d in DRONES:
        for i in V:
            if at[d, i, T_LEN].X > 0.5:
                end_loc[d] = i
                break
        end_E[d] = E[d, T_LEN].X

    end_tau = {i: tau[i, T_LEN].X for i in hex_ids}

    # ── OLAYLAR ────────────────────────────────────────────────────────────
    hareketler = [(d, t, i, j) for (d, i, j, t), v in x.items()        if v.X > 0.5]
    denetimler = [(d, t, i)    for (d, i, t),    v in visit.items()    if v.X > 0.5]
    sarjlar    = [(d, t, s)    for (d, s, t),    v in charging.items() if v.X > 0.5]
    enerji     = {d: [E[d, t].X for t in T_PLUS] for d in DRONES}

    toplam_slack = sum(slack[i, t].X for i in hex_ids for t in T)
    risk_agirlikli_slack = sum(risks_dict[i] * slack[i, t].X
                               for i in hex_ids for t in T)
    ziyaret_odulu_val = sum(risks_dict[i] * visit[d, i, t].X
                            for (d, i, t) in visit)

    peak_per_station = {s: 0 for s in stn_ids}
    for t in T:
        for s in stn_ids:
            es_zamanli_t = sum(
                charging[d, s, t].X for d in DRONES
                if (d, s, t) in charging
            )
            peak_per_station[s] = max(peak_per_station[s],
                                       int(round(es_zamanli_t)))

    return {
        "obj": m.ObjVal,
        "gap": m.MIPGap,
        "runtime": m.Runtime,
        "status": status,
        "end_loc": end_loc,
        "end_E": end_E,
        "end_tau": end_tau,
        "hareketler": hareketler,
        "denetimler": denetimler,
        "sarjlar": sarjlar,
        "enerji": enerji,
        "toplam_slack": toplam_slack,
        "risk_agirlikli_slack": risk_agirlikli_slack,
        "ziyaret_odulu": ziyaret_odulu_val,
        "peak_per_station": peak_per_station,
    }

# ==============================================================================
# CIKTI: CSV
# ==============================================================================
def export_csv(cfg, results, hex_ids, stn_ids, DRONES, risks, max_interval):
    """Tum CSV ciktilarini yazar."""
    out = cfg.OUTPUT_DIR
    os.makedirs(out, exist_ok=True)
    rows = []
    for r in results:
        if r is None:
            continue
        b = r["block_idx"]
        peaks = r["peak_per_station"]
        rows.append({
            "blok": b + 1,
            "saat_baslangic": b * cfg.BLOCK_HOURS,
            "saat_bitis":     (b + 1) * cfg.BLOCK_HOURS,
            "obj":            r["obj"],
            "gap_pct":        r["gap"] * 100,
            "runtime_s":      r["runtime"],
            "hareket_say":    len(r["hareketler"]),
            "denetim_say":    len(r["denetimler"]),
            "sarj_say":       len(r["sarjlar"]),
            "toplam_slack_dk":          r["toplam_slack"],
            "risk_agirlikli_slack":     r["risk_agirlikli_slack"],
            "ziyaret_odulu":            r["ziyaret_odulu"],
            **{f"peak_{s}": peaks[s] for s in stn_ids},
        })
    pd.DataFrame(rows).to_csv(os.path.join(out, "blok_ozet.csv"),
                              index=False, encoding="utf-8")
    har_rows = []
    for r in results:
        if r is None:
            continue
        b = r["block_idx"]
        for (d, t, i, j) in r["hareketler"]:
            har_rows.append({
                "dron": d,
                "blok": b + 1,
                "t_dilim_blok_ici": t,
                "t_dakika_global": b * cfg.BLOCK_HOURS * 60 + t * cfg.DELTA_T,
                "kaynak": i,
                "hedef": j,
            })
    pd.DataFrame(har_rows).to_csv(os.path.join(out, "hareketler.csv"),
                                  index=False, encoding="utf-8")
    den_rows = []
    for r in results:
        if r is None:
            continue
        b = r["block_idx"]
        for (d, t, i) in r["denetimler"]:
            den_rows.append({
                "dron": d,
                "blok": b + 1,
                "t_dilim_blok_ici": t,
                "t_dakika_global": b * cfg.BLOCK_HOURS * 60 + t * cfg.DELTA_T,
                "hex": i,
                "risk": risks[i],
                "max_interval_dk": max_interval[i],
            })
    pd.DataFrame(den_rows).to_csv(os.path.join(out, "denetimler.csv"),
                                  index=False, encoding="utf-8")
    srj_rows = []
    for r in results:
        if r is None:
            continue
        b = r["block_idx"]
        for (d, t, s) in r["sarjlar"]:
            srj_rows.append({
                "dron": d,
                "blok": b + 1,
                "t_dilim_blok_ici": t,
                "t_dakika_global": b * cfg.BLOCK_HOURS * 60 + t * cfg.DELTA_T,
                "istasyon": s,
            })
    pd.DataFrame(srj_rows).to_csv(os.path.join(out, "sarjlar.csv"),
                                  index=False, encoding="utf-8")
    visit_per_hex = {h: 0 for h in hex_ids}
    for r in results:
        if r is None:
            continue
        for (d, t, i) in r["denetimler"]:
            visit_per_hex[i] += 1

    hex_rows = []
    for h in hex_ids:
        ziyaret_say = visit_per_hex[h]
        ort_aralik = (cfg.TOTAL_HOURS * 60 / ziyaret_say) if ziyaret_say > 0 else float("inf")
        hex_rows.append({
            "hex": h,
            "risk": risks[h],
            "max_interval_dk": max_interval[h],
            "ziyaret_sayisi": ziyaret_say,
            "ortalama_aralik_dk": ort_aralik if ort_aralik != float("inf") else "INF",
            "ihlal_riski": "VAR" if ort_aralik > max_interval[h] else "YOK",
        })
    pd.DataFrame(hex_rows).to_csv(os.path.join(out, "hex_ziyaret_ozet.csv"),
                                  index=False, encoding="utf-8")
    sok_rows = []
    for r in results:
        if r is None:
            continue
        b = r["block_idx"]
        for s in stn_ids:
            sok_rows.append({
                "blok": b + 1,
                "istasyon": s,
                "peak_blok_ici": r["peak_per_station"][s],
            })
    sok_df = pd.DataFrame(sok_rows)
    sok_df.to_csv(os.path.join(out, "soket_kullanim.csv"),
                  index=False, encoding="utf-8")
    if not sok_df.empty:
        global_peak = sok_df.groupby("istasyon")["peak_blok_ici"].max().to_dict()
    else:
        global_peak = {s: 0 for s in stn_ids}

    return visit_per_hex, global_peak


def export_block_details(cfg, results, hex_ids, DRONES):
    """Her blok icin ayri klasore detay CSV'leri yazar."""
    detay_dir = os.path.join(cfg.OUTPUT_DIR, "blok_detaylari")
    os.makedirs(detay_dir, exist_ok=True)

    for r in results:
        if r is None:
            continue
        b = r["block_idx"]
        en_rows = []
        for d in DRONES:
            for t, e_val in enumerate(r["enerji"][d]):
                en_rows.append({
                    "dron": d,
                    "t_dilim": t,
                    "t_dakika": t * cfg.DELTA_T,
                    "enerji_pct": e_val,
                })
        pd.DataFrame(en_rows).to_csv(
            os.path.join(detay_dir, f"blok_{b+1:02d}_enerji.csv"),
            index=False, encoding="utf-8"
        )

# ==============================================================================
# CIKTI: PNG
# ==============================================================================
def export_png_routes(cfg, results, coords, hex_ids, stn_ids, DRONES, risks):
    """24 saatlik plani tek figurde dron rotalari olarak gosterir."""
    fig, ax = plt.subplots(figsize=(14, 11))

    risk_vals = [risks[h] for h in hex_ids]
    rmin, rmax = min(risk_vals), max(risk_vals)
    rspan = rmax - rmin if rmax > rmin else 1.0

    for h in hex_ids:
        x, y = coords[h]
        norm = (risks[h] - rmin) / rspan
        color = plt.cm.YlOrRd(0.3 + 0.6 * norm)
        ax.scatter(x, y, c=[color], s=180, alpha=0.7, edgecolors="gray",
                   linewidths=0.5, zorder=2)
        ax.annotate(h.replace("H", ""), (x, y), fontsize=5,
                    ha="center", va="center", zorder=3)

    for s in stn_ids:
        x, y = coords[s]
        ax.scatter(x, y, c="blue", s=400, marker="s", edgecolors="black",
                   linewidths=2, zorder=4)
        ax.annotate(s, (x, y), fontsize=12, fontweight="bold",
                    ha="center", va="center", color="white", zorder=5)

    dron_colors = {"D1": "tab:red", "D2": "tab:blue", "D3": "tab:green", "D4": "tab:orange", "D5": "tab:purple", "D6": "tab:brown"}
    for d in DRONES:
        rota = []
        for r in results:
            if r is None:
                continue
            for (dd, t, i, j) in r["hareketler"]:
                if dd == d:
                    rota.append((i, j, r["block_idx"], t))
        for (i, j, b, t) in rota:
            xa, ya = coords[i]
            xb, yb = coords[j]
            ax.annotate("", xy=(xb, yb), xytext=(xa, ya),
                        arrowprops=dict(arrowstyle="->",
                                        color=dron_colors.get(d, "black"),
                                        alpha=0.45, lw=1.0),
                        zorder=1)

    legend_items = [
        Line2D([0], [0], marker="s", color="w", markerfacecolor="blue",
               markersize=12, label="Istasyon"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=plt.cm.YlOrRd(0.9),
               markersize=10, label="Yuksek risk hex"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=plt.cm.YlOrRd(0.3),
               markersize=10, label="Dusuk risk hex"),
    ]
    for d in DRONES:
        legend_items.append(
            Line2D([0], [0], color=dron_colors.get(d, "black"), lw=2,
                   label=f"{d} rotasi")
        )
    ax.legend(handles=legend_items, loc="upper left", fontsize=10)

    ax.set_title(f"Dron Rotalari - 24 Saatlik Plan ({cfg.N_BLOCKS} blok x "
                 f"{cfg.BLOCK_HOURS}h)", fontsize=14)
    ax.set_xlabel("Dogu (m)")
    ax.set_ylabel("Kuzey (m)")
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)

    out_path = os.path.join(cfg.OUTPUT_DIR, "dron_rotalari.png")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


def export_png_energy(cfg, results, DRONES):
    """Her dronun 24 saatlik enerji egrisini cizer."""
    fig, ax = plt.subplots(figsize=(14, 6))

    dron_colors = {"D1": "tab:red", "D2": "tab:blue", "D3": "tab:green", "D4": "tab:orange", "D5": "tab:purple", "D6": "tab:brown"}
    for d in DRONES:
        zincir = []
        for r in results:
            if r is None:
                continue
            b = r["block_idx"]
            offset_min = b * cfg.BLOCK_HOURS * 60
            for t, e_val in enumerate(r["enerji"][d]):
                if t == 0 and b > 0:
                    continue   # onceki blok ile cakisma
                zincir.append((offset_min + t * cfg.DELTA_T, e_val))
        if zincir:
            xs = [p[0] / 60 for p in zincir]
            ys = [p[1] for p in zincir]
            ax.plot(xs, ys, label=d, color=dron_colors.get(d, "black"),
                    lw=1.8, marker="o", markersize=2)

    ax.axhline(y=cfg.E_MIN, color="red", linestyle="--", alpha=0.4,
               label=f"E_MIN ({cfg.E_MIN}%)")
    ax.axhline(y=cfg.E_DEP, color="orange", linestyle="--", alpha=0.4,
               label=f"E_DEP ({cfg.E_DEP}%)")
    
    for b in range(1, cfg.N_BLOCKS):
        ax.axvline(x=b * cfg.BLOCK_HOURS, color="gray",
                   linestyle=":", alpha=0.3)

    ax.set_title("Dron Enerji Profili - 24 Saat", fontsize=14)
    ax.set_xlabel("Saat")
    ax.set_ylabel("Enerji (%)")
    ax.set_xlim(0, cfg.TOTAL_HOURS)
    ax.set_ylim(0, 105)
    ax.legend(loc="lower left", fontsize=10)
    ax.grid(True, alpha=0.3)

    out_path = os.path.join(cfg.OUTPUT_DIR, "enerji_profili.png")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ==============================================================================
# ANA ROLLING HORIZON DONGUSU
# ==============================================================================
def main():
    cfg = Config()

    print("=" * 78)
    print(" DRON ERKEN YANGIN TESPIT - V14 (SOLVER + MODEL OPTIMIZASYONU)")
    print(f" {cfg.TOTAL_HOURS}h plan = {cfg.N_BLOCKS} blok x {cfg.BLOCK_HOURS}h "
          f"| Rolling Horizon | YOL 1: PERIYODIK DENETIM")
    print("=" * 78)

    # Cikti dizini
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)

    # ---- Veri ------------------------------------------------------------
    coords, risks, hex_ids, stn_ids, lat_ref, lon_ref, m_per_lon = load_data(cfg)
    V      = hex_ids + stn_ids
    DRONES = list(cfg.DRONES_HOME.keys())
    home   = cfg.DRONES_HOME

    tau_step, travel_energy = compute_geometry(coords, V, cfg)
    allowed = range_filter(V, travel_energy, hex_ids, stn_ids, cfg)

    max_interval, risk_min, risk_max = compute_max_interval(risks, hex_ids, cfg)

    # ---- Bilgi -----------------------------------------------------------
    print(f"\n[Konfigurasyon]")
    print(f"  Hex sayisi   : {len(hex_ids)}")
    print(f"  Istasyon     : {len(stn_ids)} ({', '.join(stn_ids)})")
    print(f"  Dron         : {len(DRONES)} ({', '.join(DRONES)})")
    print(f"  Blok         : {cfg.N_BLOCKS} adet, her biri {cfg.BLOCK_LEN} dilim "
          f"({cfg.BLOCK_HOURS}h)")
    print(f"  Risk araligi : {risk_min:.2f} - {risk_max:.2f}")
    print(f"  CAP_S        : {cfg.CAP_S} (ust sinir, peak[s] gercek pik)")

    print(f"\n[V5: Kenar Filtreleme]")
    print(f"  K_HEX_NEIGHBORS : {cfg.K_HEX_NEIGHBORS} (her hex'ten en yakin)")
    print(f"  STATION_LIMIT   : %{cfg.STATION_LIMIT_PCT} enerji")
    print(f"  Toplam izinli kenar: {len(allowed)} "
          f"(V4'te limit_pct=2.0 -> 1992 kenar; V5 ~4x daha kucuk)")

    print(f"\n[V11: Cevrim Kapanisi]")
    print(f"  HER blok sonu kendi istasyonunda olmak zorunlu")
    print(f"  Ara bloklar    : E >= {cfg.E_BLOCK_END}% (esnek)")
    print(f"  V12 SON blok   : E == {cfg.E_CAP}% (esitlik, 24h cevrim kapanisi)")

    print(f"\n[V12 KRITIK DUZELTMELER - KORUNDU]")
    print(f"  Hover enerjisi : D_HOVER * HOVER_TIME_MIN = "
          f"{cfg.D_HOVER}*{cfg.HOVER_TIME_MIN} = %{cfg.D_HOVER*cfg.HOVER_TIME_MIN:.1f}/ziyaret")
    print(f"                   (V11: D_HOVER * DELTA_T = %{cfg.D_HOVER*cfg.DELTA_T:.1f} -> 10x BUG)")
    print(f"  Cevrim kapanisi: SON blok E == E_CAP (formulasyon 5.7.1)")

    print(f"\n[V13 KAPASITE KALIBRASYONU - KORUNDU]")
    print(f"  BLOCK_HOURS    : {cfg.BLOCK_HOURS}h ({cfg.BLOCK_LEN} dilim/blok)")
    print(f"  MAX_INTV_HIGH  : {cfg.MAX_INTERVAL_HIGH} dk")

    print(f"\n[V14 SOLVER + MODEL OPTIMIZASYONU]")
    print(f"  K_HEX_NEIGHBORS: {cfg.K_HEX_NEIGHBORS} (V13: 10, model %20 kucuk)")
    print(f"  MIP_GAP        : %{cfg.MIP_GAP*100:.0f} (V13: %10)")
    print(f"  TIME_LIMIT     : {cfg.TIME_LIMIT_PER_BLOCK}s (V13: 600s)")
    print(f"  MIP_FOCUS      : {cfg.MIP_FOCUS} (V13: 1, optimaliteye odaklan)")
    print(f"  HEURISTICS     : %{cfg.HEURISTICS_PCT*100:.0f} (V13: %20)")

    print(f"\n[Periyodik Denetim Parametreleri (V7)]")
    print(f"  En yuksek risk (R={risk_max:.2f}) -> max {cfg.MAX_INTERVAL_HIGH} dk")
    print(f"  En dusuk risk  (R={risk_min:.2f}) -> max {cfg.MAX_INTERVAL_LOW} dk")
    print(f"  TAU UST SINIRSIZ (V6'daki upper-bound infeasibility duzeltildi)")

    print(f"\n[Amac Fonksiyonu]")
    print(f"  min  {cfg.ALPHA}*enerji + {cfg.BETA}*risk*slack - {cfg.GAMMA}*risk*visit")

    print(f"\n[Gurobi]")
    print(f"  Limit/blok: {cfg.TIME_LIMIT_PER_BLOCK}s, MIP gap: %{cfg.MIP_GAP*100:.0f}")

    # ---- Baslangic durumu ------------------------------------------------
    current_loc = {d: home[d] for d in DRONES}
    current_E   = {d: cfg.E_CAP for d in DRONES}
    current_tau = {i: 0.0 for i in hex_ids}

    total_results = []
    total_start = timer.time()
    recovery_count = 0  
    recovery_blocks = [] 

    print(f"\n{'-' * 78}")
    print(f" {'Blok':>5} {'Saat':>14} {'Obj':>10} {'Gap':>7} {'Sure':>7}  "
          f"Konum/Enerji ozet")
    print(f"{'-' * 78}")

    for b in range(cfg.N_BLOCKS):
        is_final = (b == cfg.N_BLOCKS - 1)
        saat_s   = b * cfg.BLOCK_HOURS
        saat_e   = (b + 1) * cfg.BLOCK_HOURS

        try:
            result = solve_block(
                cfg, V, hex_ids, stn_ids, DRONES, home,
                tau_step, travel_energy, allowed,
                current_loc, current_E, current_tau,
                risks, max_interval,
                is_final, b
            )
        except Exception as e:
            print(f"  [HATA] Blok {b+1}: {e}")
            result = None

        if result is None or result.get("failed", False):
            if result and result.get("failed"):
                status_text = result.get("status_text", "?")
                rt = result.get("runtime", 0)
                print(f"  {b+1:>3}/{cfg.N_BLOCKS:<2} {saat_s:>2}:00-{saat_e:>2}:00"
                      f"   COZUMSUZ [{status_text}] ({rt:.1f}s)")
            else:
                print(f"  {b+1:>3}/{cfg.N_BLOCKS:<2} {saat_s:>2}:00-{saat_e:>2}:00"
                      f"   HATA - blok atlandi")

      
            recovery_count += 1
            recovery_blocks.append(b + 1)
            current_loc = {d: home[d] for d in DRONES}
            current_E   = {d: cfg.E_CAP for d in DRONES}
      
            current_tau = {
                i: min(current_tau[i] + cfg.BLOCK_LEN * cfg.DELTA_T, 1400.0)
                for i in current_tau
            }
            continue

  
        current_loc = result["end_loc"]
        current_E   = result["end_E"]
      
        current_tau = {i: min(result["end_tau"][i], 1400.0)
                       for i in result["end_tau"]}

        konum_str = " ".join(
            f"{d}:{result['end_loc'][d]}({result['end_E'][d]:.0f}%)"
            for d in DRONES
        )
        print(f"  {b+1:>3}/{cfg.N_BLOCKS:<2} "
              f"{saat_s:>2}:00-{saat_e:>2}:00 "
              f"{result['obj']:>9.2f} "
              f"{result['gap']*100:>5.1f}% "
              f"{result['runtime']:>5.1f}s  "
              f"{konum_str[:40]}")

        result["block_idx"] = b
        total_results.append(result)

    total_runtime = timer.time() - total_start

    # ==========================================================================
    # KONSOLIDE RAPOR
    # ==========================================================================
    print(f"\n{'=' * 78}")
    print(" 24 SAATLIK PLAN OZETI")
    print(f"{'=' * 78}")

    if not total_results:
        print("  Hicbir blok cozulmedi. Konfigurasyonu gozden gecirin.")
        return

    toplam_obj  = sum(r["obj"]                  for r in total_results)
    toplam_har  = sum(len(r["hareketler"])      for r in total_results)
    toplam_den  = sum(len(r["denetimler"])      for r in total_results)
    toplam_srj  = sum(len(r["sarjlar"])         for r in total_results)
    toplam_slk  = sum(r["toplam_slack"]         for r in total_results)
    toplam_rsl  = sum(r["risk_agirlikli_slack"] for r in total_results)
    toplam_zod  = sum(r["ziyaret_odulu"]        for r in total_results)

    visited_unique = set()
    visit_per_hex  = {h: 0 for h in hex_ids}
    for r in total_results:
        for (d, t, i) in r["denetimler"]:
            visited_unique.add(i)
            visit_per_hex[i] += 1

    global_peak = {s: 0 for s in stn_ids}
    for r in total_results:
        for s in stn_ids:
            global_peak[s] = max(global_peak[s], r["peak_per_station"][s])

    ihlal_eden_hex = []
    for h in hex_ids:
        zs = visit_per_hex[h]
        if zs == 0:
            ort = float("inf")
        else:
            ort = cfg.TOTAL_HOURS * 60 / zs
        if ort > max_interval[h]:
            ihlal_eden_hex.append((h, risks[h], max_interval[h], zs, ort))

    print(f"  Toplam obj         : {toplam_obj:.2f}")
    print(f"  Toplam hareket     : {toplam_har}")
    print(f"  Toplam denetim     : {toplam_den}")
    print(f"  Toplam sarj olayi  : {toplam_srj}")
    print(f"  Benzersiz hex      : {len(visited_unique)}/{len(hex_ids)} "
          f"(%{100*len(visited_unique)/len(hex_ids):.1f})")
    print(f"  Toplam slack       : {toplam_slk:.1f} dk")
    print(f"  Risk-agir. slack   : {toplam_rsl:.2f}")
    print(f"  Ziyaret odulu top. : {toplam_zod:.2f}")
    print(f"  Toplam sure        : {total_runtime:.1f} s "
          f"({total_runtime/60:.1f} dk)")

    if recovery_count == 0:
        print(f"  Recovery tetigi    : 0 (tum bloklar feasible) ✓")
    else:
        print(f"  Recovery tetigi    : {recovery_count}/{cfg.N_BLOCKS} blok "
              f"COZUMSUZ (palyatif uygulandi)")
        print(f"    Cozumsuz bloklar : {recovery_blocks}")
        print(f"    UYARI: Recovery palyatifi modeli kandiriyor (isinlama).")
        print(f"           Tezde 'X blok cozulemedi' olarak rapor edilmeli.")

    print(f"\n  Hex bazli ziyaret dagilimi:")
    if visit_per_hex:
        zs_vals = sorted(visit_per_hex.values())
        print(f"    Min  : {min(zs_vals)}")
        print(f"    Med  : {zs_vals[len(zs_vals)//2]}")
        print(f"    Max  : {max(zs_vals)}")
        en_cok = max(visit_per_hex, key=visit_per_hex.get)
        en_az  = min(visit_per_hex, key=visit_per_hex.get)
        print(f"    En cok: {en_cok} ({visit_per_hex[en_cok]} kez, "
              f"R={risks[en_cok]:.2f})")
        print(f"    En az : {en_az} ({visit_per_hex[en_az]} kez, "
              f"R={risks[en_az]:.2f})")

    print(f"\n  max_interval ihlali (kabaca ortalama bazli):")
    print(f"    Ihlal eden hex sayisi: {len(ihlal_eden_hex)}/{len(hex_ids)}")
    if ihlal_eden_hex:
        print(f"    Ilk 5 ihlal:")
        for (h, r_v, mi, zs, ort) in ihlal_eden_hex[:5]:
            ort_str = f"{ort:.0f}" if ort != float("inf") else "INF"
            print(f"      {h}: R={r_v:.2f}, max_iv={mi:.0f}dk, "
                  f"ziy={zs}, ort_aralik={ort_str}dk")

    print(f"\n  Istasyon pik kullanim (CAP_S={cfg.CAP_S}):")
    for s in stn_ids:
        print(f"    {s}: global pik = {global_peak[s]:.0f} es-zamanli sarj")
    print(f"  -> Onerilen soket sayisi (340 hex tam olcekte degerlendirilecek):")
    for s in stn_ids:
        print(f"    {s}: {int(global_peak[s] + 1)} (pik + 1 yedek)")

    # ==========================================================================
    # CIKTI DOSYALARI
    # ==========================================================================
    print(f"\n{'=' * 78}")
    print(" CIKTI DOSYALARI")
    print(f"{'=' * 78}")

    if cfg.EXPORT_CSV:
        export_csv(cfg, total_results, hex_ids, stn_ids, DRONES,
                   risks, max_interval)
        print(f"  [CSV] blok_ozet.csv, hareketler.csv, denetimler.csv,")
        print(f"        sarjlar.csv, hex_ziyaret_ozet.csv, soket_kullanim.csv")

    if cfg.EXPORT_BLOCK_DETAILS:
        export_block_details(cfg, total_results, hex_ids, DRONES)
        print(f"  [DETAY] blok_detaylari/blok_NN_enerji.csv (her blok icin)")

    if cfg.EXPORT_PNG:
        try:
            p1 = export_png_routes(cfg, total_results, coords, hex_ids,
                                    stn_ids, DRONES, risks)
            print(f"  [PNG] dron_rotalari.png")
        except Exception as e:
            print(f"  [PNG] dron_rotalari.png HATASI: {e}")
        try:
            p2 = export_png_energy(cfg, total_results, DRONES)
            print(f"  [PNG] enerji_profili.png")
        except Exception as e:
            print(f"  [PNG] enerji_profili.png HATASI: {e}")

    print(f"\n  Cikti dizini: {cfg.OUTPUT_DIR}")
    print(f"{'=' * 78}\n")


if __name__ == "__main__":
    main()