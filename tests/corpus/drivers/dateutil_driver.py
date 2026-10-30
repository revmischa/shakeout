from datetime import datetime

from dateutil import parser, relativedelta, rrule

print(parser.parse("2024-02-29T10:30:00"))
print(datetime(2024, 1, 31) + relativedelta.relativedelta(months=+1))
print(list(rrule.rrule(rrule.WEEKLY, count=3, dtstart=datetime(2024, 1, 1))))
