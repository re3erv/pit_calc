# data/zeta_catalog.py

ZETA_DICT = {
    'всас': 0.5,
    'выпуск': 1.0,
    'обратный клапан': 2.0,
    'задвижка': 0.3,
    'переход диаметров': 0.2
}

def get_zeta_for_type(obj_type):
    """Возвращает коэффициент местного сопротивления для типа объекта."""
    return ZETA_DICT.get(obj_type, 0.5)