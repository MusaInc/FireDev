package dev.firedev.pulse.domain

import java.time.LocalDate

/** Consecutive-day streaks of focused work. */
object Streaks {
    /** Days in a row, ending today or yesterday, with at least [minimumMinutes] of focus. */
    fun current(sessions: List<FocusSession>, today: LocalDate, minimumMinutes: Int = 1): Int {
        val active = activeDays(sessions, minimumMinutes)
        var day = if (today in active) today else today.minusDays(1)
        var streak = 0
        while (day in active) {
            streak++
            day = day.minusDays(1)
        }
        return streak
    }

    /** The longest run of consecutive active days anywhere in the history. */
    fun longest(sessions: List<FocusSession>, minimumMinutes: Int = 1): Int {
        val days = activeDays(sessions, minimumMinutes).sorted()
        var best = 0
        var run = 0
        var previous: LocalDate? = null
        for (day in days) {
            run = if (previous != null && previous.plusDays(1) == day) run + 1 else 1
            best = maxOf(best, run)
            previous = day
        }
        return best
    }

    private fun activeDays(sessions: List<FocusSession>, minimumMinutes: Int): Set<LocalDate> =
        sessions.groupBy { it.date }
            .filterValues { day -> day.sumOf { it.minutes } >= minimumMinutes }
            .keys
}
