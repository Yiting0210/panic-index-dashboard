"""NYSE trading days: weekdays that are not exchange holidays.

Built from pandas holiday rules so no extra dependency is needed. Unscheduled
closures (national days of mourning) are listed in SPECIAL_CLOSURES; a new
one shows up as a "no prices" warning until it is added here.
"""
from datetime import date
from functools import lru_cache

from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday,
                                    USLaborDay, USMartinLutherKingJr, USMemorialDay,
                                    USPresidentsDay, USThanksgivingDay, nearest_workday,
                                    sunday_to_monday)

SPECIAL_CLOSURES = {
    date(2025, 1, 9),   # National Day of Mourning for President Carter
}


class NYSEHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        # A Saturday New Year's Day is not observed on the Friday before.
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01",
                observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=None)
def _holidays(year):
    days = NYSEHolidayCalendar().holidays(date(year, 1, 1), date(year, 12, 31))
    return frozenset(d.date() for d in days) | {d for d in SPECIAL_CLOSURES if d.year == year}


def is_trading_day(day):
    return day.weekday() < 5 and day not in _holidays(day.year)
