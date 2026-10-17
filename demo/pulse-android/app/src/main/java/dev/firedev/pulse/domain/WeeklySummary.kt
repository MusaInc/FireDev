package dev.firedev.pulse.domain

import java.time.LocalDate

/** The seven days ending [today], summarised for the home screen. */
data class WeeklySummary(val totalMinutes: Int, val activeDays: Int, val streak: Int) {
    val headline: String
        get() = "${DurationFormat.format(totalMinutes)} this week"

    val detail: String
        get() = when (streak) {
            0 -> "Start a streak today"
            1 -> "1 day streak"
            else -> "$streak day streak"
        } + " · $activeDays of 7 days"

    companion object {
        fun of(sessions: List<FocusSession>, today: LocalDate): WeeklySummary {
            val weekStart = today.minusDays(6)
            val week = sessions.filter { !it.date.isBefore(weekStart) && !it.date.isAfter(today) }
            return WeeklySummary(
                totalMinutes = week.sumOf { it.minutes },
                activeDays = week.filter { it.minutes > 0 }.map { it.date }.toSet().size,
                streak = Streaks.current(sessions, today),
            )
        }
    }
}
