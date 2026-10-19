package dev.firedev.tv

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import org.json.JSONObject
import java.util.Locale

/**
 * The agent's voice on the TV: Android TextToSpeech (Fire OS ships an engine for VoiceView). The WebView has no
 * speech synthesis, so without this everything the agent says would only ever be a caption. One line at a time:
 * the interface queues them and waits for each {type: 'tts', phase: 'done' | 'stopped' | 'error', id}.
 */
class SpeechOutput(context: Context, private val emit: (JSONObject) -> Unit) {
    @Volatile private var ready: Boolean? = null // null until the engine has started
    private val waiting = ArrayList<Pair<String, String>>()
    private lateinit var tts: TextToSpeech

    init {
        tts = TextToSpeech(context.applicationContext) { status -> started(status == TextToSpeech.SUCCESS) }
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String) = event(id, "start")
            override fun onDone(id: String) = event(id, "done")
            @Deprecated("Deprecated in Java") override fun onError(id: String) = event(id, "error")
            override fun onError(id: String, errorCode: Int) = event(id, "error")
            override fun onStop(id: String, interrupted: Boolean) = event(id, "stopped")
        })
    }

    private fun started(ok: Boolean) {
        if (ok) {
            val lang = Locale.getDefault()
            if (tts.isLanguageAvailable(lang) < TextToSpeech.LANG_AVAILABLE) tts.language = Locale.US else tts.language = lang
            tts.setSpeechRate(1.05f)
        }
        val queued = synchronized(waiting) { ready = ok; waiting.toList().also { waiting.clear() } }
        queued.forEach { (id, text) -> speak(id, text) }
    }

    fun speak(id: String, text: String) {
        val ok = synchronized(waiting) { ready ?: run { waiting.clear(); waiting.add(id to text); return } }
        if (!ok || tts.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle(), id) != TextToSpeech.SUCCESS) event(id, "error")
    }

    fun stop() {
        synchronized(waiting) { waiting.forEach { event(it.first, "stopped") }; waiting.clear() }
        if (ready == true) tts.stop()
    }

    fun destroy() = runCatching { tts.stop(); tts.shutdown() }

    private fun event(id: String, phase: String) = emit(JSONObject().put("type", "tts").put("phase", phase).put("id", id))

}
