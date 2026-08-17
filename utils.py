# utils.py
import time
import functools

def timed(func):
    """Декоратор для измерения времени выполнения метода/функции."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start

        obj = args[0] if args else None

        # Если у объекта есть метод _log, используем его
        if hasattr(obj, '_log'):
            obj._log(f"⏱️ {func.__qualname__} выполнен за {elapsed:.3f} сек")
        # Если есть progress_signal (для потоков), эмитим туда
        elif hasattr(obj, 'progress_signal'):
            obj.progress_signal.emit(f"⏱️ {func.__qualname__} выполнен за {elapsed:.3f} сек")
        # Иначе просто печатаем в консоль
        else:
            print(f"⏱️ {func.__qualname__} выполнен за {elapsed:.3f} сек")
        return result
    return wrapper