package dev.firedev.tv

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import org.json.JSONObject

/**
 * On-device speech recognition, for TVs and Android TV boxes that have a recogniser. Most Fire TV devices don't
 * expose one to apps (their microphone belongs to Alexa), so create() returns null there and FireDev relies on
 * the phone microphone and the Alexa skill instead.
 */
class SpeechInput private constructor(private val recognizer: SpeechRecognizer, private val emit: (JSONObject) -> Unit) {
    private var listening = false

    fun start() {
        if (listening) return
        listening = true
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        recognizer.startListening(intent)
    }

    fun stop() {
        if (listening) recognizer.stopListening()
        listening = false
    }

    fun destroy() = recognizer.destroy()

    private fun send(phase: String, text: String = "") = emit(JSONObject().put("type", "speech").put("phase", phase).put("text", text))

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = send("ready")
        override fun onBeginningOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
        override fun onError(error: Int) {
            listening = false
            send("error", "speech error $error")
        }
        override fun onPartialResults(partialResults: Bundle?) {
            best(partialResults)?.let { send("partial", it) }
        }
        override fun onResults(results: Bundle?) {
            listening = false
            send("final", best(results) ?: "")
        }
    }

    private fun best(b: Bundle?) = b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.takeIf { it.isNotBlank() }

    companion object {
        fun create(context: Context, emit: (JSONObject) -> Unit): SpeechInput? {
            if (!SpeechRecognizer.isRecognitionAvailable(context)) return null
            return runCatching { SpeechRecognizer.createSpeechRecognizer(context) }.getOrNull()?.let { r ->
                SpeechInput(r, emit).also { r.setRecognitionListener(it.listener) }
            }
        }
    }
}
