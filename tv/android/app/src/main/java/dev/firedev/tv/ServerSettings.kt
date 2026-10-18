package dev.firedev.tv

import android.content.Intent
import android.content.SharedPreferences
import org.json.JSONObject

/**
 * Where the FireDev service is. Built-in defaults come from the build (local.properties); an intent can override
 * them without a rebuild, and the override is remembered:
 *   adb shell am start -n dev.firedev.tv/.MainActivity -e server http://192.168.1.20:8130 [-e token TOKEN]
 */
class ServerSettings(private val prefs: SharedPreferences, private val defaultServer: String, private val defaultToken: String) {
    val server: String get() = prefs.getString(KEY_SERVER, null) ?: defaultServer
    val token: String? get() = (prefs.getString(KEY_TOKEN, null) ?: defaultToken).ifBlank { null }

    /** No server set by intent: the build's default (empty for local-first builds). */
    val isDefaultServer: Boolean get() = prefs.getString(KEY_SERVER, null) == null

    /** Returns true if the intent changed the settings. */
    fun applyIntent(intent: Intent?): Boolean {
        val server = intent?.getStringExtra("server")?.trim()?.trimEnd('/')
        val token = intent?.getStringExtra("token")?.trim()
        if (server.isNullOrEmpty() && token == null) return false
        val edit = prefs.edit()
        if (!server.isNullOrEmpty() && isValidServer(server)) edit.putString(KEY_SERVER, server)
        if (token != null) edit.putString(KEY_TOKEN, token)
        edit.apply()
        return true
    }

    fun toJson(version: String): JSONObject = JSONObject().put("server", server).put("token", token ?: JSONObject.NULL).put("version", version)

    companion object {
        private const val KEY_SERVER = "server"
        private const val KEY_TOKEN = "token"

        fun isValidServer(url: String) = Regex("^https?://[^\\s/]+(:\\d+)?(/.*)?$").matches(url)
    }
}
