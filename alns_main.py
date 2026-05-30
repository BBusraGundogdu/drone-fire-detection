# -*- coding: utf-8 -*-
"""
================================================================================
ALNS_MAIN.PY  --  ALNS Ana Cevrimi (Day 3 / parca 5)
================================================================================
Amac:
  Tum bilesenleri birlestirip ALNS'i koşturan ana script.
  
  Akis:
    1. Warm-start (V25'ten Solution)
    2. ALNS iterasyon dongusu:
       a) Destroy operator sec (roulette)
       b) Repair operator sec (roulette)
       c) Yeni cozumu olustur (destroy + repair)
       d) Feasibility kontrolu (hard)
       e) Objektif degerlendir
       f) Simulated Annealing kabul karari
       g) En iyi cozumu izle
       h) Adaptive weights guncelle
    3. Sonuc raporu + CSV ciktilari

Parametreler (config.py'de yok, burada hardcoded):
  - MAX_ITERATIONS: 1000 (test icin, gerceginde 5000-10000)
  - DESTROY_RATE: 0.15 (cozumun %15'i)
  - REHEATING_THRESHOLD: 200 iter iyilesme yoksa T arttir
  - SEGMENT_LENGTH: 100 iter sonunda agirliklari normalize et
================================================================================
"""

import os
import sys
import time
import random
import argparse
import pandas as pd
from copy import deepcopy

# Lokal modul'leri import et
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warm_start import load_v25_warmstart
from destroy_ops import DESTROY_OPERATORS, get_destroy_op
from repair_ops import REPAIR_OPERATORS, get_repair_op
from acceptance import SimulatedAnnealing
from adaptive_weights import AdaptiveWeights


# ==============================================================================
# KONFIGURASYON
# ==============================================================================
class ALNSConfig:
    MAX_ITERATIONS = 3000           # 1000'den 3000'e cikti (yeterli convergence)
    TIME_LIMIT_SECONDS = 7200       # 2 saat (V19 kuralina uyum)
    DESTROY_RATE = 0.15             # Cozumun %15'i bozulur
    REHEATING_THRESHOLD = 300       # 300 iter iyilesme yoksa reheat (yeni)
    SEGMENT_LENGTH = 100            # Her 100 iter agirliklari normalize
    
    # SA parametreleri 
    # T0 onceki kosumda 23M idi (cok yuksek), simdi 4.6M (obj'nin %1'i)
    # Bu sayede SA daha hizli cool olur, exploitation daha erken baslar
    SA_T0_FACTOR = 0.01             # 0.05 -> 0.01 (T_init 5x dustu)
    SA_COOLING_RATE = 0.998         # 0.9975 -> 0.998 (biraz hizli soguma)
    SA_MIN_T = 1.0
    
    # Adaptive weights
    AW_SIGMA_1 = 33    # new global best
    AW_SIGMA_2 = 13    # improvement
    AW_SIGMA_3 = 9     # worse accepted
    AW_REACTION = 0.5  # lambda
    
    # Operator listesi
    DESTROY_OPS = ["random", "worst", "spatial", "time_window"]
    REPAIR_OPS = ["greedy", "regret", "risk_priority", "random"]
    
    # Random seed (reproducibility)
    SEED = 42
    
    # Cikti
    VERBOSE = True
    REPORT_INTERVAL = 100   # Her 100 iter konsol cikitisi
    SAVE_RESULTS = True


# ==============================================================================
# ANA ALNS DONGUSU
# ==============================================================================
def run_alns(initial_solution, cfg=None, verbose=True):
    """
    ALNS koşumunu yur.
    
    Args:
        initial_solution: Solution objesi (warm-start)
        cfg: ALNSConfig objesi
        verbose: konsol ciktisi
    
    Returns:
        best_solution: en iyi bulunan cozum
        history: dict (iteration, current_obj, best_obj, ...)
    """
    if cfg is None:
        cfg = ALNSConfig()
    
    rng = random.Random(cfg.SEED)
    
    # Cozumler
    current = initial_solution
    best = current.copy()
    
    current_obj = current.evaluate()
    best_obj = current_obj
    initial_obj = current_obj
    
    # Bilesenler
    sa = SimulatedAnnealing(
        T0=initial_obj * cfg.SA_T0_FACTOR,
        cooling_rate=cfg.SA_COOLING_RATE,
        min_T=cfg.SA_MIN_T,
        reheating_threshold=cfg.REHEATING_THRESHOLD,
        rng=rng,
    )
    
    aw_destroy = AdaptiveWeights(
        cfg.DESTROY_OPS,
        sigma_1=cfg.AW_SIGMA_1, sigma_2=cfg.AW_SIGMA_2, sigma_3=cfg.AW_SIGMA_3,
        reaction_factor=cfg.AW_REACTION,
    )
    aw_repair = AdaptiveWeights(
        cfg.REPAIR_OPS,
        sigma_1=cfg.AW_SIGMA_1, sigma_2=cfg.AW_SIGMA_2, sigma_3=cfg.AW_SIGMA_3,
        reaction_factor=cfg.AW_REACTION,
    )
    
    # History
    history = {
        "iteration": [], "current_obj": [], "best_obj": [], "temperature": [],
        "destroy_op": [], "repair_op": [], "outcome": [], "duration_s": [],
        "coverage_pct": [],
    }
    
    if verbose:
        print("="*78)
        print(" ALNS KOSUM BAŞLIYOR")
        print("="*78)
        print(f"  Baslangic obj : {initial_obj:.0f}")
        print(f"  T_init        : {sa.T:.0f}")
        print(f"  Max iter      : {cfg.MAX_ITERATIONS}")
        print(f"  Time limit    : {cfg.TIME_LIMIT_SECONDS}s ({cfg.TIME_LIMIT_SECONDS/60:.1f}dk)")
        print(f"  Destroy rate  : {cfg.DESTROY_RATE}")
        print()
        print(f"{'Iter':>6} {'Current':>12} {'Best':>12} {'T':>9} {'D-op':>14} {'R-op':>14} {'Outcome':>14} {'Cov%':>6}")
        print("-"*100)
    
    t_start = time.time()
    
    for it in range(cfg.MAX_ITERATIONS):
        elapsed = time.time() - t_start
        if elapsed > cfg.TIME_LIMIT_SECONDS:
            if verbose:
                print(f"\n  [TIME LIMIT] {elapsed:.0f}s gecti, durduruluyor.")
            break
        
        iter_t0 = time.time()
        
        # 1. Operator sec
        d_name = aw_destroy.select(rng)
        r_name = aw_repair.select(rng)
        
        # 2. Cozumu kopyala ve destroy/repair uygula
        candidate = current.copy()
        destroy_op = get_destroy_op(d_name)
        destroyed = destroy_op(candidate, destroy_rate=cfg.DESTROY_RATE, rng=rng)
        
        repair_op = get_repair_op(r_name)
        inserted = repair_op(candidate, destroyed_list=destroyed, rng=rng)
        
        # 3. Obj degerlendir
        candidate_obj = candidate.evaluate()
        
        # 4. SA kabul kriteri
        outcome = "rejected"
        accepted = sa.accept(candidate_obj, current_obj)
        
        if accepted:
            if candidate_obj < best_obj:
                # Yeni global en iyi
                best = candidate.copy()
                best_obj = candidate_obj
                outcome = "new_best"
            elif candidate_obj < current_obj:
                outcome = "improvement"
            else:
                outcome = "accepted_worse"
            current = candidate
            current_obj = candidate_obj
        
        # 5. Adaptive weights guncelle
        aw_destroy.update(d_name, outcome)
        aw_repair.update(r_name, outcome)
        
        # 6. Periyodik agirlik normalizasyonu
        if (it + 1) % cfg.SEGMENT_LENGTH == 0:
            aw_destroy.normalize_weights()
            aw_repair.normalize_weights()
        
        # 7. SA cooling
        sa.cool()
        
        # 8. History kaydet
        iter_dur = time.time() - iter_t0
        cov = candidate.get_kpis()["coverage_pct"]
        history["iteration"].append(it)
        history["current_obj"].append(current_obj)
        history["best_obj"].append(best_obj)
        history["temperature"].append(sa.T)
        history["destroy_op"].append(d_name)
        history["repair_op"].append(r_name)
        history["outcome"].append(outcome)
        history["duration_s"].append(iter_dur)
        history["coverage_pct"].append(cov)
        
        # 9. Konsol ciktisi
        if verbose and (it % cfg.REPORT_INTERVAL == 0 or outcome == "new_best"):
            print(f"{it:>6} {current_obj:>12.0f} {best_obj:>12.0f} {sa.T:>9.1f} "
                  f"{d_name:>14} {r_name:>14} {outcome:>14} {cov:>5.1f}%")
    
    # Final raporu
    total_time = time.time() - t_start
    if verbose:
        print()
        print("="*78)
        print(" ALNS KOSUM SONLANDI")
        print("="*78)
        print(f"  Toplam iter   : {len(history['iteration'])}")
        print(f"  Toplam sure   : {total_time:.1f}s ({total_time/60:.1f}dk)")
        print(f"  Baslangic obj : {initial_obj:.0f}")
        print(f"  Final obj     : {best_obj:.0f}")
        print(f"  Iyilesme      : {100*(initial_obj-best_obj)/initial_obj:.1f}%")
        print()
        sa_stats = sa.get_stats()
        print(f"  SA istatistikleri:")
        print(f"    Kabul orani         : {sa_stats['accept_rate']:.1%}")
        print(f"    Iyilesme bulundu    : {sa_stats['n_improvement']}")
        print(f"    Kotu kabul          : {sa_stats['n_worse_accepted']}")
        print(f"    Reheating sayisi    : {sa_stats['n_reheats']}")
        print()
        print(f"  Final operator agirliklari:")
        print(f"    Destroy: {aw_destroy}")
        print(f"    Repair : {aw_repair}")
        print()
        kpis = best.get_kpis()
        print(f"  Final cozum:")
        print(f"    Kapsama : {kpis['coverage_pct']:.1f}%")
        print(f"    Ihlal   : {kpis['violations']}")
        print(f"    Slack   : {kpis['slack_total']:.0f}")
        for z, zc in kpis["zone_coverage"].items():
            print(f"    {z}: {zc['visited']}/{zc['total']} ({zc['pct']:.1f}%)")
    
    return best, history, aw_destroy, aw_repair, sa


# ==============================================================================
# CLI
# ==============================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate-dir", default=None)
    parser.add_argument("--hex-csv", default=None)
    parser.add_argument("--zone-csv-dir", default=None)
    parser.add_argument("--max-iter", type=int, default=1000)
    parser.add_argument("--time-limit", type=int, default=7200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report-interval", type=int, default=50)
    args = parser.parse_args()
    
    cfg = ALNSConfig()
    cfg.MAX_ITERATIONS = args.max_iter
    cfg.TIME_LIMIT_SECONDS = args.time_limit
    cfg.SEED = args.seed
    cfg.REPORT_INTERVAL = args.report_interval
    
    # Warm-start
    sol = load_v25_warmstart(
        aggregate_dir=args.aggregate_dir,
        hex_csv=args.hex_csv,
        zone_csv_dir=args.zone_csv_dir,
        verbose=True,
    )
    
    # ALNS koş
    best, history, aw_d, aw_r, sa = run_alns(sol, cfg, verbose=True)
    
    # Output dizini
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "alns_output")
    os.makedirs(output_dir, exist_ok=True)
    
    # History CSV
    pd.DataFrame(history).to_csv(
        os.path.join(output_dir, "alns_history.csv"),
        index=False
    )
    
    # Best cozumden hareketler + denetimler cikar
    har_rows = []
    den_rows = []
    for dkey, info in best.drones.items():
        zone, drone_id = dkey
        for t in range(best.cfg.T_TOTAL):
            if info["actions"][t] == "move":
                t_global = t * best.cfg.DELTA_T
                blok = t // best.cfg.BLOCK_LEN + 1
                t_blok = t % best.cfg.BLOCK_LEN
                src = info["schedule"][t]
                dst = info["schedule"][t + 1] if t + 1 < len(info["schedule"]) else src
                har_rows.append({
                    "zone": zone, "dron": drone_id, "blok": blok,
                    "t_dilim_blok_ici": t_blok, "t_dakika_global": t_global,
                    "kaynak": src, "hedef": dst,
                })
            elif info["actions"][t] == "visit":
                hex_id = info["schedule"][t]
                if hex_id in best.hex_info:
                    t_global = t * best.cfg.DELTA_T
                    blok = t // best.cfg.BLOCK_LEN + 1
                    t_blok = t % best.cfg.BLOCK_LEN
                    den_rows.append({
                        "zone": zone, "dron": drone_id, "blok": blok,
                        "t_dilim_blok_ici": t_blok, "t_dakika_global": t_global,
                        "hex": hex_id,
                        "risk": best.hex_info[hex_id]["risk"],
                        "max_interval_dk": best.hex_info[hex_id]["max_interval"],
                    })
    
    pd.DataFrame(har_rows).to_csv(os.path.join(output_dir, "alns_hareketler.csv"),
                                    index=False)
    pd.DataFrame(den_rows).to_csv(os.path.join(output_dir, "alns_denetimler.csv"),
                                    index=False)
    
    # KPI ozeti
    kpis = best.get_kpis()
    with open(os.path.join(output_dir, "alns_summary.txt"), "w", encoding="utf-8") as f:
        f.write("ALNS KOSUM SONUC RAPORU\n")
        f.write("=" * 78 + "\n\n")
        f.write(f"Toplam iter   : {len(history['iteration'])}\n")
        f.write(f"Kapsama       : {kpis['coverage_pct']:.1f}%\n")
        f.write(f"Ihlal         : {kpis['violations']}\n")
        f.write(f"Slack toplam  : {kpis['slack_total']:.0f}\n")
        f.write(f"Objektif      : {kpis['objective']:.0f}\n")
        f.write(f"\nZone bazli:\n")
        for z, zc in kpis["zone_coverage"].items():
            f.write(f"  {z}: {zc['visited']}/{zc['total']} ({zc['pct']:.1f}%)\n")
    
    print(f"\n[CIKTILAR] {output_dir}\\")
    print(f"  alns_history.csv")
    print(f"  alns_hareketler.csv")
    print(f"  alns_denetimler.csv")
    print(f"  alns_summary.txt")


if __name__ == "__main__":
    main()
