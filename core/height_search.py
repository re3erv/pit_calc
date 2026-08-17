# height_search.py
import numpy as np
import math
# Внутри core/height_search.py
from .mesh import Mesh
from .volume_core import compute_volume_by_contour

class VolumeCache:
    """Кэш для хранения результатов вычисления объёма."""
    def __init__(self, precision=None):
        self.cache = {}
        self.hits = 0
        self.misses = 0
        self.current_file = None

    def set_file(self, file_path):
        if self.current_file != file_path:
            self.cache = {}
            self.hits = 0
            self.misses = 0
            self.current_file = file_path

    def _key(self, z_level):
        return round(float(z_level), 9)

    def get(self, z_level):
        key = self._key(z_level)
        result = self.cache.get(key)
        if result is not None:
            self.hits += 1
        return result

    def set(self, z_level, volume):
        key = self._key(z_level)
        self.cache[key] = volume

    def get_or_compute(self, z_level, compute_func):
        cached = self.get(z_level)
        if cached is not None:
            return cached, True
        self.misses += 1
        volume = compute_func()
        self.set(z_level, volume)
        return volume, False

    def stats(self):
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0.0
        return (f"Кэш: {len(self.cache)} записей, попаданий: {self.hits}, "
                f"промахов: {self.misses}, hit rate: {hit_rate:.1f}%")


class HeightSearch:
    """
    Поиск высоты по заданному объёму.
    Остановка:
      - если задана volume_tol и |V - target| ≤ volume_tol, то останавливаемся;
      - иначе, когда ширина интервала ≤ height_tol.
    """
    def __init__(self, mesh: Mesh, target_volumes, volume_cache=None,
                 height_tol=0.001, volume_tol=None, progress_callback=None):
        self.mesh = mesh
        self.targets = target_volumes
        self.cache = volume_cache if volume_cache else VolumeCache()
        self.height_tol = height_tol if height_tol is not None and height_tol > 0 else 0.001
        self.volume_tol = volume_tol if volume_tol is not None and volume_tol > 0 else None
        self.progress = progress_callback

    def search(self):
        results = []
        details = []

        for idx, target in enumerate(self.targets):
            if self.progress:
                self.progress(f"⏳ [{idx+1}/{len(self.targets)}] Поиск высоты для объёма {target:.1f} м³...")
            z_found, iters, lo, hi, V_lo, V_hi = self._find_height_for_target(target)
            if np.isnan(z_found):
                if self.progress:
                    self.progress(f"  ⚠️ Целевой объём {target:.1f} недостижим")
                results.append(float('nan'))
                details.append({'volume': target, 'z': float('nan'),
                                'iterations': 0, 'error': float('nan'),
                                'height_interval': float('nan'), 'volume_diff': float('nan')})
            else:
                v_actual = self._volume_at(z_found)
                rel_error = abs(v_actual - target) / max(target, 1e-9)
                height_interval = hi - lo
                volume_diff = abs(v_actual - target)
                if self.progress:
                    self.progress(
                        f"  ✅ Найдено: Z={z_found:.6f}, объём={v_actual:.1f}, "
                        f"итераций={iters}, отклонение={rel_error*100:.4f}%"
                    )
                    self.progress(
                        f"     Точность: по высоте интервал {height_interval:.6f} м "
                        f"(задан {self.height_tol:.6f} м), по объёму отклонение {volume_diff:.1f} м³ "
                        f"(задан {self.volume_tol if self.volume_tol else 'не задано'})"
                    )
                results.append(z_found)
                details.append({
                    'volume': target,
                    'z': z_found,
                    'iterations': iters,
                    'error': rel_error,
                    'height_interval': height_interval,
                    'volume_diff': volume_diff,
                    'height_tol': self.height_tol,
                    'volume_tol': self.volume_tol
                })

        return {
            'volumes': self.targets,
            'results': results,
            'details': details,
            'cache_stats': self.cache.stats()
        }

    def _volume_at(self, level):
        def compute():
            return compute_volume_by_contour(self.mesh, level)
        result, from_cache = self.cache.get_or_compute(level, compute)
        if self.progress:
            if from_cache:
                self.progress(f"    💾 Кэш (попадание): Z={level:.6f} → V={result:.1f}")
            else:
                self.progress(f"    🔢 Вычислено → в кэш: Z={level:.6f} → V={result:.1f}")
        return result

    def _volume_ok(self, volume, target):
        """Проверяет, достигнута ли точность по объёму."""
        if self.volume_tol is None:
            return False
        return abs(volume - target) <= self.volume_tol

    def _height_ok(self, lo, hi):
        """Проверяет, достигнута ли точность по высоте."""
        return (hi - lo) <= self.height_tol

    def _find_height_for_target(self, target):
        total_iterations = 0
        z_min = self.mesh.z_min
        z_max = self.mesh.z_max

        # Шаг 1: сканирование с шагом 100 м
        scan_step = 100.0
        z_lo = math.floor((z_min - 1.0) / scan_step) * scan_step
        z_hi = math.ceil((z_max + 1.0) / scan_step) * scan_step

        if self.progress:
            self.progress(f"  📐 Шаг 1: Сканирование с шагом {scan_step:.0f} м...")

        z_scan = np.arange(z_lo, z_hi + scan_step/2, scan_step)
        z_scan = [float(z) for z in z_scan]
        v_scan = []
        for zz in z_scan:
            v = self._volume_at(zz)
            v_scan.append(v)
            total_iterations += 1

        # Ищем переход через целевой объём
        z_left, z_right = None, None
        for i in range(len(z_scan) - 1):
            z1, z2 = z_scan[i], z_scan[i+1]
            v1, v2 = v_scan[i], v_scan[i+1]
            if (v1 < target <= v2) or (v1 > target >= v2):
                z_left, z_right = z1, z2
                if self.progress:
                    self.progress(f"  ✅ Переход через target: [{z1:.1f}, {z2:.1f}]")
                break

        # Если переход не найден, ищем пики в интервалах, где оба конца < target
        if z_left is None:
            if self.progress:
                self.progress("  🔍 Переход не найден, ищу пики внутри интервалов...")

            candidates = []
            for i in range(len(z_scan) - 1):
                v1, v2 = v_scan[i], v_scan[i+1]
                if v1 < target and v2 < target and not (v1 == 0 and v2 == 0):
                    z_mid = (z_scan[i] + z_scan[i+1]) / 2.0
                    v_mid = self._volume_at(z_mid)
                    total_iterations += 1
                    if v_mid < max(v1, v2):
                        continue
                    candidates.append((v_mid, i, v1, v2, z_mid))

            candidates.sort(reverse=True, key=lambda x: x[0])

            for v_mid, i, v1, v2, z_mid in candidates:
                z1, z2 = z_scan[i], z_scan[i+1]
                z_above, iter_above = self._find_point_above_target(target, z1, z2)
                total_iterations += iter_above
                if z_above is not None:
                    z_left, z_right = z1, z_above
                    if self.progress:
                        self.progress(f"  ✅ Найдена точка V > target: Z={z_above:.3f}")
                    break

            if z_left is None:
                if self.progress:
                    self.progress("  ⚠️ Целевой объём недостижим")
                return float('nan'), total_iterations, None, None, None, None

        # Шаг 2: бинарное уточнение
        if self.progress:
            self.progress(f"  📐 Шаг 2: Уточнение в диапазоне [{z_left:.3f}, {z_right:.3f}]")

        lo, hi = z_left, z_right
        V_lo = self._volume_at(lo)
        V_hi = self._volume_at(hi)

        # Проверяем начальные границы на соответствие объёмной точности
        if self._volume_ok(V_lo, target):
            return float(lo), total_iterations, lo, hi, V_lo, V_hi
        if self._volume_ok(V_hi, target):
            return float(hi), total_iterations, lo, hi, V_lo, V_hi

        step = 10.0
        last_step = None

        # Основной цикл: продолжаем, пока не выполнено ни одно условие остановки
        while not (self._height_ok(lo, hi) or self._volume_ok(V_lo, target) or self._volume_ok(V_hi, target)):
            if self.progress and last_step != step:
                self.progress(f"    Уровень точности: {step:g} м")
                last_step = step

            while hi - lo > step:
                mid_raw = (lo + hi) / 2.0
                mid = round(mid_raw / step) * step
                if mid <= lo:
                    mid = lo + step
                elif mid >= hi:
                    mid = hi - step
                if mid <= lo or mid >= hi:
                    break
                v_mid = self._volume_at(mid)
                total_iterations += 1

                # Проверяем объёмную точность немедленно
                if self._volume_ok(v_mid, target):
                    return float(mid), total_iterations, lo, hi, V_lo, V_hi

                if v_mid < target:
                    lo = mid
                    V_lo = v_mid
                else:
                    hi = mid
                    V_hi = v_mid

                if self._height_ok(lo, hi) or self._volume_ok(V_lo, target) or self._volume_ok(V_hi, target):
                    break

            if self._height_ok(lo, hi) or self._volume_ok(V_lo, target) or self._volume_ok(V_hi, target):
                break

            step /= 10.0
            if step < self.height_tol / 10:
                break

        # Финальное решение
        z_result = (lo + hi) / 2.0
        # Округляем до заданной точности по высоте
        z_result = round(z_result / self.height_tol) * self.height_tol
        self._volume_at(z_result)
        total_iterations += 1
        return float(z_result), total_iterations, lo, hi, V_lo, V_hi

    def _find_point_above_target(self, target, z_lo, z_hi):
        """
        Ищет в интервале [z_lo, z_hi] точку, где V > target.
        Использует бинарный поиск максимума по производной и затем проверку.
        Возвращает (z_found, iterations) или (None, iterations).
        """
        total = 0
        z_mid = (z_lo + z_hi) / 2.0
        v_mid = self._volume_at(z_mid)
        total += 1
        if v_mid > target:
            return float(z_mid), total

        z_q1 = z_lo + (z_hi - z_lo) / 4.0
        z_q3 = z_lo + 3.0 * (z_hi - z_lo) / 4.0
        v_q1 = self._volume_at(z_q1)
        total += 1
        if v_q1 > target:
            return float(z_q1), total
        v_q3 = self._volume_at(z_q3)
        total += 1
        if v_q3 > target:
            return float(z_q3), total

        peak_lo, peak_hi = z_lo, z_hi
        delta = (z_hi - z_lo) / 100.0
        while peak_hi - peak_lo > 1.0:
            z_peak_mid = (peak_lo + peak_hi) / 2.0
            v_peak_mid = self._volume_at(z_peak_mid)
            v_peak_plus = self._volume_at(z_peak_mid + delta)
            total += 2
            if v_peak_plus > v_peak_mid:
                peak_lo = z_peak_mid
            else:
                peak_hi = z_peak_mid

        z_peak = (peak_lo + peak_hi) / 2.0
        v_peak = self._volume_at(z_peak)
        total += 1
        if v_peak > target:
            return float(z_peak), total
        return None, total