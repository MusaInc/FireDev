package dev.firedev.pulse

import android.app.Activity
import android.os.Bundle
import android.widget.TextView
import dev.firedev.pulse.domain.FocusSession
import dev.firedev.pulse.domain.WeeklySummary
import java.time.LocalDate

/** Shows this week's focus summary. The interesting logic lives in the domain package. */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val today = LocalDate.now()
        val sessions = listOf(
            FocusSession(today, 50),
            FocusSession(today.minusDays(1), 35),
            FocusSession(today.minusDays(2), 80),
        )
        val summary = WeeklySummary.of(sessions, today)
        setContentView(TextView(this).apply {
            textSize = 28f
            setPadding(64, 64, 64, 64)
            text = "Pulse ${BuildConfig.VERSION_NAME}\n\n${summary.headline}\n${summary.detail}"
        })
    }
}
