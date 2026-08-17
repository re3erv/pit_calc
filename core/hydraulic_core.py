# hydraulic_core.py
import numpy as np
import math

# ---------- Физические константы (вода) ----------
GRAVITY = 9.81  # м/с²

# ---------- Вспомогательные функции ----------
def fluid_properties(temperature=20.0):
    """
    Возвращает плотность и кинематическую вязкость воды.
    temperature: температура в °C.
    """
    # Плотность (кг/м³) по упрощённой формуле для воды
    rho = 1000.0 * (1 - (temperature - 4.0) * 0.0002)
    # Кинематическая вязкость (м²/с) при температуре
    nu = 1.004e-6 * (1 - 0.025 * (temperature - 20.0))
    return rho, nu

def inner_diameter(outer_d, wall_thickness):
    """Внутренний диаметр трубы, м."""
    return outer_d - 2 * wall_thickness

def flow_velocity(flow_rate, diameter):
    """
    Средняя скорость потока, м/с.
    flow_rate: расход, м³/с
    diameter: внутренний диаметр, м
    """
    area = math.pi * diameter**2 / 4.0
    return flow_rate / area

def reynolds_number(velocity, diameter, nu):
    """Число Рейнольдса."""
    return velocity * diameter / nu

def friction_factor_swamee_jain(re, roughness, diameter):
    """
    Коэффициент гидравлического трения λ по формуле Свами–Джайна.
    roughness: абсолютная шероховатость, м
    diameter: внутренний диаметр, м
    """
    if re < 2000:
        # Ламинарный режим: λ = 64/Re
        return 64.0 / re
    else:
        # Турбулентный режим (формула Свами–Джайна)
        return 0.25 / (math.log10((roughness / (3.7 * diameter)) + (5.74 / re**0.9)))**2

def darcy_weisbach_loss(friction_factor, length, diameter, velocity):
    """
    Потери напора по длине, м.
    """
    return friction_factor * (length / diameter) * (velocity**2 / (2 * GRAVITY))

def local_loss(k_coeff, velocity):
    """
    Местные потери напора, м.
    k_coeff: коэффициент местного сопротивления ζ
    """
    return k_coeff * (velocity**2 / (2 * GRAVITY))

def bend_loss_coefficient(angle_deg, r_over_d):
    """
    Приближённый коэффициент местного сопротивления для отвода.
    angle_deg: угол поворота в градусах (пространственный)
    r_over_d: отношение радиуса гиба к диаметру трубы
    Возвращает ζ.
    """
    # Упрощённая зависимость: ζ = A * B, где A зависит от угла, B от R/D
    # Для R/D >= 2 значения можно взять из таблиц, здесь упрощённо.
    angle_rad = math.radians(angle_deg)
    # Коэффициент для угла (примерно линейно для углов > 30°)
    if angle_deg < 5:
        return 0.0
    base = 0.5 * (1 - math.cos(angle_rad))  # грубая аппроксимация
    if r_over_d < 1.0:
        correction = 3.0  # очень крутой поворот
    elif r_over_d < 2.0:
        correction = 1.5
    else:
        correction = 1.0
    return base * correction + 0.1  # добавим небольшую постоянную

# ---------- Основной расчёт ----------
def calculate_pipeline(points, pipe_params, flow_rate, fluid_temp=20.0,
                       fittings=None, check_pn=True, pn=None, min_angle_deg=1.0,
                       p_in_head=0.0, p_out_head=1.0):
    """
    Расчёт напорного трубопровода по заданной 3D-полилинии.

    Параметры:
    points : list of (x, y, z) – координаты вершин полилинии
    pipe_params : dict с ключами:
        'outer_diameter' : float, м
        'wall_thickness' : float, м
        'roughness'      : float, м (абсолютная шероховатость)
        'r_min'          : float, м (минимальный радиус гиба)
    flow_rate : float, расход, м³/с
    fluid_temp : float, температура жидкости, °C
    fittings : list of dict или None. Каждый dict описывает местное сопротивление
               в точке (станции) или в узле. Формат:
               {'station': 3, 'k': 1.5}  # station – индекс вершины (0-based), k – коэффициент ζ
               или {'between': (1,2), 'k': 0.8}  # между узлами 1 и 2
    check_pn : bool – проверять ли превышение PN
    pn : float – номинальное давление, Па (или None)

    Возвращает словарь:
    {
        'segments': список участков с параметрами,
        'total_head_loss': суммарные потери, м,
        'required_head': требуемый напор насоса (разность энергии между выпуском и всасом), м,
        'warnings': список предупреждений
    }
    """
    # Константы
    rho, nu = fluid_properties(fluid_temp)

    outer_d = pipe_params['outer_diameter']
    wall_t = pipe_params['wall_thickness']
    d_inner = inner_diameter(outer_d, wall_t)
    roughness = pipe_params['roughness']
    r_min = pipe_params.get('r_min', 1.0)  # м

    total_friction_loss = 0.0
    total_local_loss = 0.0
    total_zeta = 0.0

    # Расход и скорость
    Q = flow_rate
    V = flow_velocity(Q, d_inner)
    area = math.pi * d_inner**2 / 4.0

    warnings = []

    # Проверка скорости
    if V < 1.0 or V > 3.0:
        warnings.append(f"Скорость {V:.2f} м/с вне допустимого диапазона 1–3 м/с")

    # Разбиваем полилинию на сегменты
    segments = []
    total_length = 0.0
    total_head_loss = 0.0

    # Для эпюры напоров будем хранить станции (узел, напор)
    # Начальное давление на всасе: по умолчанию 0 избыточных (атмосферное)
    # Для расчёта требуемого напора насоса будем считать, что насос в начале (узел 0)
    # Выпуск – последний узел, требуемое избыточное давление 1 м вод.ст.
    # (пока сделаем упрощённо: насос поднимает давление до нужного значения)

    # Геометрия: вычисляем длины, углы между сегментами
    n_points = len(points)
    if n_points < 2:
        raise ValueError("Нужно минимум 2 точки")

    # Массивы для эпюры
    station_distances = [0.0]  # расстояния от начала
    station_heads = [0.0]     # пока заполним позже

    # Сначала посчитаем длины и углы для каждого узла (кроме крайних)
    # angle[i] – угол поворота в узле i (между сегментами i-1 и i)
    angles = [0.0] * n_points

    for i in range(1, n_points - 1):
        p_prev = np.array(points[i-1])
        p_cur = np.array(points[i])
        p_next = np.array(points[i+1])
        v1 = p_prev - p_cur
        v2 = p_next - p_cur
        # косинус угла между направлениями (векторы от узла к соседям)
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-12)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        angle = math.degrees(math.acos(cos_angle))
        # Угол поворота трассы (острый угол между направлениями)
        # Направление потока: от i-1 к i, затем от i к i+1.
        # Угол между векторами v1 (направлен от i к i-1) и v2 (от i к i+1) – это внешний угол.
        # Мы хотим острый угол между направлением входящего и исходящего потоков.
        # Лучше использовать угол между векторами (i-1 -> i) и (i -> i+1):
        vec_in = p_cur - p_prev
        vec_out = p_next - p_cur
        cos_angle_flow = np.dot(vec_in, vec_out) / (np.linalg.norm(vec_in) * np.linalg.norm(vec_out) + 1e-12)
        cos_angle_flow = np.clip(cos_angle_flow, -1.0, 1.0)
        angle_flow = math.degrees(math.acos(cos_angle_flow))
        # Поворот – это отклонение от прямой, т.е. 180 - angle_flow, но если угол_flow = 180 (прямая), поворот=0.
        turn_angle = 180.0 - angle_flow
        angles[i] = turn_angle
        # Для очень малых углов (<1°) считаем, что поворота нет
        if turn_angle < min_angle_deg:
            angles[i] = 0.0
        else:
            # Проверка радиуса гиба
            # Мы не знаем фактический радиус, поэтому предполагаем, что труба может быть согнута с R = r_min
            r_over_d = r_min / d_inner
            if r_over_d < 1.0:
                warnings.append(f"Узел {i}: минимальный радиус гиба меньше диаметра (R/D = {r_over_d:.2f})")

    # Проходим по сегментам и считаем потери
    # Начальное давление на всасе: 0 м (атмосферное), но для расчёта требуемого напора насоса мы найдём разницу энергий.
    # Всас – узел 0, выпуск – узел n-1.
    # Требуемый напор насоса = (z_вып - z_вс) + (p_вып/ρg - p_вс/ρg) + суммарные потери.
    # Если p_вс = 0, p_вып = 1 м, то H_нас = Δz + 1 + h_loss.

    # Рассчитываем потери на каждом сегменте (прямом участке)
    for i in range(n_points - 1):
        p1 = np.array(points[i])
        p2 = np.array(points[i+1])
        length = np.linalg.norm(p2 - p1)
        if length < 1e-6:
            continue  # нулевой сегмент
        total_length += length

        # Потери по длине
        re = reynolds_number(V, d_inner, nu)
        lambda_ = friction_factor_swamee_jain(re, roughness, d_inner)
        h_friction = darcy_weisbach_loss(lambda_, length, d_inner, V)

        # Потери на повороте в начале сегмента (если угол в узле i > 0)
        h_local = 0.0

        total_friction_loss += h_friction

        if i > 0 and angles[i] > 0:
            # Коэффициент для поворота
            # Используем r_min для оценки R/D
            r_over_d = r_min / d_inner
            k_bend = bend_loss_coefficient(angles[i], r_over_d)
            h_local += local_loss(k_bend, V)
            total_zeta += k_bend          # <-- добавьте
            # Если рядом ещё поворот (расстояние < 5*D), увеличиваем коэффициент (п.2.8)
            # Здесь пока не реализовано.

        # Дополнительные местные сопротивления из fittings
        if fittings:
            for fit in fittings:
                # Проверяем, относится ли к этому сегменту
                if 'station' in fit and fit['station'] == i:
                    h_local += local_loss(fit['k'], V)
                    total_zeta += fit['k']   # <-- добавьте
                elif 'between' in fit and fit['between'] == (i, i+1):
                    h_local += local_loss(fit['k'], V)
                    total_zeta += fit['k']   # <-- добавьте
        total_local_loss += h_local
        
        total_segment_loss = h_friction + h_local
        total_head_loss += total_segment_loss

        segments.append({
            'index': i,
            'start_point': points[i],
            'end_point': points[i+1],
            'length': length,
            'velocity': V,
            'reynolds': re,
            'friction_factor': lambda_,
            'friction_loss': h_friction,
            'local_loss': h_local,
            'total_loss': total_segment_loss,
            'elevation_start': p1[2],
            'elevation_end': p2[2],
            'total_friction_loss': total_friction_loss,
            'total_local_loss': total_local_loss,
        })

        # Расстояние от начала
        station_distances.append(station_distances[-1] + length)

    # Требуемый напор насоса
    z_in = points[0][2]
    z_out = points[-1][2]
    required_head = (z_out - z_in) + (p_out_head - p_in_head) + total_head_loss

    # Эпюра напоров: пьезометрический напор в каждом узле.
    # Начинаем с давления на всасе + отметки всаса = H_in = z_in + p_in_head
    # Далее вычитаем потери на каждом сегменте, но прибавляем геодезическое изменение?
    # Лучше строить напор относительно уровня моря.
    # Пьезометрическая высота = z + p/ρg.
    # Насос создаёт напор, поэтому в узле 0 после насоса напор = H_in + required_head? 
    # На самом деле насос добавляет энергию, чтобы преодолеть разность высот и потери, 
    # и обеспечить требуемое давление на выходе.
    # Итоговая линия энергии: на входе насоса (узел 0) энергия = z_in + p_in_head + V^2/2g (скоростной напор можно не учитывать, т.к. он одинаков на всей длине при постоянном диаметре)
    # На выходе из насоса энергия = z_in + p_in_head + H_нас.
    # На выпуске энергия = z_out + p_out_head.
    # Тогда H_нас = (z_out - z_in) + (p_out_head - p_in_head) + h_loss.
    # Это совпадает с required_head.

    # Для эпюры: пьезометрический напор в каждом узле после насоса:
    # В узле 0 (после насоса) напор = z_in + p_in_head + H_нас
    # Далее по мере движения к выпуску напор уменьшается на потери и изменяется за счёт геодезии.
    # Пьезометрический напор в узле i = (z_in + p_in_head + H_нас) - Σ потерь до i.
    # Но фактически потери уже учтены, а геодезическая высота автоматически входит.
    # Построим: H_piezo[i] = z_in + p_in_head + required_head - кумулятивные потери до i.
    # Однако это даст напор на уровне оси трубы, без учёта скоростного напора. Это приемлемо.

    # Заполним эпюру
    station_heads = [0.0] * n_points
    station_heads[0] = z_in + p_in_head + required_head
    cumulative_loss = 0.0
    for i, seg in enumerate(segments):
        cumulative_loss += seg['total_loss']
        station_heads[i+1] = station_heads[0] - cumulative_loss

    # Проверки
    warnings = check_pressure_limits(
        points, station_heads, pn, check_pn, warnings
    )

    return {
        'segments': segments,
        'total_length': total_length,
        'total_head_loss': total_head_loss,
        'total_friction_loss': total_friction_loss,
        'total_local_loss': total_local_loss,
        'total_zeta': total_zeta,   # <-- добавьте
        'required_head': required_head,
        'station_distances': station_distances,
        'station_heads': station_heads,
        'points': points,
        'warnings': warnings,
        'velocity': V,
        'inner_diameter': d_inner,
        'reynolds': reynolds_number(V, d_inner, nu) if len(segments) > 0 else None,
    }

def check_pressure_limits(points, station_heads, pn, check_pn, warnings):
    # Вакуум
    for i, (pt, head) in enumerate(zip(points, station_heads)):
        elevation = pt[2]
        if head < elevation:
            warnings.append(f"Узел {i}: вакуум! Напор {head:.2f} м ниже отметки {elevation:.2f} м")

    # PN
    if check_pn and pn is not None:
        pn_head = pn / (1000.0 * GRAVITY)
        for i, (pt, head) in enumerate(zip(points, station_heads)):
            elevation = pt[2]
            if (head - elevation) > pn_head:
                warnings.append(f"Узел {i}: избыточное давление {head - elevation:.2f} м превышает PN ({pn_head:.2f} м)")
    return warnings