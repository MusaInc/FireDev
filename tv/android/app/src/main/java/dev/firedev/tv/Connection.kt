package dev.firedev.tv

import android.content.SharedPreferences
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import dev.firedev.tv.transport.HostKeyMismatch
import dev.firedev.tv.transport.NotInstalled
import dev.firedev.tv.transport.PinnedTls
import dev.firedev.tv.transport.Profile
import dev.firedev.tv.transport.SshLink
import dev.firedev.tv.transport.SshSetup
import dev.firedev.tv.transport.Transport
import dev.firedev.tv.transport.TvKey
import dev.firedev.tv.transport.UnknownHostKey
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyPair
import java.security.KeyStore
import java.util.concurrent.Executors
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * This TV's connection to the user's FireDev machine, chosen once and then restored on every start:
 *   lan  a computer on the same Wi-Fi (found by Discovery), TLS with its certificate pinned
 *   ssh  the user's own server, host key pinned, the TV's own key for login
 * The interface always talks to the [Transport]'s 127.0.0.1 relays. Nothing here is hosted by anyone but the user.
 */
class Connection(private val prefs: SharedPreferences, private val emit: (JSONObject) -> Unit) {
    private val work = Executors.newSingleThreadExecutor { Thread(it, "firedev-connect").apply { isDaemon = true } }
    @Volatile var profile: Profile? = prefs.getString(KEY_PROFILE, null)?.let { runCatching { Profile.fromJson(JSONObject(it)) }.getOrNull() }
        private set
    @Volatile var transport: Transport? = null
        private set
    private var pendingSsh: JSONObject? = null
    val key: KeyPair by lazy { TvKey.fromSeed(seed()) }
    val tvName: String = listOf(Build.MANUFACTURER, Build.MODEL).filter { !it.isNullOrBlank() }.distinct().joinToString(" ").ifBlank { "Fire TV" }

    fun start() {
        val p = profile ?: return
        transport = runCatching { Transport(p, if (p.mode == "ssh") key else null).start() }
            .onFailure { Log.e(TAG, "transport", it) }.getOrNull()
        transport?.api?.onError = { e -> emit(state("error", e.message ?: "Can't reach ${p.name}")) }
    }

    /** What the interface needs: where to talk (127.0.0.1 relays) and how this TV is connected. */
    fun config(): JSONObject? {
        val p = profile ?: return null
        val t = transport ?: return null
        return JSONObject().put("server", t.server).put("previewBase", t.previewBase).put("token", p.token ?: JSONObject.NULL)
            .put("connection", p.publicJson())
    }

    fun publicKey(): String = TvKey.publicLine(key, "firedev-tv-" + tvName.replace(Regex("[^A-Za-z0-9]+"), "-").lowercase())

    // --- LAN ---------------------------------------------------------------------------------------------

    fun connectLan(m: JSONObject) = work.execute {
        try {
            emit(state("connecting", "Connecting to ${m.optString("name")}…"))
            val p = Profile("lan", m.optString("name", "Computer"), m.getString("host"), m.getInt("port"), id = m.optString("id"),
                previewPort = m.optInt("previewPort"), fp = m.optString("fp").ifBlank { null })
            replace(p)
            probe("/api/info")
            emit(state("ready", "Connected to ${p.name}"))
        } catch (e: Exception) {
            emit(state("error", e.message ?: "Couldn't connect"))
        }
    }

    /** The pairing code for this TV's claim, from the certificate this TV actually connected to. */
    fun pairCode(claim: String, nonce: String): String? = transport?.tls?.seen?.let { PinnedTls.pairCode(it, claim, nonce) }

    // --- SSH ---------------------------------------------------------------------------------------------

    /** j: {host, port, user, password?, hostKey?}. The password, if any, is used once to install the TV's key. */
    fun connectSsh(j: JSONObject) = work.execute {
        val host = j.getString("host").trim()
        val port = j.optInt("port", 22)
        val user = j.getString("user").trim()
        val password = j.optString("password").ifBlank { null }
        try {
            emit(state("connecting", "Connecting to $host…"))
            val link = SshLink(host, port, user, key, j.optString("hostKey").ifBlank { null })
            try {
                link.ensure(password ?: return@execute connectWithKey(link, j))
            } catch (e: UnknownHostKey) {
                pendingSsh = j
                emit(state("hostkey", "Check the server's fingerprint").put("fingerprint", e.fingerprint).put("host", host))
                return@execute
            }
            emit(state("connecting", "Adding this TV's key on $host…"))
            SshSetup.installKey(link, publicKey(), password)
            connectWithKey(link, j)
        } catch (e: HostKeyMismatch) {
            emit(state("error", e.message ?: "Host key changed").put("hostKeyChanged", true))
        } catch (e: Exception) {
            emit(state("error", e.message ?: "Couldn't connect to $host"))
        }
    }

    private fun connectWithKey(link: SshLink, j: JSONObject) {
        try {
            link.ensure()
        } catch (e: UnknownHostKey) {
            pendingSsh = j
            emit(state("hostkey", "Check the server's fingerprint").put("fingerprint", e.fingerprint).put("host", link.host))
            return
        } catch (e: Exception) {
            emit(state("needs_key", e.message ?: "The server didn't accept the TV's key").put("publicKey", publicKey())
                .put("host", link.host).put("user", link.user))
            return
        }
        emit(state("connecting", "Starting FireDev on ${link.host}…"))
        val server = try { SshSetup.bootstrap(link, tvName) } catch (e: NotInstalled) {
            emit(state("not_installed", e.message ?: "FireDev isn't installed on the server").put("host", link.host)); link.close(); return
        }
        link.close()
        val p = Profile("ssh", server.name, link.host, link.port, user = link.user, hostKey = link.hostKeyPin,
            apiPort = server.apiPort, remotePreviewPort = server.previewPort, token = server.token)
        replace(p)
        probe("/api/health")
        emit(state("ready", "Connected to ${p.name}").put("token", server.token))
    }

    fun trustHostKey(fingerprint: String) {
        val j = pendingSsh ?: return
        pendingSsh = null
        connectSsh(JSONObject(j.toString()).put("hostKey", fingerprint))
    }

    // --- common --------------------------------------------------------------------------------------------

    /** After a LAN pairing, keep the certificate pairing proved (if discovery didn't give one). */
    fun paired() {
        val p = profile ?: return
        val seen = transport?.tls?.seen ?: return
        if (p.mode == "lan" && p.fp == null) save(p.copy(fp = seen))
    }

    fun forget() = work.execute {
        transport?.close()
        transport = null
        profile = null
        prefs.edit().remove(KEY_PROFILE).apply()
        emit(state("forgotten", ""))
    }

    private fun replace(p: Profile) {
        transport?.close()
        val t = Transport(p, if (p.mode == "ssh") key else null).start()
        t.api.onError = { e -> emit(state("error", e.message ?: "Can't reach ${p.name}")) }
        transport = t
        save(p)
    }

    private fun save(p: Profile) {
        profile = p
        prefs.edit().putString(KEY_PROFILE, p.toJson().toString()).apply()
    }

    /** Checks that 127.0.0.1 inside the TV really reaches the machine before telling the interface it's ready. */
    private fun probe(path: String) {
        val c = URL(transport!!.server + path).openConnection() as HttpURLConnection
        c.connectTimeout = 10_000
        c.readTimeout = 20_000
        try {
            if (c.responseCode != 200) throw Exception("FireDev answered ${c.responseCode}")
        } catch (e: java.io.IOException) {
            throw Exception(transport!!.api.lastError ?: e.message ?: "No answer through the connection")
        } finally {
            c.disconnect()
        }
    }

    private fun state(s: String, message: String) = JSONObject().put("type", "connection").put("state", s).put("message", message)

    // --- the TV's SSH key seed, wrapped with an Android Keystore key where the device has one ---------------

    private fun seed(): ByteArray {
        prefs.getString(KEY_SEED, null)?.let { stored -> runCatching { return unwrap(stored) } }
        val s = TvKey.newSeed()
        prefs.edit().putString(KEY_SEED, wrap(s)).apply()
        return s
    }

    private fun keystoreKey(): SecretKey? {
        if (Build.VERSION.SDK_INT < 23) return null
        return runCatching {
            val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            (ks.getKey(KS_ALIAS, null) as SecretKey?) ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
                init(KeyGenParameterSpec.Builder(KS_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
            }.generateKey()
        }.getOrNull()
    }

    private fun wrap(seed: ByteArray): String {
        val k = keystoreKey() ?: return "plain:" + Base64.encodeToString(seed, Base64.NO_WRAP)
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, k) }
        return "ks:" + Base64.encodeToString(c.iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(c.doFinal(seed), Base64.NO_WRAP)
    }

    private fun unwrap(s: String): ByteArray {
        if (s.startsWith("plain:")) return Base64.decode(s.substring(6), Base64.NO_WRAP)
        val (_, iv, data) = s.split(":")
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, keystoreKey(), GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP))) }
        return c.doFinal(Base64.decode(data, Base64.NO_WRAP))
    }

    companion object {
        private const val TAG = "FireDevConnection"
        private const val KEY_PROFILE = "connection"
        private const val KEY_SEED = "tv_key_seed"
        private const val KS_ALIAS = "firedev-tv-key"
    }
}
