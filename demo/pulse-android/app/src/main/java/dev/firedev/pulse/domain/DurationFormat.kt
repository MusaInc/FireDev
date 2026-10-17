package dev.firedev.pulse.domain

/** Human-readable durations for the summary screen. */
object DurationFormat {
    fun formatMinutes(minutes: Int): String = when {
        minutes <= 0 -> "0 min"
        minutes < 60 -> "$minutes min"
        minutes % 60 == 0 -> "${minutes / 60} h"
        else -> "${minutes / 60} h ${minutes % 60} min"
    }
}
