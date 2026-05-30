# -*- coding: utf-8 -*-
"""
================================================================================
ACCEPTANCE.PY  --  Simulated Annealing Kabul Kriteri (Day 3 / parca 3)
================================================================================
Amac:
  ALNS'te her iterasyonun sonunda yeni cozumu KABUL et veya REDDET et.
  
  Simulated Annealing mantigi:
  - Iyi cozum (obj < current): her zaman kabul et
  - Kotu cozum: bazen kabul et, exp(-deltaE/T) olasiligiyla
  - T (sicaklik) yavasca duser: cooling_rate
  
  Bu mekanizma lokal optimumdan kacma imkani saglar.

Tasarim:
  - Cooling: geometrik (T_new = T_old * cooling_rate)
  - Reheating: 1000 iterasyonda iyilesme yoksa T'yi yari yari arttir
  - Kabul orani izlenir: ALNS analizi icin
================================================================================
"""

import math
import random


class SimulatedAnnealing:
    """
    SA kabul kriteri + sicaklik yonetimi.
    
    Kullanim:
        sa = SimulatedAnnealing(T0=initial_objective * 0.05)
        if sa.accept(new_obj, current_obj):
            current = new
        sa.cool()
    """
    
    def __init__(self, T0=1000.0, cooling_rate=0.9975, 
                 min_T=1.0, reheating_threshold=1000, rng=None):
        """
        Parametreler:
            T0: Baslangic sicakligi
            cooling_rate: Her iterasyonda T = T * cooling_rate
            min_T: Minimum sicaklik (asla altina dusmez)
            reheating_threshold: Bu kadar iyilesme olmazsa T yari yariya yukselt
            rng: Random generator
        """
        self.T = T0
        self.T0 = T0
        self.cooling_rate = cooling_rate
        self.min_T = min_T
        self.reheating_threshold = reheating_threshold
        self.iterations_without_improvement = 0
        self.rng = rng if rng else random.Random()
        
        # Istatistikler
        self.n_accept = 0
        self.n_reject = 0
        self.n_improvement = 0
        self.n_worse_accepted = 0
        self.n_reheats = 0

    def accept(self, new_obj, current_obj):
        """
        Yeni cozumu kabul et veya reddet.
        
        Donder: True (kabul), False (red)
        """
        delta = new_obj - current_obj
        
        if delta < 0:
            # Iyilesme: her zaman kabul
            self.n_accept += 1
            self.n_improvement += 1
            self.iterations_without_improvement = 0
            return True
        
        if delta == 0:
            # Esit obj, kabul et (diversification icin)
            self.n_accept += 1
            return True
        
        # Kotu cozum: probabilistik kabul
        prob = math.exp(-delta / max(self.T, 1e-9))
        if self.rng.random() < prob:
            self.n_accept += 1
            self.n_worse_accepted += 1
            self.iterations_without_improvement += 1
            return True
        
        self.n_reject += 1
        self.iterations_without_improvement += 1
        return False

    def cool(self):
        """T'yi azalt (geometrik soguma)."""
        self.T = max(self.min_T, self.T * self.cooling_rate)
        
        # Reheating: cok iyilesme olmuyorsa T'yi arttir
        if self.iterations_without_improvement >= self.reheating_threshold:
            self.T = max(self.T * 2, self.T0 * 0.5)
            self.iterations_without_improvement = 0
            self.n_reheats += 1

    def get_stats(self):
        total = self.n_accept + self.n_reject
        return {
            "T_current": self.T,
            "T_initial": self.T0,
            "n_accept": self.n_accept,
            "n_reject": self.n_reject,
            "n_improvement": self.n_improvement,
            "n_worse_accepted": self.n_worse_accepted,
            "n_reheats": self.n_reheats,
            "accept_rate": self.n_accept / max(1, total),
            "improvement_rate": self.n_improvement / max(1, total),
        }
    
    def reset_T(self):
        """Sicakligi sifirla (yeni koşum icin)."""
        self.T = self.T0
        self.iterations_without_improvement = 0
