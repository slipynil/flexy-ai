"""Ручной тест ожиданий в src.generation (без живого flexy-ai).

Проверяет поведение при «вне штатном» режиме flexy-ai.com:
1. флап токена лечится повторным входом, а не cooldown;
2. перебор аккаунтов обрывается по дедлайну GENERATION_DEADLINE_SECONDS;
3. флап на SYSTEMIC_FAILURE_THRESHOLD аккаунтах подряд -> ранний выход
   «сервис недоступен», остальные аккаунты не расходуются;
4. «предохранитель»: следующий запрос падает мгновенно, не трогая пул;
5. серия флапов, прерванная другой ошибкой, предохранитель не открывает;
6. исчерпанный пул дает понятную ошибку.

Подмена DB и FlexyAiAPI — атрибутов модуля, как описано в ADR-001.
Запуск: ./venv/bin/python tests/generation_wait_test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.generation as gen
from src.errors.api_errors import LoginFlapError


class FakeDB:
    """Эмуляция репозитория: помнит cooldown и списания tries."""

    def __init__(self, n_accounts):
        self.accounts = [(f"key{i}", f"mail{i}@x.com", "pw") for i in range(n_accounts)]
        self.busy = []
        self.tries_updated = []

    def get_account(self):
        return self.accounts.pop(0) if self.accounts else None

    def set_account_busy(self, api_key, seconds=120):
        self.busy.append(api_key)

    def update_account_tries(self, api_key):
        self.tries_updated.append(api_key)

    def close(self):
        pass


class FakeAPI:
    """Эмуляция FlexyAiAPI: очередь исходов на каждый вызов login().

    Исходы: "ok" | "flap" | "timeout". Когда очередь пуста, используется
    default_result.
    """

    default_result = "ok"
    login_results: list = []

    def __init__(self):
        self.calls = 0

    def login(self, email, password):
        self.calls += 1
        result = (
            FakeAPI.login_results.pop(0)
            if FakeAPI.login_results
            else FakeAPI.default_result
        )
        if result == "flap":
            raise LoginFlapError("в ответе нет user_token (вход не выполнен?)")
        if result == "timeout":
            time.sleep(1.1)  # имитация read-таймаута: попытка «дорогая»
            raise RuntimeError("Read timed out")

    def set_api_key(self, key):
        pass

    def context_to_image(self, path, prompt):
        return "out.jpg"


def reset_module_state():
    """Между сценариями сбрасываем подмены и «предохранитель»."""
    gen._service_broken_until = None
    FakeAPI.login_results = []
    FakeAPI.default_result = "ok"


def main():
    gen.DB = lambda *a, **k: FakeDB(1)
    gen.FlexyAiAPI = FakeAPI
    saved_deadline = gen.GENERATION_DEADLINE_SECONDS

    # 1. флап токена -> повторный вход того же аккаунта -> успех
    reset_module_state()
    FakeAPI.login_results = ["flap", "ok"]
    result = gen.context_to_image("in.jpg")
    assert result == "out.jpg", result
    print("1. флап токена: повторный вход спас попытку — OK")

    # 2. все запросы виснут -> дедлайн обрывает перебор
    reset_module_state()
    gen.GENERATION_DEADLINE_SECONDS = 3  # ужимаем бюджет, чтобы тест шел быстро
    FakeAPI.default_result = "timeout"
    db = FakeDB(50)
    gen.DB = lambda *a, **k: db
    t0 = time.monotonic()
    try:
        gen.context_to_image("in.jpg")
        raise AssertionError("ожидали RuntimeError по дедлайну")
    except RuntimeError as e:
        elapsed = time.monotonic() - t0
        assert "слишком медленно" in str(e), e
        # бюджет 3с + одна незавершенная попытка ~1.1с, но точно не 50 x 1.1с
        assert elapsed < 10, f"дедлайн не сработал: {elapsed:.1f}c"
    print(f"2. дедлайн: перебор оборван за {elapsed:.1f}с (вместо ~55с) — OK")

    # 3. флап на всех аккаунтах подряд -> ранний выход, пул не жжем
    reset_module_state()
    gen.GENERATION_DEADLINE_SECONDS = saved_deadline
    FakeAPI.default_result = "flap"
    db = FakeDB(10)
    gen.DB = lambda *a, **k: db
    try:
        gen.context_to_image("in.jpg")
        raise AssertionError("ожидали RuntimeError «сервис недоступен»")
    except RuntimeError as e:
        assert "временно недоступен" in str(e), e
        # в cooldown ушли только отказавшие аккаунты, остальные не тронуты
        assert len(db.busy) == gen.SYSTEMIC_FAILURE_THRESHOLD, db.busy
        assert len(db.accounts) == 10 - gen.SYSTEMIC_FAILURE_THRESHOLD
    print(
        f"3. системный флап: выход после {gen.SYSTEMIC_FAILURE_THRESHOLD} аккаунтов, "
        f"пул не расходуется — OK"
    )

    # 4. «предохранитель» (открыт сценарием 3): мгновный отказ без аккаунтов
    #    (сбрасывать состояние нельзя — проверяем именно открытый предохранитель)
    FakeAPI.default_result = "ok"  # сервис «починился», но предохранитель раньше
    db = FakeDB(10)
    gen.DB = lambda *a, **k: db
    t0 = time.monotonic()
    try:
        gen.context_to_image("in.jpg")
        raise AssertionError("ожидали мгновенный отказ от предохранителя")
    except RuntimeError as e:
        elapsed = time.monotonic() - t0
        assert "временно недоступен" in str(e), e
        assert elapsed < 1, f"отказ не мгновенный: {elapsed:.2f}c"
        assert not db.busy and len(db.accounts) == 10
    print(f"4. предохранитель: отказ за {elapsed:.3f}с, аккаунты не тронуты — OK")

    # 5. серия флапов, прерванная другой ошибкой, — предохранитель не открывается:
    #    акк1: flap,flap (счетчик 1); акк2: timeout (сброс); акк3: flap,flap
    #    (счетчик 1); акк4: ok -> успех
    reset_module_state()
    FakeAPI.login_results = ["flap", "flap", "timeout", "flap", "flap", "ok"]
    db = FakeDB(5)
    gen.DB = lambda *a, **k: db
    result = gen.context_to_image("in.jpg")
    assert result == "out.jpg", result
    assert len(db.busy) == 3, db.busy
    assert len(db.tries_updated) == 1
    print("5. прерванная серия флапов: генерация дошла до успеха — OK")

    # 6. пул исчерпан мгновенно (без аккаунтов)
    reset_module_state()
    gen.DB = lambda *a, **k: FakeDB(0)
    try:
        gen.context_to_image("in.jpg")
        raise AssertionError("ожидали RuntimeError")
    except RuntimeError as e:
        assert "нет свободных аккаунтов" in str(e)
    print("6. пустой пул: понятная ошибка — OK")

    gen.GENERATION_DEADLINE_SECONDS = saved_deadline


if __name__ == "__main__":
    main()
