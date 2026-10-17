package dev.firedev.pulse.domain

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate

class StreaksTest {
    private val today = LocalDate.of(2026, 10, 17)
    private fun daysAgo(n: Long, minutes: Int = 30) = FocusSession(today.minusDays(n), minutes)

    @Test fun emptyHistoryHasNoStreak() = assertEquals(0, Streaks.current(emptyList(), today))
    @Test fun todayOnly() = assertEquals(1, Streaks.current(listOf(daysAgo(0)), today))
    @Test fun streakCanEndYesterday() = assertEquals(2, Streaks.current(listOf(daysAgo(1), daysAgo(2)), today))
    @Test fun gapBreaksStreak() = assertEquals(1, Streaks.current(listOf(daysAgo(0), daysAgo(2)), today))
    @Test fun minimumMinutesApplies() =
        assertEquals(1, Streaks.current(listOf(daysAgo(0, 40), daysAgo(1, 10)), today, minimumMinutes = 20))
    @Test fun sessionsOnSameDayAddUp() =
        assertEquals(1, Streaks.current(listOf(daysAgo(0, 10), daysAgo(0, 15)), today, minimumMinutes = 20))
    @Test fun longestFindsBestRun() =
        assertEquals(3, Streaks.longest(listOf(daysAgo(0), daysAgo(5), daysAgo(6), daysAgo(7), daysAgo(9))))
    @Test fun longestOfEmptyIsZero() = assertEquals(0, Streaks.longest(emptyList()))
}
