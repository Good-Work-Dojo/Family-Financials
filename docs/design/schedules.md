# Recurring charges and schedules

> Scope: how recurring charges are detected, promoted to schedules, judged (paid / due / missed / data stale) and fed
> into the forecast. Describes the system as it is; verify names against the code (`etl/enrich/recurring.py`,
> `app/src/lib/schedules.ts`, `app/src/lib/forecast.ts`).

## Detection and promotion (ETL, clock-free)

`recurring.detect` groups external spending (purchases and fees; split purchases by their parent) by
`(vendor, account)` and needs at least 3 occurrences. A series gets a cadence when at least 70% of its consecutive gaps
fall in one band (`CADENCE_BANDS`: weekly, biweekly, monthly, quarterly, annual) and an amount mode: `fixed` (tight
spread, or most charges share one amount) or `variable` (within a band around the median); a wider spread is a
frequent store, not a subscription. A series is `active` while the data end is within twice its median gap of
`last_seen`; a vendor in `assumptions.cancelled_subscriptions` is never active.

Every series also carries `median_gap_days`, `expected_dates` (its next three occurrences; `next_expected` is the
first), `grace_days` per cadence (`GRACE_DAYS`) and `confidence` (the cadence band's share × 1.0 for fixed or 0.85 for
variable). `promote(record)` sets `schedule`: active, cadence set, amount mode fixed or variable, at least 4 DISTINCT
charge dates for weekly / biweekly and 3 otherwise, and confidence ≥ 0.7. Nothing here reads the clock;
judging a schedule against today is the app's job.

**A monthly bill is expected on its day of the month** (`monthly_rule`, read from the series' own distinct payment
dates, never the clock or the data end). The rule is the first of these that holds:

1. **First or last business day**: none of the last four payments fell on a Saturday or Sunday (a payment on a weekend
   says the bill is paid on its calendar day: a card charge that posts on the 1st whatever the day is a bill on the
   1st), and at least three of them fell on their month's first business day (the 1st, or the Monday after a weekend
   1st) — the first business day of each month; the same for the last business day. A business day is Monday to
   Friday — there is no holiday calendar, so a holiday can make the real payment a day later, which the grace window
   absorbs. This case is checked first, so a bill on the 1st that weekends push to the 2nd or 3rd is expected on the
   Monday, not the 1st.
2. **A stable day** (`_stable_day`), in this order: the last six payments' days of the month all within three days
   (the 14th to the 16th); else all but one of them, when there are four or more (one odd payment — a 15th bill paid
   once on the 18th — is left out); else the last three alone, when the payment before them is three days or more
   from their day (the bill moved its day; a day that drifts a day a month did not move). The day is the median of the
   payments within the span (the lower middle one), clamped to a shorter month's last day (the 31st is 28 or 29
   February). A payment on the last day of a month shorter than 31 days counts as that day or any later one, so a bill
   on the 31st paid on 30 April stays on the 31st.
3. **Otherwise** (an irregular monthly bill) and for every other cadence: `last_seen` stepped by the median gap. Days
   that wrap round the month end (the 30th, the 31st, the 1st) are no stable day: such a bill is the business-day case
   when that test passes, else irregular.

**The first expected date** is the first date the rule gives at least `FIRST_EXPECTED_MIN_DAYS` (15) after the last
payment, from the last payment's own month on; the second and third are the months after it. So a payment that crossed
a month end is not followed by a date it already paid: a bill due on the 1st, paid on Friday 31 July because 1 August is
a Saturday, is next expected on 1 September, and a last-business-day bill whose October payment came on 2 November is
still expected on 30 November. The ordinary cases are the next month: paid 1 October, expected 2 November; a 15th bill
paid on 13 September, 15 October; a 31st bill paid on 31 January, 28 February.

The grace window and "missed" read the expected date whichever rule set it: `grace_days` per cadence (monthly 6), and
missed only past the first expected date plus grace with the account's data reaching that deadline (the status below).
"Lapsed" reads no expected date: `active` is false once the data end is more than twice the median gap after
`last_seen`, or the vendor is cancelled.

**The monthly and yearly figures** (`monthly_equivalent`, `annualized`; `annualized_cents`): a monthly, quarterly or
annual series counts the charges a calendar year holds — twelve, four, one — so its yearly figure is that many of its
`typical_amount` and its monthly figure is the amount, a third of it or a twelfth of it (a $900 quarterly bill is $300
a month and $3,600 a year, the four charges the bills chart counts); a weekly or biweekly series scales 365 days by its
period (7 or 14: a year is no whole number of weeks), and its monthly figure is that over twelve. The ETL is the one
place that converts: Recurring's "Monthly cost" sums the export's monthly figures and its "Annualized" tile the
export's yearly figures, so both agree with the ETL to the cent.

## Status (app, `scheduleStatus`)

Evaluated in this order against `today` and the owning account's `txn_data_end` (`knownThroughFor`: the honest "data
known through" date for feed and CSV accounts alike — not the feed's fetch time). `today` is the household's day,
`clock.today(assumptions.household.timezone)` (`app/src/lib/clock.ts`), so a browser in another zone sees the same
statuses; with no zone in the export it is the browser's own day:

1. `inactive` — not a schedule, or no expected date;
2. `paid` — a charge posted within `grace_days` of today (the ETL derives the expected dates from `last_seen`, so
   "paid" means recently paid);
3. past the deadline (first expected date + grace): `missed` only when the account's data reaches that deadline,
   otherwise `unknown`, shown as "data stale" — "missed" is never claimed on data that cannot know;
4. `due` — from the day before the expected date through the grace window;
5. `upcoming` — within 14 days;
6. `scheduled` — no chip.

Consumers: the Recurring tab's status column and its "missed only" pill (`visibleRecurring`: one filter for the rows
and the pill's count); Home's "next 30 days" (`upcomingSchedules`, `lib/upcoming.ts`; five rows on a phone and twelve on a
desktop, then "N more"), whose "≈" total is the next charge of each schedule LISTED, each in whole dollars as its row
prints it (`wholeDollars`, `lib/format.ts`), so it adds up to the rows, and names the rows past the cap as not in it.

## The forecast counts a schedule once

`forecastCashflow` (`lib/forecast.ts`) = a trailing baseline of NON-scheduled spend × seasonality + the promoted
schedules' future charges. `scheduledHistory` removes a scheduled `(vendor, account)` pair's past charges from the
baseline; `chargeDates` adds its future charges at `typical_amount`, on the dates the bills chart reads
(`expectedCharges`, below): the exported expected dates, then later ones by the cadence. So the forecast and the bills
chart put every charge in the same month, and the forecast's dates are the chips' dates. The forecast starts from the newest snapshot's liquid cash, which already
holds every day up to the snapshot's date: its first month is only the rest of the month that date falls in, the
baseline prorated to the days after it and only the charges dated after it (`forecastStart`, anchored on the balance's
date, not the transactions' last day); on the month's last day the forecast starts with the next month, whole
(`docs/design/figures-and-vocabulary.md`).

## Renewals and obligations

`isLumpy` (`lib/obligations.ts`) is the one predicate for a bill worth seeing coming: a renewal (annual, semiannual or
quarterly cadence) or a large charge that does not recur weekly to monthly. Spending → Recurring's obligations chart,
its "all renewals" list and "renewals in the next 90 days" line, and Planning's lumpy-bills figure share it.

The "next 12 months" chart has exactly twelve bars (`obligationMonths`): the first is the month after the month in
progress (`monthInProgress`), never a month already under way, and a month no bill falls in is a bar at zero. Each bar
is the active items' charges expected in that month, by `expectedCharges` (`lib/obligations.ts`, which the forecast
reads too): each item's exported `expected_dates` (the ETL's, so a monthly bill on its own day), then later ones by its
cadence — calendar months for monthly, quarterly and annual items, each counted from the exported date with the latest
day of the month (so a clamped month end never becomes the day) and kept from the month after the last exported date
on; the median gap in days for weekly and biweekly ones, as the ETL steps them (7 or 14 from an export that states
none) — and dates already past (a late or lapsed charge) are left out of the window. So a monthly bill with a
day-of-month rule is in every bar from its next expected month on, an annual one in its month; an irregular monthly
bill's three exported dates are day counts from its last payment and can skip or double a month before the calendar
months take over. The chart's list of renewals and Planning's
lumpy-bills figure total the same twelve months; the 90-day line runs from the newest snapshot's date, else the day the
data reaches (`obligationsFrom`).
