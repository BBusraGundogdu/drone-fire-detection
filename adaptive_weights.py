# -*- coding: utf-8 -*-
"""
================================================================================
ADAPTIVE_WEIGHTS.PY  --  Operatör Ağırlık Yönetimi (Day 3 / parca 4)
================================================================================
Amac:
  ALNS'in 'A'si (Adaptive). Operatorlerin gecmis performansina gore
  agirliklarini guncelle. Iyi olan operatorler sonraki iterasyonlarda
  daha sik secilir (roulette wheel).
  
Mekanizma (Ropke & Pisinger 2006 standart):
  Her operator icin agirlik w_i, kullanim sayisi n_i, toplam skor s_i tutulur.
  Her iterasyon sonunda operatorun skoru artar:
    - Yeni en iyi cozum bulundu     -> skor += sigma_1 (33)
    - Yeni iyi (gecerli'den iyi)    -> skor += sigma_2 (13)
    - Kotu kabul edildi (SA)        -> skor += sigma_3 (9)
    - Hicbir sey olmadi             -> skor += 0
  
  Her 100 iterasyonda agirliklari guncelle:
    w_new = (1 - lambda) * w_old + lambda * (s_i / n_i)
  
  Operator secimi: roulette wheel agirlik baz alinarak
================================================================================
"""

import random


class AdaptiveWeights:
    """
    Operator agirlik yonetimi + roulette wheel secimi.
    
    Kullanim:
        aw = AdaptiveWeights(["random", "worst", "spatial", "time_window"])
        op = aw.select_destroy(rng)        # operator ismini ver
        # ... destroy/repair calistir ...
        aw.update_destroy(op, "new_best")   # skor guncelle
        aw.normalize_weights()              # periyodik (her 100 iter)
    """
    
    def __init__(self, op_names, sigma_1=33, sigma_2=13, sigma_3=9, 
                 reaction_factor=0.5):
        """
        Parametreler:
            op_names: ["random", "worst", ...] operator isim listesi
            sigma_1: yeni global en iyi cozum bulundu - max odul
            sigma_2: kabul edilen yeni cozum (improvement)
            sigma_3: SA ile kabul edilen kotu cozum
            reaction_factor: lambda - agirlik adaptasyon hizi (0..1)
        """
        self.op_names = list(op_names)
        self.weights = {n: 1.0 for n in self.op_names}    # baslangic esit
        self.scores = {n: 0.0 for n in self.op_names}     # bu segmentteki skorlar
        self.uses = {n: 0 for n in self.op_names}         # kullanim sayisi
        self.total_uses = {n: 0 for n in self.op_names}   # global kullanim
        
        # Performans icin uzun-vadeli istatistikler
        self.history_scores = {n: [] for n in self.op_names}
        self.history_weights = {n: [] for n in self.op_names}
        
        self.sigma_1 = sigma_1
        self.sigma_2 = sigma_2
        self.sigma_3 = sigma_3
        self.lambda_ = reaction_factor
    
    def select(self, rng):
        """Roulette wheel ile bir operator sec."""
        weights = [self.weights[n] for n in self.op_names]
        chosen = rng.choices(self.op_names, weights=weights, k=1)[0]
        return chosen
    
    def update(self, op_name, outcome):
        """
        Bir operator kullanildiktan sonra cagrilir.
        
        outcome: "new_best" | "improvement" | "accepted_worse" | "rejected"
        """
        self.uses[op_name] += 1
        self.total_uses[op_name] += 1
        
        if outcome == "new_best":
            self.scores[op_name] += self.sigma_1
        elif outcome == "improvement":
            self.scores[op_name] += self.sigma_2
        elif outcome == "accepted_worse":
            self.scores[op_name] += self.sigma_3
        # rejected: 0 skor

    def normalize_weights(self):
        """
        Her segment sonunda cagrilir (orn. her 100 iterasyon).
        Agirliklari skor performansina gore guncelle ve sifirla.
        
        w_new = (1 - lambda) * w_old + lambda * (s_i / n_i)
        """
        for name in self.op_names:
            old_w = self.weights[name]
            if self.uses[name] > 0:
                avg_score = self.scores[name] / self.uses[name]
                new_w = (1 - self.lambda_) * old_w + self.lambda_ * avg_score
            else:
                # Hic kullanilmamis: agirlik dusur (ama 0 yapma, kesfedilebilirlik)
                new_w = old_w * 0.5
            
            # Minimum agirlik koru (operator hic kullanilmaz hale gelmesin)
            self.weights[name] = max(0.1, new_w)
            
            # Tarihce
            self.history_scores[name].append(self.scores[name])
            self.history_weights[name].append(self.weights[name])
        
        # Skor ve kullanim sifirla
        self.scores = {n: 0.0 for n in self.op_names}
        self.uses = {n: 0 for n in self.op_names}
    
    def get_stats(self):
        return {
            "weights": dict(self.weights),
            "total_uses": dict(self.total_uses),
            "history_scores": {k: list(v) for k, v in self.history_scores.items()},
            "history_weights": {k: list(v) for k, v in self.history_weights.items()},
        }
    
    def __repr__(self):
        parts = []
        for name in self.op_names:
            parts.append(f"{name}={self.weights[name]:.2f}")
        return f"AdaptiveWeights({', '.join(parts)})"
