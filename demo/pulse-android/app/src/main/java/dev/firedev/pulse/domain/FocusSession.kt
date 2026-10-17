package dev.firedev.pulse.domain

import java.time.LocalDate

/** One block of focused work on a given day. */
data class FocusSession(val date: LocalDate, val minutes: Int) {
    init {
        require(minutes >= 0) { "minutes must not be negative" }
    }
}
