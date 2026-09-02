from datetime import time

from bot.config import _int_set, _time


def test_int_set_parses_csv_with_spaces():
    assert _int_set("1, 22,333") == {1, 22, 333}
    assert _int_set("") == set()
    assert _int_set(",,") == set()


def test_time_falls_back_on_garbage():
    assert _time("09:30", "10:00") == time(9, 30)
    assert _time("", "10:00") == time(10, 0)
    assert _time("не время", "10:00") == time(10, 0)
