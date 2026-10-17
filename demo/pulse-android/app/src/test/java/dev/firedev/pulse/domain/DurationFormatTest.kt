package dev.firedev.pulse.domain

import org.junit.Assert.assertEquals
import org.junit.Test

class DurationFormatTest {
    @Test fun zeroMinutes() = assertEquals("0 min", DurationFormat.formatMinutes(0))
    @Test fun negativeIsZero() = assertEquals("0 min", DurationFormat.formatMinutes(-5))
    @Test fun underAnHour() = assertEquals("45 min", DurationFormat.formatMinutes(45))
    @Test fun wholeHours() = assertEquals("2 h", DurationFormat.formatMinutes(120))
    @Test fun hoursAndMinutes() = assertEquals("1 h 5 min", DurationFormat.formatMinutes(65))
}
