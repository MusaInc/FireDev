package dev.firedev.pulse.domain

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate

class WeeklySummaryTest {
    private val today = LocalDate.of(2026, 10, 17)
    private fun daysAgo(n: Long, minutes: Int) = FocusSession(today.minusDays(n), minutes)

    @Test fun totalsOnlyTheLastSevenDays() =
        assertEquals(90, WeeklySummary.of(listOf(daysAgo(0, 60), daysAgo(6, 30), daysAgo(7, 100)), today).totalMinutes)
    @Test fun countsActiveDays() =
        assertEquals(2, WeeklySummary.of(listOf(daysAgo(0, 10), daysAgo(0, 5), daysAgo(3, 20)), today).activeDays)
    @Test fun headlineUsesFormattedDuration() =
        assertEquals("1 h 30 min this week", WeeklySummary.of(listOf(daysAgo(0, 90)), today).headline)
    @Test fun detailWithoutStreak() =
        assertEquals("Start a streak today · 1 of 7 days", WeeklySummary.of(listOf(daysAgo(3, 20)), today).detail)
    @Test fun detailWithStreak() =
        assertEquals("2 day streak · 2 of 7 days", WeeklySummary.of(listOf(daysAgo(0, 20), daysAgo(1, 20)), today).detail)
}
