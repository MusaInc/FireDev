package dev.firedev.tv.transport

import org.json.JSONObject
import java.security.KeyPair

/**
 * How this TV reaches the user's FireDev machine. Saved on the TV only.
 *   lan  a computer on the same Wi-Fi, TLS with its certificate pinned ([fp])
 *   ssh  the user's own server over SSH, host key pinned ([hostKey]), the TV's key for login
 */
data class Profile(
    val mode: String,
    val name: String,
    val host: String,
    val port: Int, // lan: the API's TLS port; ssh: the SSH port
    val id: String? = null,
    val previewPort: Int = 0, // lan: the previews' TLS port
    val fp: String? = null,
    val user: String? = null,
    val hostKey: String? = null,
    val apiPort: Int = 8130, // ssh: FireDev on the server's 127.0.0.1
    val remotePreviewPort: Int = 8132,
    val token: String? = null,
) {
    fun toJson(): JSONObject = JSONObject().put("mode", mode).put("name", name).put("host", host).put("port", port)
        .put("id", id ?: JSONObject.NULL).put("previewPort", previewPort).put("fp", fp ?: JSONObject.NULL)
        .put("user", user ?: JSONObject.NULL).put("hostKey", hostKey ?: JSONObject.NULL).put("apiPort", apiPort)
        .put("remotePreviewPort", remotePreviewPort).put("token", token ?: JSONObject.NULL)

    /** What the interface may see: no token. */
    fun publicJson(): JSONObject = toJson().apply { remove("token") }

    companion object {
        fun fromJson(j: JSONObject) = Profile(
            mode = j.getString("mode"), name = j.optString("name", "FireDev"), host = j.getString("host"), port = j.getInt("port"),
            id = j.optStr("id"), previewPort = j.optInt("previewPort", 0), fp = j.optStr("fp"), user = j.optStr("user"),
            hostKey = j.optStr("hostKey"), apiPort = j.optInt("apiPort", 8130), remotePreviewPort = j.optInt("remotePreviewPort", 8132),
            token = j.optStr("token"))

        private fun JSONObject.optStr(k: String): String? = if (isNull(k) || !has(k)) null else getString(k)
    }
}

/**
 * The two relays the interface uses: the FireDev API (and its WebSocket) and the preview gateway, each on 127.0.0.1
 * inside the TV, carried over the profile's link. The preview gets its own port, so it's its own origin.
 */
class Transport(val profile: Profile, key: KeyPair?, apiLocal: Int = 38130, previewLocal: Int = 38132) : AutoCloseable {
    val tls: PinnedTls? = if (profile.mode == "lan") PinnedTls(profile.fp) else null
    val ssh: SshLink? = if (profile.mode == "ssh") SshLink(profile.host, profile.port, profile.user ?: "", key!!, profile.hostKey) else null

    val api: Relay = Relay("api", apiLocal) { open(true) }
    val preview: Relay = Relay("preview", previewLocal) { open(false) }

    private fun open(isApi: Boolean): Relay.Upstream = when (profile.mode) {
        "lan" -> Relay.of(tls!!.connect(profile.host, if (isApi) profile.port else profile.previewPort))
        "ssh" -> ssh!!.direct(if (isApi) profile.apiPort else profile.remotePreviewPort)
        else -> throw IllegalStateException("unknown mode ${profile.mode}")
    }

    fun start(): Transport {
        api.start()
        preview.start()
        return this
    }

    val server: String get() = api.url
    val previewBase: String get() = preview.url

    override fun close() {
        api.close()
        preview.close()
        ssh?.close()
    }
}

class NotInstalled(msg: String) : Exception(msg)

/** First connection to a server over SSH: optionally install the TV's key with a one-time password, start FireDev there, get a TV token. */
object SshSetup {
    fun installKey(link: SshLink, publicLine: String, password: String) {
        val q = SshLink.shellQuote(publicLine)
        val (code, out) = link.exec(
            "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && (grep -qxF $q ~/.ssh/authorized_keys || echo $q >> ~/.ssh/authorized_keys)",
            30, password)
        link.close() // the password is used once and never kept; next logins use the key
        if (code != 0) throw Exception("Couldn't add the TV's key: ${out.trim().take(200)}")
    }

    data class Server(val name: String, val apiPort: Int, val previewPort: Int, val token: String)

    fun bootstrap(link: SshLink, tvName: String): Server {
        val (code, out) = link.exec("firedev up --json", 90)
        if (code == 127 || out.contains("command not found") || out.contains("firedev: not found"))
            throw NotInstalled("FireDev isn't installed on ${link.host} yet. On the server, install it (see the FireDev README), then try again.")
        val up = lastJson(out) ?: throw Exception("FireDev didn't start on ${link.host}: ${out.trim().takeLast(300)}")
        val (c2, out2) = link.exec("firedev pair --issue --json --name " + SshLink.shellQuote(tvName), 30)
        val issued = lastJson(out2) ?: throw Exception("FireDev on ${link.host} didn't give this TV a token: ${out2.trim().takeLast(300)} ($c2)")
        return Server(up.optString("name", link.host), up.optInt("port", 8130), up.optInt("preview_port", 8132), issued.getString("token"))
    }

    private fun lastJson(out: String): JSONObject? = out.lines().map { it.trim() }.lastOrNull { it.startsWith("{") && it.endsWith("}") }
        ?.let { runCatching { JSONObject(it) }.getOrNull() }
}
